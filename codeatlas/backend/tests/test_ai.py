"""AI 기능 테스트 (가짜 LLM과 가짜 HTTP 서버를 써서 API 키 없이 돈다)."""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.ai.llm import AnthropicLLM, BaseLLM, LLMError, NoLLM, OpenAICompatibleLLM
from app.ai.qa import answer_question
from app.ai.search import build_index, korean_keywords, tokenize
from app.ai.summarize import summarize_all
from app.ai.tour import build_tour
from app.analyzer.graph import CodeGraph, build_graph
from app.analyzer.parser import parse_repository

SAMPLE = Path(__file__).parent / "sample_repo"


def sample_graph() -> CodeGraph:
    return CodeGraph(build_graph(parse_repository(SAMPLE), "sample"), SAMPLE)


class FakeLLM(BaseLLM):
    provider, model, available = "fake", "fake-1", True

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def complete(self, system, user, max_tokens=600):
        self.calls += 1
        if self.fail:
            raise LLMError("일부러 실패")
        if "keywords" in system.lower():
            return "login, token, auth"
        if user.startswith("질문:"):
            return "로그인은 AuthService.login에서 처리하고 make_token으로 토큰을 만듭니다 [1][2]."
        return "가짜 요약입니다."


class SummarizeTest(unittest.TestCase):
    def test_without_llm_uses_docstrings(self):
        cg = sample_graph()
        with tempfile.TemporaryDirectory() as tmp:
            info = summarize_all(cg, NoLLM(), Path(tmp) / "s.json")
        self.assertEqual(info["llm_calls"], 0)
        self.assertEqual(cg.node("app/service.py::AuthService.login")["summary"], "사용자 로그인을 처리하고 토큰을 발급한다.")
        self.assertEqual(cg.node("app/service.py::AuthService.login")["summary_source"], "docstring")
        self.assertTrue(all(d.get("summary") for _, d in cg.G.nodes(data=True)))

    def test_with_llm_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "s.json"
            llm = FakeLLM()
            cg = sample_graph()
            info = summarize_all(cg, llm, cache, workers=2)
            self.assertGreater(info["llm_calls"], 0)
            self.assertEqual(cg.node("app/utils.py::make_token")["summary"], "가짜 요약입니다.")
            self.assertEqual(cg.node("app/utils.py::make_token")["summary_source"], "llm")
            # 두 번째 실행은 캐시만 쓴다
            llm2 = FakeLLM()
            info2 = summarize_all(sample_graph(), llm2, cache, workers=2)
            self.assertEqual(llm2.calls, 0)
            self.assertEqual(info2["cache_hits"], info["llm_calls"])

    def test_llm_failures_fall_back(self):
        llm = FakeLLM(fail=True)
        cg = sample_graph()
        with tempfile.TemporaryDirectory() as tmp:
            info = summarize_all(cg, llm, Path(tmp) / "s.json", workers=1)
        self.assertLessEqual(llm.calls, 3)  # 연속 3번 실패하면 더 부르지 않는다
        self.assertTrue(info["error"])
        self.assertEqual(cg.node("app/utils.py::make_token")["summary_source"], "docstring")


class SearchQATourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cg = sample_graph()
        with tempfile.TemporaryDirectory() as tmp:
            summarize_all(cls.cg, NoLLM(), Path(tmp) / "s.json")
        cls.index = build_index(cls.cg)

    def test_tokenize(self):
        self.assertIn("token", tokenize("make_token"))
        self.assertIn("로그", tokenize("로그인은"))
        self.assertIn("login", korean_keywords("로그인은 어디서 처리돼?"))

    def test_korean_search(self):
        hits = [n for n, _ in self.index.search(tokenize("로그인 처리"), k=3)]
        self.assertIn("app/service.py::AuthService.login", hits)

    def test_answer_without_llm_returns_path(self):
        res = answer_question(self.cg, self.index, "로그인하면 토큰은 어떻게 만들어져?", NoLLM())
        path = res["path"]
        self.assertIn("app/service.py::AuthService.login", path)
        self.assertIn("app/utils.py::make_token", path)
        self.assertLess(path.index("app/service.py::AuthService.login"), path.index("app/utils.py::make_token"))
        self.assertTrue(res["answer"])
        self.assertTrue(res["nodes"])

    def test_answer_with_llm(self):
        res = answer_question(self.cg, self.index, "로그인은 어디서 처리돼?", FakeLLM())
        self.assertIn("AuthService.login", res["answer"])
        self.assertIn("login", res["keywords"])

    def test_tour_follows_entry_point(self):
        steps = build_tour(self.cg)["steps"]
        ids = [s["id"] for s in steps]
        self.assertEqual(ids[:3], ["file:app/main.py", "app/main.py::main", "app/service.py::AuthService.login"])


class _FakeAPI(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeAPI.seen.append((self.path, self.headers, body))  # 헤더 이름은 대소문자 구분 없이 조회
        if self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"role": "assistant", "content": "openai 응답"}}]}
        else:
            out = {"type": "message", "content": [{"type": "text", "text": "claude 응답"}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LLMClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _FakeAPI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_openai_compatible(self):
        llm = OpenAICompatibleLLM("openai", self.base + "/v1", "sk-test", "my-model")
        self.assertEqual(llm.complete("시스템", "사용자"), "openai 응답")
        path, headers, body = _FakeAPI.seen[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(body["model"], "my-model")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "시스템"})

    def test_anthropic(self):
        llm = AnthropicLLM(self.base, "key-test", "claude-haiku-5-5")
        self.assertEqual(llm.complete("시스템", "사용자", max_tokens=100), "claude 응답")
        path, headers, body = _FakeAPI.seen[-1]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers["x-api-key"], "key-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(body["system"], "시스템")
        self.assertEqual(body["max_tokens"], 100)
        self.assertEqual(body["messages"], [{"role": "user", "content": "사용자"}])

    def test_connection_error(self):
        llm = OpenAICompatibleLLM("ollama", "http://127.0.0.1:9/v1", "", "m", timeout=2)
        with self.assertRaises(LLMError):
            llm.complete("s", "u")


try:
    import httpx  # noqa: F401  (FastAPI TestClient에 필요)
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@unittest.skipUnless(HAS_HTTPX, "pip install httpx 후 API 테스트가 실행됩니다")
class APITest(unittest.TestCase):
    def test_full_flow(self):
        from fastapi.testclient import TestClient

        import app.main as main
        from app.store import ProjectStore

        with tempfile.TemporaryDirectory() as tmp:
            main.store = ProjectStore(replace(main.settings, data_dir=Path(tmp)), NoLLM())
            client = TestClient(main.app)
            self.assertTrue(client.get("/api/health").json()["ok"])
            pid = client.post("/api/projects", json={"source": str(SAMPLE)}).json()["project_id"]
            for _ in range(100):
                st = client.get(f"/api/projects/{pid}/status").json()
                if st["state"] in ("done", "error"):
                    break
                time.sleep(0.05)
            self.assertEqual(st["state"], "done", st)
            graph = client.get(f"/api/projects/{pid}/graph").json()
            self.assertTrue(any(n["id"] == "app/service.py::AuthService.login" for n in graph["nodes"]))
            node = client.get(f"/api/projects/{pid}/node", params={"id": "app/service.py::AuthService.login"}).json()
            self.assertIn("def login", node["code"])
            self.assertEqual({c["id"] for c in node["callers"]}, {"app/main.py::main", "app/main.py::App.run"})
            ans = client.post(f"/api/projects/{pid}/ask", json={"question": "토큰은 어떻게 만들어?"}).json()
            self.assertIn("app/utils.py::make_token", [n["id"] for n in ans["nodes"]])
            tour = client.get(f"/api/projects/{pid}/tour").json()
            self.assertGreaterEqual(len(tour["steps"]), 3)
            self.assertEqual(client.get("/api/projects/없는-프로젝트/graph").status_code, 404)


if __name__ == "__main__":
    unittest.main()
