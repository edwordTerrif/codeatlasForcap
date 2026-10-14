"""CodeAtlas API 서버 (FastAPI).

실행:  (backend 폴더에서)  python -m uvicorn app.main:app --reload
문서:  http://localhost:8000/docs  (자동 생성 API 문서)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .ai.llm import get_llm
from .ai.qa import answer_question
from .ai.tour import build_tour
from .config import ROOT_DIR, settings
from .store import Project, ProjectNotFound, ProjectNotReady, ProjectStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

llm = get_llm(settings)
store = ProjectStore(settings, llm)

app = FastAPI(
    title="CodeAtlas API",
    version=__version__,
    description="처음 보는 코드 지도 — AI 기반 소스코드 구조 시각화 및 이해 지원 시스템",
)
app.add_middleware(
    CORSMiddleware,  # 개발 중 Vite 서버(5173)에서 부를 수 있도록
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NODE_FIELDS = ("kind", "name", "qualname", "parent", "path", "lineno", "end_lineno", "importance",
               "community", "summary", "summary_source", "error", "main_guard")


class AnalyzeRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=500, description="GitHub 주소 또는 로컬 폴더 경로")
    include_tests: bool = Field(False, description="tests/docs/examples 폴더도 분석할지")
    summarize: bool = Field(True, description="LLM으로 요약을 만들지 (LLM 설정이 있을 때만)")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


def _project(pid: str) -> Project:
    try:
        return store.get(pid)
    except ProjectNotFound:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다. 먼저 분석을 실행하세요.")
    except ProjectNotReady:
        raise HTTPException(409, "아직 분석 중입니다. 잠시 후 다시 시도하세요.")


def _brief(p: Project, nid: str) -> dict:
    d = p.cg.node(nid)
    return {"id": nid, "kind": d["kind"], "name": d["name"], "qualname": d.get("qualname", d["name"]),
            "path": d.get("path", ""), "lineno": d.get("lineno"), "summary": d.get("summary", "")}


def _sorted(p: Project, ids) -> list[str]:
    return sorted(ids, key=lambda n: -p.cg.node(n).get("rank", 0.0))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "llm": llm.describe()}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return store.list()


@app.post("/api/projects")
def create_project(req: AnalyzeRequest) -> dict:
    pid = store.start(req.source, include_tests=req.include_tests, summarize=req.summarize)
    return {"project_id": pid, "status": store.status(pid)}


@app.get("/api/projects/{pid}/status")
def project_status(pid: str) -> dict:
    try:
        return store.status(pid)
    except ProjectNotFound:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")


@app.get("/api/projects/{pid}")
def project_meta(pid: str) -> dict:
    return _project(pid).meta


@app.get("/api/projects/{pid}/graph")
def project_graph(pid: str) -> dict:
    p = _project(pid)
    G = p.cg.G
    nodes = [{"id": n, **{k: d.get(k) for k in NODE_FIELDS}} for n, d in G.nodes(data=True)]
    edges = [{"source": s, "target": t, "kind": d["kind"], "weight": d["weight"], "heuristic": d["heuristic"]}
             for s, t, d in G.edges(data=True)]
    return {"project": p.meta, "root": "dir:", "nodes": nodes, "edges": edges}


@app.get("/api/projects/{pid}/node")
def node_detail(pid: str, id: str = Query(..., description="노드 id")) -> dict:
    p = _project(pid)
    cg = p.cg
    if id not in cg:
        raise HTTPException(404, "노드를 찾을 수 없습니다.")
    d = cg.node(id)
    max_lines = 400 if d["kind"] == "file" else 300
    start, code = cg.source(id, max_lines=max_lines)
    truncated = d["kind"] != "folder" and (d.get("end_lineno") or 0) - start + 1 > max_lines

    def related(kind: str, outgoing: bool) -> list[dict]:
        nbrs = cg.G.successors(id) if outgoing else cg.G.predecessors(id)
        ids = [n for n in nbrs if (cg.G[id][n] if outgoing else cg.G[n][id])["kind"] == kind]
        return [_brief(p, n) for n in _sorted(p, ids)[:50]]

    return {
        "node": {"id": id, **{k: v for k, v in d.items() if k not in ("rank",)}},
        "code": code, "code_start": start, "code_truncated": truncated,
        "callers": related("calls", False), "callees": related("calls", True),
        "imports": related("imports", True), "imported_by": related("imports", False),
        "bases": related("inherits", True), "subclasses": related("inherits", False),
        "children": [_brief(p, c) for c in cg.children.get(id, [])[:200]],
        "ancestors": cg.ancestors(id),
    }


@app.post("/api/projects/{pid}/ask")
def ask(pid: str, req: AskRequest) -> dict:
    p = _project(pid)
    return answer_question(p.cg, p.index, req.question, llm)


@app.get("/api/projects/{pid}/tour")
def tour(pid: str, steps: int = Query(8, ge=3, le=20)) -> dict:
    return build_tour(_project(pid).cg, max_steps=steps)


# 빌드된 화면(frontend/dist)이 있으면 같은 주소에서 함께 제공한다 → python만으로 전체 실행 가능
DIST_DIR = ROOT_DIR / "frontend" / "dist"
if (DIST_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse({
            "message": "CodeAtlas API 서버가 실행 중입니다. 화면은 frontend 폴더에서 npm run dev 로 띄우세요.",
            "docs": "/docs",
        })
