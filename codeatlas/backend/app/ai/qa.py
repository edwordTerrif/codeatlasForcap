"""그래프 기반 질의응답 (Graph RAG).

1) 질문을 키워드로 바꿔(LLM이 있으면 영어 식별자 키워드도 추가) BM25로 관련 코드를 찾고
2) 찾은 코드들 사이의 호출 경로를 그래프에서 찾아 '근거 경로'를 만들고
3) 그 코드만 근거로 LLM이 답하게 한다. 답과 함께 지도에 칠할 노드·간선을 돌려준다.
"""
from __future__ import annotations

import re
from itertools import permutations

import networkx as nx

from ..analyzer.graph import CodeGraph
from .llm import BaseLLM, LLMError
from .search import BM25Index, korean_keywords, tokenize

QA_SYSTEM = (
    "너는 처음 보는 코드베이스를 설명해 주는 시니어 개발자다. 주어진 코드 조각만 근거로 한국어로 답한다. "
    "근거로 쓴 코드는 [1], [2]처럼 번호로 표시하고, 코드에서 확인할 수 없는 내용은 모른다고 말한다."
)
KEYWORD_SYSTEM = "You turn questions about a codebase into search keywords. Reply with keywords only."


def _call_graph(cg: CodeGraph) -> nx.DiGraph:
    cached = getattr(cg, "_call_graph", None)
    if cached is None:
        cached = nx.DiGraph()
        cached.add_edges_from((s, t) for s, t, d in cg.G.edges(data=True) if d["kind"] == "calls")
        cg._call_graph = cached  # type: ignore[attr-defined]
    return cached


def expand_query(question: str, llm: BaseLLM) -> list[str]:
    if not llm.available:
        return []
    prompt = (
        f"Question about a Python codebase: {question}\n"
        "List 5-10 English identifiers or words likely to appear in the relevant function/class names, "
        "comma-separated (e.g. login, auth, authenticate, token)."
    )
    try:
        text = llm.complete(KEYWORD_SYSTEM, prompt, max_tokens=80)
    except LLMError:
        return []
    words = [w.strip(" .`'\"") for w in re.split(r"[,\n]", text)]
    return [w for w in words if w and len(w) <= 40][:12]


def find_path(cg: CodeGraph, seeds: list[str], score: dict[str, float] | None = None, max_len: int = 6) -> list[str]:
    """찾은 코드들을 잇는 호출 경로 중 '질문과 관련 깊은 코드를 가장 많이 지나는' 것. 없으면 주변 흐름."""
    G = _call_graph(cg)
    score = score or {}
    best: list[str] = []
    best_score = 0.0
    top = [s for s in seeds[:5] if s in G]
    for a, b in permutations(top, 2):
        try:
            path = nx.shortest_path(G, a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        if len(path) > max_len:
            continue
        value = sum(score.get(n, 0.0) for n in path) + 0.1 * len(path)
        if value > best_score:
            best, best_score = path, value
    if best:
        return best
    if not seeds:
        return []
    s = seeds[0]
    rank = lambda n: cg.node(n).get("rank", 0.0)  # noqa: E731
    callers = sorted(cg.callers(s), key=rank, reverse=True)[:1]
    callees = sorted(cg.callees(s), key=rank, reverse=True)[:1]
    return callers + [s] + callees


def answer_question(cg: CodeGraph, index: BM25Index, question: str, llm: BaseLLM, top_k: int = 8) -> dict:
    question = question.strip()
    keywords = list(dict.fromkeys(korean_keywords(question) + expand_query(question, llm)))
    tokens = tokenize(question + " " + " ".join(keywords))
    hits = index.search(tokens, k=24)
    entity_hits = [(n, s) for n, s in hits if cg.kind(n) in ("function", "method", "class")]
    file_hits = [(n, s) for n, s in hits if cg.kind(n) == "file"]
    seeds = [n for n, _ in entity_hits[:6]] or [n for n, _ in file_hits[:3]]
    if not seeds:
        return {
            "question": question, "keywords": keywords, "llm": llm.available,
            "answer": "관련된 코드를 찾지 못했어요. 함수 이름이나 영어 키워드(예: login, token)를 넣어 다시 질문해 보세요.",
            "nodes": [], "path": [], "edges": [],
        }
    score = dict(hits)
    path = find_path(cg, seeds, score)
    evidence: list[str] = []
    for n in path + seeds:
        if n not in evidence:
            evidence.append(n)
    evidence = evidence[:top_k]

    blocks = []
    for i, nid in enumerate(evidence, 1):
        d = cg.node(nid)
        start, code = cg.source(nid, max_lines=40)
        blocks.append(
            f"[{i}] {d['qualname']} ({d['path']}:{start})\n요약: {d.get('summary', '')}\n```python\n{code}\n```"
        )
    flow = " → ".join(cg.node(n)["qualname"] for n in path)

    answer = ""
    if llm.available:
        prompt = (
            f"질문: {question}\n\n호출 흐름(그래프에서 찾은 경로): {flow or '(없음)'}\n\n관련 코드:\n\n"
            + "\n\n".join(blocks)
            + "\n\n위 코드만 근거로 질문에 답해줘. 처음 보는 사람이 이해하도록 흐름 순서대로 설명하고, 근거 번호를 붙여줘."
        )
        try:
            answer = llm.complete(QA_SYSTEM, prompt, max_tokens=900)
        except LLMError as e:
            answer = f"(AI 답변 생성에 실패해 검색 결과만 보여 드려요: {e})\n\n"
    if not answer or answer.startswith("(AI 답변 생성에 실패"):
        lines = ["질문과 관련 있어 보이는 코드예요 (키워드 검색 + 호출 그래프 결과):"]
        for i, nid in enumerate(evidence, 1):
            d = cg.node(nid)
            lines.append(f"[{i}] {d['qualname']} ({d['path']}:{d.get('lineno', 1)}) — {d.get('summary', '')}")
        if len(path) > 1:
            lines.append(f"\n호출 흐름: {flow}")
        answer = answer + "\n".join(lines)

    return {
        "question": question,
        "keywords": keywords,
        "llm": llm.available,
        "answer": answer,
        "nodes": [
            {
                "ref": i, "id": nid, "kind": cg.kind(nid), "qualname": cg.node(nid)["qualname"],
                "path": cg.node(nid).get("path", ""), "lineno": cg.node(nid).get("lineno", 1),
                "summary": cg.node(nid).get("summary", ""), "score": round(score.get(nid, 0.0), 3),
            }
            for i, nid in enumerate(evidence, 1)
        ],
        "path": path,
        "edges": [[a, b] for a, b in zip(path, path[1:])],
    }
