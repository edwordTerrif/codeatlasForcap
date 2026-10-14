"""명령줄 도구: 화면 없이 분석·질문·투어를 실행해 본다 (발표 리허설, 디버깅용).

사용 예 (backend 폴더에서):
  python cli.py analyze https://github.com/psf/requests
  python cli.py list
  python cli.py ask psf-requests "세션은 요청을 어떻게 보내?"
  python cli.py tour psf-requests
"""
from __future__ import annotations

import argparse
import sys
import time

from app.ai.llm import get_llm
from app.ai.qa import answer_question
from app.ai.tour import build_tour
from app.config import settings
from app.store import ProjectStore


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # Windows 콘솔에서 특수문자 때문에 멈추지 않도록
    ap = argparse.ArgumentParser(description="CodeAtlas 명령줄 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="저장소 분석")
    a.add_argument("source", help="GitHub 주소 또는 로컬 폴더")
    a.add_argument("--include-tests", action="store_true", help="tests/docs/examples도 분석")
    a.add_argument("--no-summary", action="store_true", help="LLM 요약 없이 분석")
    sub.add_parser("list", help="분석한 프로젝트 목록")
    q = sub.add_parser("ask", help="코드에 대해 질문")
    q.add_argument("project")
    q.add_argument("question")
    t = sub.add_parser("tour", help="읽기 투어 출력")
    t.add_argument("project")
    args = ap.parse_args()

    llm = get_llm(settings)
    store = ProjectStore(settings, llm)
    if llm.warning:
        print(f"[알림] {llm.warning}")

    if args.cmd == "analyze":
        pid = store.start(args.source, include_tests=args.include_tests, summarize=not args.no_summary)
        last = ""
        while True:
            st = store.status(pid)
            line = f"[{st['progress'] * 100:5.1f}%] {st['message']}"
            if line != last:
                print(line)
                last = line
            if st["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        if st["state"] == "error":
            return 1
        meta = store.get(pid).meta
        print(f"\n프로젝트 id: {pid}")
        for k, v in meta["stats"].items():
            print(f"  {k}: {v}")
        print(f"  요약: {meta['summary']}")
    elif args.cmd == "list":
        for m in store.list():
            print(f"{m['id']:40s} {m['created_at']}  {m['source']}")
    elif args.cmd == "ask":
        p = store.get(args.project)
        res = answer_question(p.cg, p.index, args.question, llm)
        print(res["answer"])
        if res["path"]:
            print("\n경로:", " -> ".join(p.cg.node(n)["qualname"] for n in res["path"]))
    elif args.cmd == "tour":
        for s in build_tour(store.get(args.project).cg)["steps"]:
            print(f"{s['step']}. {s['title']}  ({s['path']}:{s['lineno']})")
            print(f"   왜: {s['reason']}")
            print(f"   요약: {s['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
