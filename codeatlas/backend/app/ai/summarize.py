"""계층적 요약: 함수·메서드·클래스 → 파일 → 폴더 순으로 아래에서 위로 요약을 쌓는다.

- 비용을 줄이려고 중요도(PageRank) 상위 항목만 LLM으로 요약하고, 나머지는 docstring/구조로 자동 요약한다.
- 한 번 만든 요약은 summaries.json에 저장해 두고, 코드가 바뀌지 않았으면 다시 부르지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from ..analyzer.graph import CodeGraph
from .llm import BaseLLM, LLMError

PROMPT_VERSION = "v1"
SYSTEM_PROMPT = (
    "너는 처음 보는 코드베이스를 신입 개발자에게 설명해 주는 시니어 개발자다. "
    "한국어로 짧고 정확하게 쓰고, 코드에 없는 내용은 지어내지 않는다. 머리말 없이 요약 문장만 쓴다."
)
KIND_KO = {"function": "함수", "method": "메서드", "class": "클래스", "file": "파일", "folder": "폴더"}

Progress = Callable[[int, int, str], None]


def first_sentence(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text, maxsplit=1)[0].replace("\n", " ").strip()
    m = re.search(r"(.+?[.!?。])(\s|$)", para)
    s = m.group(1) if m else para
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _clean(text: str, limit: int = 320) -> str:
    text = re.sub(r"^[\s\-*#>\"'`]+", "", (text or "").strip())
    text = re.sub(r"\s+", " ", text).strip().strip("\"'`")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fallback_summary(cg: CodeGraph, nid: str) -> tuple[str, str]:
    """LLM 없이 만드는 요약 → (문장, 출처: docstring|auto)."""
    d = cg.node(nid)
    kind = d["kind"]
    doc = first_sentence(d.get("docstring", ""))
    if doc:
        return doc, "docstring"
    kids = cg.children.get(nid, [])
    if kind in ("function", "method"):
        n_out, n_in = len(cg.callees(nid)), len(cg.callers(nid))
        return f"{KIND_KO[kind]} {d['name']} — 저장소 안의 다른 코드 {n_out}개를 호출하고, {n_in}곳에서 호출됩니다.", "auto"
    if kind == "class":
        methods = [cg.node(c)["name"] for c in kids if cg.kind(c) == "method"]
        extra = f" (주요 메서드: {', '.join(methods[:4])})" if methods else ""
        return f"클래스 {d['name']} — 메서드 {len(methods)}개{extra}.", "auto"
    if kind == "file":
        if d.get("error"):
            return f"분석하지 못한 파일입니다: {d['error']}", "auto"
        n_func = sum(1 for c in kids if cg.kind(c) == "function")
        n_cls = sum(1 for c in kids if cg.kind(c) == "class")
        if not kids:
            return "함수나 클래스 정의 없이 설정·import 등만 있는 파일입니다.", "auto"
        top = ", ".join(cg.node(c)["name"] for c in kids[:3])
        return f"함수 {n_func}개, 클래스 {n_cls}개가 있는 파일입니다. 주요 항목: {top}.", "auto"
    files = [c for c in kids if cg.kind(c) == "file"]
    sub = [c for c in kids if cg.kind(c) == "folder"]
    top = ", ".join(cg.node(c)["name"] for c in kids[:3])
    return f"파이썬 파일 {len(files)}개, 하위 폴더 {len(sub)}개가 있는 폴더입니다. 주요 항목: {top}.", "auto"


def _entity_prompt(cg: CodeGraph, nid: str) -> str:
    d = cg.node(nid)
    kind = KIND_KO[d["kind"]]
    if d["kind"] == "class":
        lines = [d.get("signature", f"class {d['name']}")]
        if d.get("docstring"):
            lines.append(f'    """{first_sentence(d["docstring"], 300)}"""')
        for c in cg.children.get(nid, [])[:25]:
            cd = cg.node(c)
            if cd["kind"] == "method":
                doc = first_sentence(cd.get("docstring", ""), 80)
                lines.append(f"    {cd.get('signature', cd['name'])}" + (f"  # {doc}" if doc else ""))
        code = "\n".join(lines)
    else:
        _, code = cg.source(nid, max_lines=60)
    return (
        f"아래 파이썬 {kind} `{d['qualname']}`({d['path']}:{d.get('lineno', 1)})가 하는 일을 1~2문장으로 요약해줘. "
        f"이름을 그대로 반복하지 말고 역할과 핵심 동작 위주로 써줘.\n\n```python\n{code}\n```"
    )


def _group_prompt(cg: CodeGraph, nid: str, summaries: dict[str, tuple[str, str]]) -> str:
    d = cg.node(nid)
    kind = KIND_KO[d["kind"]]
    items = []
    for c in cg.children.get(nid, [])[:20]:
        cd = cg.node(c)
        items.append(f"- {KIND_KO.get(cd['kind'], cd['kind'])} {cd['name']}: {summaries.get(c, ('',))[0]}")
    doc = first_sentence(d.get("docstring", ""), 300) or "(없음)"
    where = d.get("path") or d.get("name")
    return (
        f"파이썬 프로젝트의 {kind} `{where}`가 맡은 역할을 2문장 이내로 요약해줘.\n"
        f"설명 주석: {doc}\n포함된 항목과 각 요약:\n" + ("\n".join(items) if items else "(없음)")
    )


def summarize_all(
    cg: CodeGraph,
    llm: BaseLLM,
    cache_file: Path,
    max_entities: int = 120,
    max_files: int = 60,
    workers: int = 4,
    progress: Progress | None = None,
) -> dict:
    """모든 노드에 summary/summary_source 속성을 채우고, 요약 통계를 돌려준다."""
    try:
        cache: dict = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    summaries: dict[str, tuple[str, str]] = {}
    lock = threading.Lock()
    state = {"done": 0, "llm_calls": 0, "cache_hits": 0, "failures": 0, "consecutive_failures": 0, "error": ""}
    use_llm = llm.available

    by_rank = lambda ids: sorted(ids, key=lambda n: -cg.node(n).get("rank", 0.0))  # noqa: E731
    entities = by_rank(cg.ids("class", "function", "method"))
    files = by_rank(cg.ids("file"))
    folders = sorted(cg.ids("folder"), key=lambda n: -len(cg.node(n).get("path", "").split("/")))  # 깊은 폴더부터

    llm_entities = set(entities[:max_entities]) if use_llm else set()
    llm_files = set(files[:max_files]) if use_llm else set()
    llm_folders = set(folders) if use_llm and len(folders) <= 40 else set()
    total = len(llm_entities) + len(llm_files) + len(llm_folders)

    def tick(message: str) -> None:
        with lock:
            state["done"] += 1
            done = state["done"]
        if progress and total:
            progress(done, total, message)

    def run_llm(nid: str, prompt: str, max_tokens: int) -> str | None:
        """캐시를 확인하고 LLM을 부른다. 실패하면 None (연속 3번 실패하면 LLM을 끈다)."""
        nonlocal use_llm
        key = hashlib.sha1(f"{PROMPT_VERSION}|{llm.tag}|{prompt}".encode("utf-8")).hexdigest()
        hit = cache.get(nid)
        if hit and hit.get("key") == key:
            with lock:
                state["cache_hits"] += 1
            return hit["text"]
        if not use_llm:
            return None
        try:
            text = _clean(llm.complete(SYSTEM_PROMPT, prompt, max_tokens=max_tokens))
        except LLMError as e:
            with lock:
                state["failures"] += 1
                state["consecutive_failures"] += 1
                state["error"] = str(e)[:300]
                if state["consecutive_failures"] >= 3:
                    use_llm = False
            return None
        with lock:
            state["llm_calls"] += 1
            state["consecutive_failures"] = 0
            cache[nid] = {"key": key, "text": text}
        return text if text else None

    # 1) 함수·메서드·클래스: 코드 자체를 보고 요약 (병렬)
    def do_entity(nid: str) -> None:
        text = run_llm(nid, _entity_prompt(cg, nid), 220) if nid in llm_entities else None
        summaries[nid] = (text, "llm") if text else fallback_summary(cg, nid)
        if nid in llm_entities:
            tick("함수·클래스 요약 중")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(do_entity, entities))

    # 2) 파일: 안에 든 함수·클래스 요약을 모아서 요약
    def do_file(nid: str) -> None:
        text = run_llm(nid, _group_prompt(cg, nid, summaries), 260) if nid in llm_files and cg.children.get(nid) else None
        summaries[nid] = (text, "llm") if text else fallback_summary(cg, nid)
        if nid in llm_files:
            tick("파일 요약 중")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(do_file, files))

    # 3) 폴더: 깊은 폴더부터 차례로 (하위 폴더 요약이 먼저 있어야 하므로 순서대로)
    for nid in folders:
        text = run_llm(nid, _group_prompt(cg, nid, summaries), 260) if nid in llm_folders else None
        summaries[nid] = (text, "llm") if text else fallback_summary(cg, nid)
        if nid in llm_folders:
            tick("폴더 요약 중")

    for nid, (text, source) in summaries.items():
        cg.node(nid)["summary"] = text
        cg.node(nid)["summary_source"] = source
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    by_source: dict[str, int] = {}
    for _, source in summaries.values():
        by_source[source] = by_source.get(source, 0) + 1
    return {
        "llm": llm.tag,
        "by_source": by_source,
        "llm_calls": state["llm_calls"],
        "cache_hits": state["cache_hits"],
        "failures": state["failures"],
        "error": state["error"],
    }
