"""분석 작업 실행과 결과 저장.

data/
  repos/<owner>__<repo>/        내려받은 저장소
  projects/<project_id>/
    meta.json                   분석 정보 (원본 위치, 통계, 요약 정보)
    graph.json                  코드 그래프 (노드·간선·요약)
    summaries.json              LLM 요약 캐시
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

from .ai.llm import BaseLLM, NoLLM
from .ai.search import BM25Index, build_index
from .ai.summarize import summarize_all
from .analyzer.fetch import fetch_source, parse_github_url
from .analyzer.graph import CodeGraph, build_graph, graph_from_json, graph_to_json
from .analyzer.parser import parse_repository
from .config import Settings

log = logging.getLogger("codeatlas")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
STAGE_MESSAGES = {
    "queued": "대기 중", "fetching": "저장소를 가져오는 중", "parsing": "코드를 분석하는 중",
    "graph": "관계 그래프를 만드는 중", "summarizing": "요약을 만드는 중", "indexing": "검색 색인을 만드는 중",
    "done": "완료", "error": "오류",
}


class ProjectNotFound(Exception):
    pass


class ProjectNotReady(Exception):
    pass


def make_project_id(source: str) -> str:
    gh = parse_github_url(source)
    if gh:
        owner, repo, branch = gh
        base = f"{owner}-{repo}" + (f"-{branch}" if branch else "")
    else:
        p = Path(source).expanduser().resolve()
        base = f"{p.name}-{hashlib.sha1(str(p).encode()).hexdigest()[:6]}"
    pid = re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-._")
    return pid[:80] or "project"


class Project:
    def __init__(self, pid: str, meta: dict, cg: CodeGraph, index: BM25Index):
        self.id, self.meta, self.cg, self.index = pid, meta, cg, index


class ProjectStore:
    def __init__(self, settings: Settings, llm: BaseLLM):
        self.settings = settings
        self.llm = llm
        self.repos_dir = settings.data_dir / "repos"
        self.projects_dir = settings.data_dir / "projects"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self._cache: dict[str, Project] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 작업 실행

    def start(self, source: str, include_tests: bool = False, summarize: bool = True) -> str:
        source = source.strip()
        pid = make_project_id(source)
        with self._lock:
            job = self._jobs.get(pid)
            if job and job["state"] not in ("done", "error"):
                return pid  # 이미 분석 중
            self._jobs[pid] = {"state": "queued", "progress": 0.0, "message": STAGE_MESSAGES["queued"],
                               "source": source, "started_at": time.time()}
            self._cache.pop(pid, None)
        threading.Thread(target=self._run, args=(pid, source, include_tests, summarize), daemon=True).start()
        return pid

    def _update(self, pid: str, state: str | None = None, progress: float | None = None, message: str | None = None) -> None:
        with self._lock:
            job = self._jobs.setdefault(pid, {})
            if state:
                job["state"] = state
                job["message"] = STAGE_MESSAGES.get(state, state)
            if progress is not None:
                job["progress"] = round(min(1.0, max(0.0, progress)), 3)
            if message:
                job["message"] = message

    def _run(self, pid: str, source: str, include_tests: bool, summarize: bool) -> None:
        s = self.settings
        try:
            self._update(pid, "fetching", 0.02)
            root, name = fetch_source(source, self.repos_dir)

            self._update(pid, "parsing", 0.08)
            modules = parse_repository(
                root, include_tests=include_tests, max_files=s.max_files, max_bytes=s.max_file_bytes,
                progress=lambda d, t: self._update(pid, progress=0.08 + 0.3 * d / max(1, t),
                                                   message=f"코드를 분석하는 중 ({d}/{t} 파일)"),
            )
            if not modules:
                raise ValueError("파이썬(.py) 파일을 찾지 못했습니다.")

            self._update(pid, "graph", 0.4)
            cg = CodeGraph(build_graph(modules, name), root)

            pdir = self.projects_dir / pid
            pdir.mkdir(parents=True, exist_ok=True)
            llm = self.llm if summarize else NoLLM()
            self._update(pid, "summarizing", 0.45, "요약을 만드는 중" + (" (AI)" if llm.available else ""))
            summary_info = summarize_all(
                cg, llm, pdir / "summaries.json",
                max_entities=s.summary_max_entities, max_files=s.summary_max_files, workers=s.summary_workers,
                progress=lambda d, t, msg: self._update(pid, progress=0.45 + 0.5 * d / max(1, t),
                                                        message=f"{msg} ({d}/{t})"),
            )

            self._update(pid, "indexing", 0.96)
            index = build_index(cg)
            meta = {
                "id": pid, "name": name, "source": source, "root": str(root), "include_tests": include_tests,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "stats": cg.G.graph.get("stats", {}),
                "summary": summary_info,
            }
            (pdir / "graph.json").write_text(json.dumps(graph_to_json(cg.G), ensure_ascii=False), encoding="utf-8")
            (pdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            with self._lock:
                self._cache[pid] = Project(pid, meta, cg, index)
            self._update(pid, "done", 1.0)
        except Exception as e:  # 화면에 보여 줄 수 있게 메시지로 남긴다
            log.exception("analysis failed: %s", source)
            self._update(pid, "error", message=f"분석 실패: {e}")

    # ------------------------------------------------------------ 조회

    def status(self, pid: str) -> dict:
        with self._lock:
            job = self._jobs.get(pid)
            if job:
                return dict(job)
        if self._meta_path(pid).exists():
            return {"state": "done", "progress": 1.0, "message": STAGE_MESSAGES["done"]}
        raise ProjectNotFound(pid)

    def _meta_path(self, pid: str) -> Path:
        if not PROJECT_ID_RE.match(pid):
            raise ProjectNotFound(pid)
        return self.projects_dir / pid / "meta.json"

    def get(self, pid: str) -> Project:
        with self._lock:
            if pid in self._cache:
                return self._cache[pid]
            job = self._jobs.get(pid)
        if job and job.get("state") not in ("done", "error"):
            raise ProjectNotReady(pid)
        meta_path = self._meta_path(pid)
        if not meta_path.exists():
            raise ProjectNotFound(pid)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data = json.loads((meta_path.parent / "graph.json").read_text(encoding="utf-8"))
        cg = CodeGraph(graph_from_json(data), Path(meta["root"]))
        project = Project(pid, meta, cg, build_index(cg))
        with self._lock:
            self._cache[pid] = project
        return project

    def list(self) -> list[dict]:
        out = []
        for meta_path in self.projects_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: meta.get(k) for k in ("id", "name", "source", "created_at", "stats")})
        out.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return out
