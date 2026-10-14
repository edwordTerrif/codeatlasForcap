"""분석 엔진 테스트.  실행: (backend 폴더에서) python -m unittest discover -s tests -v"""
import unittest
from pathlib import Path

from app.analyzer.fetch import parse_github_url
from app.analyzer.graph import CodeGraph, build_graph, graph_from_json, graph_to_json, pagerank
from app.analyzer.parser import dotted_name, module_name_for, parse_repository, resolve_relative

SAMPLE = Path(__file__).parent / "sample_repo"


def calls_of(G, src):
    return {t for t in G.successors(src) if G[src][t]["kind"] == "calls"}


class ParserTest(unittest.TestCase):
    def test_module_names(self):
        self.assertEqual(module_name_for("pkg/sub/mod.py"), ("pkg.sub.mod", False))
        self.assertEqual(module_name_for("pkg/__init__.py"), ("pkg", True))

    def test_relative_import(self):
        self.assertEqual(resolve_relative("pkg.sub.mod", False, 1, "utils"), "pkg.sub.utils")
        self.assertEqual(resolve_relative("pkg.sub.mod", False, 2, "core"), "pkg.core")
        self.assertEqual(resolve_relative("pkg", True, 1, "service"), "pkg.service")

    def test_dotted_name(self):
        import ast
        def name(src):
            return dotted_name(ast.parse(src).body[0].value.func)
        self.assertEqual(name("self.save()"), "self.save")
        self.assertEqual(name("pkg.mod.func()"), "pkg.mod.func")
        self.assertEqual(name("super().save()"), "super().save")
        self.assertEqual(name("App().run()"), "App().run")
        self.assertEqual(name("items[0].run()"), "?.run")

    def test_tests_are_excluded_by_default(self):
        paths = [m.path for m in parse_repository(SAMPLE)]
        self.assertNotIn("tests/login_check.py", paths)
        self.assertIn("app/service.py", paths)
        with_tests = [m.path for m in parse_repository(SAMPLE, include_tests=True)]
        self.assertIn("tests/login_check.py", with_tests)

    def test_entities(self):
        mods = {m.path: m for m in parse_repository(SAMPLE)}
        service = mods["app/service.py"]
        names = {e.qualname for e in service.entities}
        self.assertTrue({"BaseService", "AuthService", "AuthService.login", "AuthService._check"} <= names)
        login = next(e for e in service.entities if e.qualname == "AuthService.login")
        self.assertEqual(login.kind, "method")
        self.assertIn("로그인", login.docstring)
        self.assertTrue(mods["app/main.py"].has_main_guard)


class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.G = build_graph(parse_repository(SAMPLE), "sample")

    def test_call_resolution(self):
        G = self.G
        self.assertEqual(
            calls_of(G, "app/main.py::main"),
            {"app/service.py::AuthService", "app/service.py::AuthService.login",  # svc = AuthService(); svc.login()
             "app/utils.py::log", "app/main.py::App", "app/main.py::App.run"},    # utils.log() / App().run()
        )
        self.assertEqual(
            calls_of(G, "app/service.py::AuthService.login"),
            {"app/service.py::AuthService._check",       # self._check()
             "app/service.py::BaseService.audit",        # 부모 클래스에서 상속받은 self.audit()
             "app/utils.py::make_token"},                # from .utils import make_token
        )
        self.assertIn("app/service.py::AuthService.login", calls_of(G, "app/main.py::App.run"))  # self.auth.login()
        self.assertEqual(G["app/service.py::AuthService"]["app/service.py::BaseService"]["kind"], "inherits")

    def test_hierarchy_and_edges_are_consistent(self):
        G = self.G
        for n, d in G.nodes(data=True):
            if d["parent"] is not None:
                self.assertIn(d["parent"], G, n)
        self.assertEqual(G.nodes["file:app/main.py"]["entry_calls"], ["app/main.py::main"])

    def test_importance_and_roundtrip(self):
        G = self.G
        for _, d in G.nodes(data=True):
            self.assertGreaterEqual(d["importance"], 0.0)
            self.assertLessEqual(d["importance"], 1.0)
        G2 = graph_from_json(graph_to_json(G))
        self.assertEqual(set(G.nodes), set(G2.nodes))
        self.assertEqual(set(G.edges), set(G2.edges))

    def test_source_snippet(self):
        cg = CodeGraph(self.G, SAMPLE)
        start, code = cg.source("app/service.py::AuthService.login")
        self.assertTrue(code.lstrip().startswith("def login"))
        self.assertGreater(start, 1)

    def test_pagerank_sums_to_one(self):
        pr = pagerank(["a", "b", "c"], [("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 1.0)])
        self.assertAlmostEqual(sum(pr.values()), 1.0, places=6)


class FetchTest(unittest.TestCase):
    def test_github_urls(self):
        self.assertEqual(parse_github_url("https://github.com/psf/requests"), ("psf", "requests", None))
        self.assertEqual(parse_github_url("github.com/psf/requests.git"), ("psf", "requests", None))
        self.assertEqual(parse_github_url("https://github.com/pallets/flask/tree/stable/src"), ("pallets", "flask", "stable"))
        self.assertIsNone(parse_github_url("https://gitlab.com/a/b"))
        self.assertIsNone(parse_github_url("C:/Users/me/project"))


if __name__ == "__main__":
    unittest.main()
