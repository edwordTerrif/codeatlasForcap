"""파싱 결과로 코드 관계 그래프를 만들고, 중요도(PageRank)와 기능 묶음(커뮤니티)을 계산한다.

노드 종류
  folder   dir:pkg/sub            (최상위 폴더는 "dir:")
  file     file:pkg/sub/mod.py
  class    pkg/sub/mod.py::Class
  function pkg/sub/mod.py::func   (함수 안 함수는 outer.inner)
  method   pkg/sub/mod.py::Class.method
소속 관계는 노드의 parent 속성에, 의존 관계는 간선(kind: calls/imports/inherits)에 담는다.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import networkx as nx

from .parser import Entity, ModuleInfo, read_source

ROOT_ID = "dir:"
ENTITY_KINDS = ("class", "function", "method")

# 이름이 흔해서 "이름만 같으면 같은 함수"로 이어 붙이면 틀리기 쉬운 것들 (휴리스틱에서 제외)
COMMON_NAMES = frozenset("""
get set run update add remove pop append extend items keys values copy close open read write format join split
strip replace send start stop call load save delete create build parse process handle setup reset clear apply
render validate encode decode execute connect emit match search find sort filter map next insert index count
lower upper info debug warning warn error exception log flush wait put post patch head options dump dumps loads
init main exit fit predict transform compile check test register visit
""".split())

Resolved = tuple[str, object]  # ("entity", id) 또는 ("module", ModuleInfo)


class _ModuleIndex:
    """import 문에 적힌 모듈 이름을 저장소 안의 파일로 찾아 준다.

    'src/flask/app.py'처럼 패키지(__init__.py가 있는 폴더) 바깥에 src 폴더가 있으면
    실제 import 이름은 'flask.app'이므로, 경로 그대로의 이름과 import 이름을 모두 등록한다.
    """

    def __init__(self, modules: list[ModuleInfo]):
        pkg_dirs = {m.path.rsplit("/", 1)[0] if "/" in m.path else "" for m in modules if m.is_package}
        self.names: dict[str, list[ModuleInfo]] = defaultdict(list)
        for m in modules:
            parts = m.path[:-3].split("/")
            dirs, stem = parts[:-1], parts[-1]
            i = len(dirs)
            while i > 0 and "/".join(dirs[:i]) in pkg_dirs:
                i -= 1
            importable = ".".join(dirs[i:] + ([] if stem == "__init__" else [stem]))
            for name in {m.module, importable}:
                if name:
                    self.names[name].append(m)

    def find(self, dotted: str) -> ModuleInfo | None:
        cands = self.names.get(dotted)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        exact = [m for m in cands if m.module == dotted]
        return exact[0] if len(exact) == 1 else None


class GraphBuilder:
    def __init__(self, modules: list[ModuleInfo], repo_name: str, heuristic: bool = True):
        self.modules = modules
        self.repo_name = repo_name
        self.heuristic = heuristic
        self.by_file = {m.file_id: m for m in modules}
        self.index = _ModuleIndex(modules)
        self.entities: dict[str, Entity] = {e.id: e for m in modules for e in m.entities}
        self.top: dict[str, dict[str, str]] = defaultdict(dict)  # 파일 → 최상위 이름 → id
        self.nested: dict[str, dict[str, str]] = defaultdict(dict)  # 함수 → 안쪽 함수/클래스 이름 → id
        self.methods: dict[str, dict[str, str]] = defaultdict(dict)  # 클래스 → 메서드 이름 → id
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for e in self.entities.values():
            if e.parent_id == e.file_id:
                self.top[e.file_id][e.name] = e.id  # 같은 이름이 또 정의되면 마지막 것이 이긴다 (파이썬과 같음)
            else:
                parent = self.entities.get(e.parent_id)
                if parent is not None and parent.kind == "class":
                    self.methods[parent.id][e.name] = e.id
                elif parent is not None:
                    self.nested[parent.id][e.name] = e.id
            self.by_name[e.name].append(e.id)
        self._bases: dict[str, list[str]] = {}
        self.stats: Counter = Counter()

    # ------------------------------------------------------------ 이름 해석

    def resolve_symbol(self, mod: ModuleInfo, name: str, depth: int = 0) -> Resolved | None:
        """모듈 mod 안에서 이름 name이 가리키는 것 (최상위 정의, import로 들여온 것, 재수출 따라가기)."""
        if depth > 4:
            return None
        eid = self.top[mod.file_id].get(name)
        if eid:
            return ("entity", eid)
        imp = mod.imports.get(name)
        if imp:
            base, sym = imp
            if sym is None:  # import x / import x.y as name
                target = self.index.find(base)
                return ("module", target) if target else None
            owner = self.index.find(base)
            if owner is not None and owner is not mod:
                r = self.resolve_symbol(owner, sym, depth + 1)
                if r:
                    return r
            sub = self.index.find(f"{base}.{sym}" if base else sym)  # from pkg import submodule
            if sub is not None:
                return ("module", sub)
        for base in mod.star_imports:
            owner = self.index.find(base)
            if owner is not None and owner is not mod:
                eid = self.top[owner.file_id].get(name)
                if eid:
                    return ("entity", eid)
        return None

    def class_bases(self, cid: str) -> list[str]:
        if cid in self._bases:
            return self._bases[cid]
        self._bases[cid] = []  # 순환 상속 방지
        cls = self.entities[cid]
        mod = self.by_file[cls.file_id]
        scope = self.entities.get(cls.parent_id)
        out = []
        for b in cls.bases:
            r = self.resolve_dotted(b, scope, mod)
            if r and r[0] == "entity" and r[1] != cid and self.entities[r[1]].kind == "class":
                out.append(r[1])
        self._bases[cid] = out
        return out

    def lookup_method(self, cid: str, name: str, depth: int = 0) -> str | None:
        if depth > 10:
            return None
        mid = self.methods[cid].get(name)
        if mid:
            return mid
        for base in self.class_bases(cid):
            mid = self.lookup_method(base, name, depth + 1)
            if mid:
                return mid
        return None

    def enclosing_class(self, ent: Entity | None) -> str | None:
        cur = ent
        while cur is not None:
            if cur.kind == "class":
                return cur.id
            cur = self.entities.get(cur.parent_id)
        return None

    def _class_of_type(self, tname: str, scope: Entity | None, depth: int) -> str | None:
        """타입 이름(tname)을 그것이 적힌 스코프에서 해석해 저장소 안의 클래스 id로."""
        mod = self.by_file[scope.file_id] if scope is not None else None
        if mod is None:
            return None
        r = self.resolve_dotted(tname, scope, mod, depth + 1)
        if r is not None and r[0] == "entity" and self.entities[r[1]].kind == "class":
            return r[1]  # type: ignore[return-value]
        return None

    def _local_type(self, scope: Entity | None, name: str) -> tuple[str, Entity] | None:
        cur = scope
        while cur is not None and cur.kind in ("function", "method"):
            if name in cur.var_types:
                return cur.var_types[name], cur
            cur = self.entities.get(cur.parent_id)
        return None

    def _attr_type(self, cid: str, attr: str, depth: int = 0) -> tuple[str, Entity] | None:
        cls = self.entities[cid]
        if attr in cls.var_types:
            return cls.var_types[attr], cls
        if depth < 6:
            for base in self.class_bases(cid):
                found = self._attr_type(base, attr, depth + 1)
                if found:
                    return found
        return None

    def resolve_dotted(self, raw: str, caller: Entity | None, mod: ModuleInfo, depth: int = 0) -> Resolved | None:
        """'foo', 'self.save', 'utils.load', 'pkg.mod.Class.method', 'super().save' 를 정의로 연결한다."""
        if depth > 3:
            return None
        parts = raw.split(".")
        if "()" in raw.replace("super()", ""):  # Foo().run() : 생성자 호출 결과의 메서드
            for i, p in enumerate(parts):
                if p.endswith("()") and p != "super()":
                    r = self.resolve_dotted(".".join(parts[:i] + [p[:-2]]), caller, mod, depth + 1)
                    tail = parts[i + 1:]
                    if (r is None or r[0] != "entity" or self.entities[r[1]].kind != "class"
                            or len(tail) != 1 or tail[0].endswith("()")):
                        return None
                    mid = self.lookup_method(r[1], tail[0])  # type: ignore[arg-type]
                    return ("entity", mid) if mid else None
        head, rest = parts[0], parts[1:]
        if head == "?":
            return None
        if head in ("self", "cls"):
            cid = self.enclosing_class(caller)
            if cid is None:
                return None
            if not rest:
                return ("entity", cid)
            if len(rest) == 1:
                mid = self.lookup_method(cid, rest[0])
                return ("entity", mid) if mid else None
            if len(rest) == 2:  # self.repo.save() : self.repo 의 타입을 알면 연결
                found = self._attr_type(cid, rest[0])
                if found:
                    tcid = self._class_of_type(found[0], found[1], depth)
                    if tcid:
                        mid = self.lookup_method(tcid, rest[1])
                        return ("entity", mid) if mid else None
            return None
        if head == "super()":
            cid = self.enclosing_class(caller)
            if cid is None or len(rest) != 1:
                return None
            for base in self.class_bases(cid):
                mid = self.lookup_method(base, rest[0])
                if mid:
                    return ("entity", mid)
            return None

        cur: Resolved | None = None
        if rest and caller is not None:  # svc = AuthService(); svc.login() 처럼 지역 변수의 타입을 아는 경우
            local = self._local_type(caller, head)
            if local and local[0].split(".")[0] != head:
                tcid = self._class_of_type(local[0], local[1], depth)
                if tcid:
                    cur = ("entity", tcid)
        scope = caller  # 가장 가까운 함수 스코프부터 안쪽 정의를 찾는다
        while cur is None and scope is not None and scope.kind in ("function", "method"):
            nid = self.nested[scope.id].get(head)
            if nid:
                cur = ("entity", nid)
                break
            scope = self.entities.get(scope.parent_id)
        if cur is None:
            cur = self.resolve_symbol(mod, head)
        if cur is None and rest and head in mod.var_types:  # app = Flask(__name__) 다음의 app.run()
            tname = mod.var_types[head]
            if tname.split(".")[0] != head:
                r = self.resolve_dotted(tname, None, mod, depth + 1)
                if r is not None and r[0] == "entity" and self.entities[r[1]].kind == "class":
                    cur = r
        if cur is None:
            # 'import a.b' 인데 a가 파일로 존재하지 않는 경우 (namespace 패키지): 가장 긴 모듈 이름부터 시도
            imp = mod.imports.get(head)
            if imp and imp[1] is None and rest:
                full = imp[0].split(".") + rest
                for i in range(len(full), 0, -1):
                    m = self.index.find(".".join(full[:i]))
                    if m is not None:
                        cur, rest = ("module", m), full[i:]
                        break
            if cur is None:
                return None
        for attr in rest:
            if cur[0] == "module":
                m = cur[1]
                sub = self.index.find(f"{m.module}.{attr}")
                if sub is not None:
                    cur = ("module", sub)
                    continue
                nxt = self.resolve_symbol(m, attr)
                if nxt is None:
                    return None
                cur = nxt
                continue
            ent = self.entities[cur[1]]
            if ent.kind == "class":
                mid = self.lookup_method(ent.id, attr)
                if mid:
                    cur = ("entity", mid)
                    continue
            return None
        return cur

    def resolve_call(self, raw: str, caller: Entity | None, mod: ModuleInfo) -> tuple[str | None, bool]:
        """호출 이름 → (대상 id, 휴리스틱 여부)."""
        r = self.resolve_dotted(raw, caller, mod)
        if r is not None and r[0] == "entity":
            return r[1], False  # type: ignore[return-value]
        if self.heuristic and "." in raw and r is None:
            # obj.method() 처럼 obj의 타입을 모를 때: 저장소 전체에서 그 이름의 함수가 딱 하나면 연결
            last = raw.rsplit(".", 1)[1]
            if last not in COMMON_NAMES and not last.startswith("__"):
                cands = [c for c in self.by_name.get(last, ()) if self.entities[c].kind in ("function", "method")]
                if len(cands) == 1:
                    return cands[0], True
        return None, False

    # ------------------------------------------------------------ 그래프 만들기

    def build(self) -> nx.DiGraph:
        G = nx.DiGraph(name=self.repo_name)
        G.add_node(ROOT_ID, kind="folder", name=self.repo_name, qualname=self.repo_name, path="", parent=None)
        for m in self.modules:
            parts = m.path.split("/")
            parent = ROOT_ID
            for i in range(1, len(parts)):
                fid = "dir:" + "/".join(parts[:i])
                if fid not in G:
                    G.add_node(fid, kind="folder", name=parts[i - 1], qualname="/".join(parts[:i]),
                               path="/".join(parts[:i]), parent=parent)
                parent = fid
            G.add_node(m.file_id, kind="file", name=parts[-1], qualname=m.module or parts[-1], path=m.path,
                       parent=parent, lineno=1, end_lineno=max(1, m.lines), docstring=m.docstring,
                       error=m.error, main_guard=m.has_main_guard)
        for e in self.entities.values():
            G.add_node(e.id, kind=e.kind, name=e.name, qualname=e.qualname, path=self.by_file[e.file_id].path,
                       parent=e.parent_id, lineno=e.lineno, end_lineno=e.end_lineno, docstring=e.docstring,
                       signature=e.signature, decorators=[d for d in e.decorators if d], is_async=e.is_async)

        def add_edge(s: str, t: str, kind: str, w: float = 1.0, heuristic: bool = False) -> None:
            if s == t:
                return
            if G.has_edge(s, t):
                d = G[s][t]
                d["weight"] += w
                if kind == "calls":
                    d["kind"] = "calls"
                d["heuristic"] = d["heuristic"] and heuristic
            else:
                G.add_edge(s, t, kind=kind, weight=w, heuristic=heuristic)

        for m in self.modules:
            for e in m.entities:
                for raw in e.calls:
                    target, guessed = self.resolve_call(raw, e, m)
                    if target:
                        add_edge(e.id, target, "calls", 0.5 if guessed else 1.0, guessed)
                        self.stats["calls_heuristic" if guessed else "calls_resolved"] += 1
                    else:
                        self.stats["calls_external"] += 1
                if e.kind == "class":
                    for base in self.class_bases(e.id):
                        add_edge(e.id, base, "inherits")
            for raw in m.toplevel_calls:
                target, guessed = self.resolve_call(raw, None, m)
                if target:
                    add_edge(m.file_id, target, "calls", 0.5 if guessed else 1.0, guessed)
            for name in set(m.import_modules):
                target = self.index.find(name)
                if target is not None and target is not m:
                    add_edge(m.file_id, target.file_id, "imports")
            if m.has_main_guard:
                entry = []
                for raw in m.main_calls:
                    target, _ = self.resolve_call(raw, None, m)
                    if target and target not in entry:
                        entry.append(target)
                G.nodes[m.file_id]["entry_calls"] = entry

        compute_importance(G)
        compute_communities(G)
        kinds = Counter(d["kind"] for _, d in G.nodes(data=True))
        edge_kinds = Counter(d["kind"] for _, _, d in G.edges(data=True))
        G.graph["stats"] = {
            "folders": kinds["folder"], "files": kinds["file"], "classes": kinds["class"],
            "functions": kinds["function"] + kinds["method"],
            "edges": dict(edge_kinds),
            "calls_resolved": self.stats["calls_resolved"],
            "calls_heuristic": self.stats["calls_heuristic"],
            "calls_external": self.stats["calls_external"],
            "parse_errors": sum(1 for m in self.modules if m.error),
        }
        return G


def build_graph(modules: list[ModuleInfo], repo_name: str, heuristic: bool = True) -> nx.DiGraph:
    return GraphBuilder(modules, repo_name, heuristic=heuristic).build()


# ---------------------------------------------------------------- 중요도 / 묶음

def pagerank(nodes: list[str], edges: Iterable[tuple[str, str, float]], alpha: float = 0.85,
             max_iter: int = 100, tol: float = 1.0e-10) -> dict[str, float]:
    """가중치 PageRank (거듭제곱법). scipy 없이 동작하도록 직접 구현."""
    n = len(nodes)
    if n == 0:
        return {}
    idx = {v: i for i, v in enumerate(nodes)}
    out_w = [0.0] * n
    adj: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for s, t, w in edges:
        if s in idx and t in idx and s != t:
            adj[idx[s]].append((idx[t], w))
            out_w[idx[s]] += w
    pr = [1.0 / n] * n
    for _ in range(max_iter):
        dangling = sum(pr[i] for i in range(n) if out_w[i] == 0)
        base = (1.0 - alpha) / n + alpha * dangling / n
        new = [base] * n
        for i in range(n):
            if out_w[i]:
                share = alpha * pr[i] / out_w[i]
                for j, w in adj[i]:
                    new[j] += share * w
        err = sum(abs(a - b) for a, b in zip(new, pr))
        pr = new
        if err < n * tol:
            break
    return dict(zip(nodes, pr))


def _log_normalize(values: dict[str, float]) -> dict[str, float]:
    """PageRank 값은 한쪽으로 쏠려 있어서 로그로 펴서 0~1로 맞춘다 (노드 크기용)."""
    if not values:
        return {}
    logs = {k: math.log(max(v, 1e-12)) for k, v in values.items()}
    lo, hi = min(logs.values()), max(logs.values())
    if hi - lo < 1e-9:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in logs.items()}


def _file_of(G: nx.DiGraph, nid: str) -> str | None:
    cur = nid
    while cur is not None:
        if G.nodes[cur]["kind"] == "file":
            return cur
        cur = G.nodes[cur].get("parent")
    return None


def compute_importance(G: nx.DiGraph) -> None:
    """rank(원래 PageRank 값)와 importance(0~1, 화면 표시용)를 노드에 기록한다."""
    entity_nodes = [n for n, d in G.nodes(data=True) if d["kind"] in ENTITY_KINDS or d["kind"] == "file"]
    dep_edges = [(s, t, d["weight"]) for s, t, d in G.edges(data=True) if d["kind"] in ("calls", "inherits")]
    pr = pagerank(entity_nodes, dep_edges)
    ent_pr = {n: v for n, v in pr.items() if G.nodes[n]["kind"] in ENTITY_KINDS}

    # 파일 단위: import + 파일 사이 호출을 합쳐서 PageRank
    files = [n for n, d in G.nodes(data=True) if d["kind"] == "file"]
    file_w: Counter = Counter()
    for s, t, d in G.edges(data=True):
        fs, ft = _file_of(G, s), _file_of(G, t)
        if fs and ft and fs != ft:
            file_w[(fs, ft)] += d["weight"] if d["kind"] != "imports" else 1.0
    file_pr = pagerank(files, [(s, t, w) for (s, t), w in file_w.items()])

    # 폴더 단위: 안에 든 파일 PageRank의 합
    folder_pr: dict[str, float] = defaultdict(float)
    for f, v in file_pr.items():
        cur = G.nodes[f].get("parent")
        while cur is not None:
            folder_pr[cur] += v
            cur = G.nodes[cur].get("parent")

    for group in (ent_pr, file_pr, dict(folder_pr)):
        norm = _log_normalize(group)
        for n, v in group.items():
            G.nodes[n]["rank"] = v
            G.nodes[n]["importance"] = round(norm[n], 4)
    for n, d in G.nodes(data=True):
        d.setdefault("rank", 0.0)
        d.setdefault("importance", 0.0)


def compute_communities(G: nx.DiGraph) -> None:
    """파일들을 서로 많이 부르는 것끼리 묶는다 (Louvain). 함수·클래스는 파일의 묶음을 따른다."""
    files = [n for n, d in G.nodes(data=True) if d["kind"] == "file"]
    F = nx.Graph()
    F.add_nodes_from(files)
    for s, t, d in G.edges(data=True):
        fs, ft = _file_of(G, s), _file_of(G, t)
        if fs and ft and fs != ft:
            w = F[fs][ft]["weight"] if F.has_edge(fs, ft) else 0.0
            F.add_edge(fs, ft, weight=w + d["weight"])
    try:
        comms = nx.community.louvain_communities(F, weight="weight", seed=42)
    except Exception:
        comms = list(nx.community.greedy_modularity_communities(F, weight="weight"))
    comm_of: dict[str, int] = {}
    cid = 0
    for c in sorted(comms, key=lambda c: (-len(c), min(c))):
        if len(c) < 2:  # 혼자 떨어진 파일은 묶음 없음(-1)
            for f in c:
                comm_of[f] = -1
            continue
        for f in c:
            comm_of[f] = cid
        cid += 1
    for n, d in G.nodes(data=True):
        f = _file_of(G, n) if d["kind"] != "folder" else None
        d["community"] = comm_of.get(f, -1) if f else -1
    G.graph["communities"] = cid


# ---------------------------------------------------------------- 저장/불러오기와 조회 도우미

def graph_to_json(G: nx.DiGraph) -> dict:
    return {
        "graph": dict(G.graph),
        "nodes": [dict(id=n, **d) for n, d in G.nodes(data=True)],
        "edges": [dict(source=s, target=t, **d) for s, t, d in G.edges(data=True)],
    }


def graph_from_json(data: dict) -> nx.DiGraph:
    G = nx.DiGraph()
    G.graph.update(data.get("graph", {}))
    for n in data["nodes"]:
        attrs = dict(n)
        G.add_node(attrs.pop("id"), **attrs)
    for e in data["edges"]:
        attrs = dict(e)
        G.add_edge(attrs.pop("source"), attrs.pop("target"), **attrs)
    return G


class CodeGraph:
    """분석된 저장소 하나: 그래프 + 원본 코드 위치. AI 기능과 API가 이 객체를 쓴다."""

    def __init__(self, G: nx.DiGraph, root: Path):
        self.G = G
        self.root = Path(root)
        self.children: dict[str, list[str]] = defaultdict(list)
        for n, d in G.nodes(data=True):
            if d.get("parent") is not None:
                self.children[d["parent"]].append(n)
        for kids in self.children.values():
            kids.sort(key=lambda c: (-G.nodes[c].get("rank", 0.0), G.nodes[c].get("lineno", 0)))

    def node(self, nid: str) -> dict:
        return self.G.nodes[nid]

    def __contains__(self, nid: str) -> bool:
        return nid in self.G

    def kind(self, nid: str) -> str:
        return self.G.nodes[nid]["kind"]

    def ids(self, *kinds: str) -> list[str]:
        return [n for n, d in self.G.nodes(data=True) if d["kind"] in kinds]

    def callees(self, nid: str) -> list[str]:
        return [t for t in self.G.successors(nid) if self.G[nid][t]["kind"] == "calls"]

    def callers(self, nid: str) -> list[str]:
        return [s for s in self.G.predecessors(nid) if self.G[s][nid]["kind"] == "calls"]

    def ancestors(self, nid: str) -> list[str]:
        out, cur = [], self.G.nodes[nid].get("parent")
        while cur is not None:
            out.append(cur)
            cur = self.G.nodes[cur].get("parent")
        return out

    def source(self, nid: str, max_lines: int = 300) -> tuple[int, str]:
        """노드에 해당하는 코드 조각 (시작 줄 번호, 코드)."""
        d = self.G.nodes[nid]
        if d["kind"] == "folder" or not d.get("path"):
            return 0, ""
        lines = _read_lines(str(self.root / d["path"]))
        start = max(1, int(d.get("lineno") or 1))
        end = min(len(lines), int(d.get("end_lineno") or start), start + max_lines - 1)
        return start, "\n".join(lines[start - 1:end])


@lru_cache(maxsize=256)
def _read_lines(path: str) -> list[str]:
    try:
        return read_source(Path(path)).splitlines()
    except OSError:
        return []
