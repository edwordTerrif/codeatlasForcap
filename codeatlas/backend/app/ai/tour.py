"""읽기 투어: 실행이 시작되는 곳부터 핵심 코드를 순서대로 안내한다.

1) 출발점 후보: if __name__ == "__main__" 파일, __main__.py, main/cli/run 같은 함수,
   그리고 "호출을 따라가면 가장 많은 코드에 닿는 함수"(라이브러리처럼 main이 없는 경우의 핵심 흐름)
2) 후보마다 닿는 코드 수(reach)로 점수를 매겨 상위 출발점에서 호출 그래프를 깊이 우선으로 따라간다
3) 단계가 모자라면 중요도(PageRank) 상위 코드로 채운다
"""
from __future__ import annotations

from collections import deque

from ..analyzer.graph import CodeGraph

ENTRY_NAMES = ("main", "cli", "create_app")  # run/serve 같은 이름은 큰 프로젝트에서 너무 흔해서 뺐다


def _next_steps(cg: CodeGraph, nid: str) -> list[str]:
    if cg.kind(nid) == "file":
        return list(cg.node(nid).get("entry_calls") or [])  # 파일은 실행 시작 블록에서 부르는 것만
    return cg.callees(nid)


def reach(cg: CodeGraph, nid: str, cap: int = 200) -> int:
    """호출을 따라가면 닿는 서로 다른 코드 수 (cap에서 멈춤)."""
    seen, q = {nid}, deque([nid])
    while q and len(seen) < cap:
        for c in _next_steps(cg, q.popleft()):
            if c not in seen:
                seen.add(c)
                q.append(c)
    return len(seen) - 1


def find_entry_points(cg: CodeGraph, limit: int = 3) -> list[tuple[str, str]]:
    cands: dict[str, str] = {}
    for f in cg.ids("file"):
        d = cg.node(f)
        if d["name"] == "__main__.py":
            cands[f] = "python -m 으로 실행할 때 시작되는 파일"
        elif d.get("main_guard"):
            cands[f] = "if __name__ == \"__main__\": 으로 직접 실행되는 파일"
    for nid in cg.ids("function"):
        d = cg.node(nid)
        if d["name"] in ENTRY_NAMES and d["parent"].startswith("file:"):
            cands.setdefault(nid, f"이름이 '{d['name']}'인 최상위 함수 (보통 실행 시작점)")
    # 명시적인 진입점(실행 시작 지점)이 어느 정도 흐름으로 이어지면 맨 앞에 둔다 (__main__.py > 실행 파일 > main 함수)
    scored = {}
    for n in cands:
        r = reach(cg, n)
        if cg.kind(n) == "file":  # python -m 패키지 로 실행되는 __main__.py 가 가장 확실한 시작점
            bonus = 30_000 if cg.node(n)["name"] == "__main__.py" else 20_000
        else:
            bonus = 10_000
        scored[n] = bonus + r if r >= 5 else r
    # main이 없는 라이브러리를 위해: 호출을 따라가면 가장 많은 코드에 닿는 함수들
    funcs = cg.ids("function", "method")
    by_reach = sorted(((reach(cg, n), n) for n in funcs), key=lambda x: (-x[0], -cg.node(x[1]).get("rank", 0.0)))
    for r, n in by_reach[:5]:
        if r > 0 and n not in scored:
            scored[n] = r
            cands[n] = f"호출을 따라가면 코드 {r}개에 닿는, 흐름의 중심이 되는 함수"
    ordered = sorted(scored, key=lambda n: (-scored[n], -cg.node(n).get("rank", 0.0)))
    return [(n, cands[n]) for n in ordered[:limit]]


def build_tour(cg: CodeGraph, max_steps: int = 8) -> dict:
    rank = lambda n: cg.node(n).get("rank", 0.0)  # noqa: E731
    order: list[tuple[str, str]] = []
    visited: set[str] = set()

    def visit(nid: str, reason: str, depth: int) -> None:
        if len(order) >= max_steps or nid in visited:
            return
        visited.add(nid)
        order.append((nid, reason))
        if depth >= 4:
            return
        # 다른 코드를 더 부르는(=흐름이 이어지는) 것을 먼저, 그다음 중요도 순
        nexts = sorted(dict.fromkeys(_next_steps(cg, nid)), key=lambda c: (len(cg.callees(c)) > 0, rank(c)), reverse=True)
        for c in nexts[:3]:
            visit(c, f"{cg.node(nid)['qualname']}에서 호출", depth + 1)

    for nid, why in find_entry_points(cg, limit=2):
        visit(nid, why, 0)
    if len(order) < max_steps:  # 모자라면 핵심 코드로 채운다
        for nid in sorted(cg.ids("class", "function", "method"), key=rank, reverse=True):
            if len(order) >= max_steps:
                break
            if nid not in visited:
                visited.add(nid)
                order.append((nid, "많은 코드가 의존하는 핵심 코드 (중요도 상위)"))

    steps = []
    for i, (nid, reason) in enumerate(order, 1):
        d = cg.node(nid)
        steps.append({
            "step": i, "id": nid, "kind": d["kind"], "title": d["qualname"], "path": d.get("path", ""),
            "lineno": d.get("lineno", 1), "summary": d.get("summary", ""), "reason": reason,
        })
    return {"steps": steps}
