"""파이썬 소스코드를 ast로 분석해 함수·클래스·import·호출 정보를 뽑아낸다.

실행하지 않고 코드 '모양'만 보는 정적 분석이라, 실행 중에 정해지는 호출
(getattr, 동적 import 등)은 잡지 못한다. 이 한계는 graph.py의 휴리스틱으로 일부 보완한다.
"""
from __future__ import annotations

import ast
import io
import os
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# 분석에서 항상 제외할 폴더
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules", "build", "dist",
    "site-packages", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", ".eggs", "htmlcov",
}
# include_tests=False 일 때 제외할 폴더 (테스트·문서·예제는 구조 파악에 잡음이 되기 쉽다)
OPTIONAL_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks", "benchmark"}
MAX_DOC_CHARS = 600


@dataclass
class Entity:
    """함수·메서드·클래스 하나."""

    id: str  # "pkg/mod.py::Class.method"
    kind: str  # "function" | "method" | "class"
    name: str
    qualname: str  # "Class.method", "outer.inner"
    file_id: str  # "file:pkg/mod.py"
    parent_id: str  # 소속(파일 id 또는 클래스/함수 id)
    lineno: int
    end_lineno: int
    docstring: str = ""
    signature: str = ""
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)  # 클래스의 부모 클래스 이름(점 표기)
    calls: list[str] = field(default_factory=list)  # 호출한 이름들: "self.save", "utils.load", "print" ...
    # 변수 이름 → 타입(클래스) 이름. 함수: x = Foo() / x: Foo / def f(x: Foo), 클래스: self.x = Foo()
    var_types: dict[str, str] = field(default_factory=dict)
    is_async: bool = False


@dataclass
class ModuleInfo:
    """파이썬 파일 하나의 분석 결과."""

    file_id: str
    path: str  # 저장소 기준 상대 경로 (항상 '/' 사용)
    module: str  # 경로 그대로의 점 표기 이름: "src/pkg/mod.py" → "src.pkg.mod"
    is_package: bool  # __init__.py 여부
    docstring: str = ""
    lines: int = 0
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # 별칭 → (모듈, 심볼|None)
    import_modules: list[str] = field(default_factory=list)  # import 한 모듈 이름 후보들
    star_imports: list[str] = field(default_factory=list)  # from x import * 의 x
    entities: list[Entity] = field(default_factory=list)
    var_types: dict[str, str] = field(default_factory=dict)  # 모듈 변수 → 타입: app = Flask(__name__)
    toplevel_calls: list[str] = field(default_factory=list)  # 함수 밖(모듈 최상위)에서의 호출
    main_calls: list[str] = field(default_factory=list)  # if __name__ == "__main__": 안의 호출
    has_main_guard: bool = False
    error: str | None = None


# ---------------------------------------------------------------- 파일 찾기/읽기

def iter_python_files(root: Path, include_tests: bool = False, max_files: int = 3000) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith(".") or d.endswith(".egg-info"):
                continue
            if not include_tests and d.lower() in OPTIONAL_DIRS:
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)  # os.walk가 이 목록만 내려가도록
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            if not include_tests and (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"):
                continue
            files.append(Path(dirpath) / name)
            if len(files) >= max_files:
                return files
    return files


def read_source(path: Path) -> str:
    """PEP 263 인코딩 선언을 존중해서 읽고, 깨진 글자는 대체 문자로 바꾼다."""
    raw = path.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError:
        encoding = "utf-8"
    return raw.decode(encoding, errors="replace")


# ---------------------------------------------------------------- 이름 도우미

def module_name_for(rel_path: str) -> tuple[str, bool]:
    """'pkg/sub/mod.py' → ('pkg.sub.mod', False), 'pkg/__init__.py' → ('pkg', True)"""
    parts = rel_path[:-3].split("/")
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


def resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    """상대 import('from ..x import y')를 절대 모듈 이름으로 바꾼다."""
    parts = module.split(".") if module else []
    if not is_package:
        parts = parts[:-1]  # 이 파일이 들어 있는 패키지
    if level > 1:
        parts = parts[: max(0, len(parts) - (level - 1))]
    base = ".".join(parts)
    if target:
        return f"{base}.{target}" if base else target
    return base


def dotted_name(expr: ast.AST) -> str | None:
    """호출 대상 식을 점 표기 문자열로: foo / self.save / pkg.mod.func / super().save / ?.method"""
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name) and expr.func.id == "super":
            parts.append("super()")
            return ".".join(reversed(parts))
        inner = dotted_name(expr.func)
        if inner and "?" not in inner and parts:  # Foo().run → "Foo().run"
            return inner + "()." + ".".join(reversed(parts))
    if parts:  # x[0].bar(), (a or b).bar() 처럼 앞부분을 알 수 없는 경우
        parts.append("?")
        return ".".join(reversed(parts))
    return None


def _docstring(node: ast.AST) -> str:
    try:
        doc = ast.get_docstring(node, clean=True) or ""  # type: ignore[arg-type]
    except Exception:
        doc = ""
    doc = doc.strip()
    return doc if len(doc) <= MAX_DOC_CHARS else doc[: MAX_DOC_CHARS - 1] + "…"


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    try:
        ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        sig = f"{prefix}{node.name}({ast.unparse(node.args)}){ret}"
    except Exception:
        sig = f"{prefix}{node.name}(...)"
    return sig if len(sig) <= 200 else sig[:197] + "..."


def _type_name(annotation: ast.AST | None) -> str | None:
    """타입 힌트에서 클래스 이름만 꺼낸다: Foo, mod.Foo, "Foo", Optional[Foo], Foo | None"""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        text = annotation.value.strip()
        return text if text.replace(".", "").replace("_", "").isalnum() else None
    if isinstance(annotation, ast.Subscript):  # Optional[Foo], list[Foo]은 Optional만 풀어 준다
        base = dotted_name(annotation.value) or ""
        if base.split(".")[-1] == "Optional":
            return _type_name(annotation.slice)
        return None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):  # Foo | None
        for side in (annotation.left, annotation.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return _type_name(side)
        return None
    name = dotted_name(annotation)
    return name if name and not name.startswith("?") else None


def _is_main_guard(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]

    def is_name(x: ast.AST) -> bool:
        return isinstance(x, ast.Name) and x.id == "__name__"

    def is_main(x: ast.AST) -> bool:
        return isinstance(x, ast.Constant) and x.value == "__main__"

    return (is_name(left) and is_main(right)) or (is_main(left) and is_name(right))


# ---------------------------------------------------------------- AST 방문자

class _Collector(ast.NodeVisitor):
    def __init__(self, info: ModuleInfo):
        self.info = info
        self.stack: list[Entity] = []  # 현재 들어와 있는 클래스/함수
        self.in_main = False
        self.used_ids: set[str] = set()

    def _new_id(self, qualname: str) -> str:
        base = f"{self.info.path}::{qualname}"
        eid, k = base, 2
        while eid in self.used_ids:  # 같은 이름이 두 번 정의된 경우 (property setter 등)
            eid, k = f"{base}#{k}", k + 1
        self.used_ids.add(eid)
        return eid

    def _record_call(self, name: str) -> None:
        if self.stack:
            self.stack[-1].calls.append(name)
        else:
            self.info.toplevel_calls.append(name)
            if self.in_main:
                self.info.main_calls.append(name)

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted_name(node.func)
        if name:
            self._record_call(name)
        self.generic_visit(node)

    def _owner(self) -> tuple[Entity | None, str]:
        parent = self.stack[-1] if self.stack else None
        return parent, (parent.id if parent else self.info.file_id)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent, parent_id = self._owner()
        if parent is None:
            kind, qual = "function", node.name
        elif parent.kind == "class":
            kind, qual = "method", f"{parent.qualname}.{node.name}"
        else:  # 함수 안의 함수
            kind, qual = "function", f"{parent.qualname}.{node.name}"
        decorators = [dotted_name(d.func if isinstance(d, ast.Call) else d) or "" for d in node.decorator_list]
        if any(d.split(".")[-1] == "overload" for d in decorators):
            return  # @typing.overload 선언은 타입 힌트용 껍데기라 건너뛴다 (실제 구현만 남김)
        # 데코레이터와 기본값은 바깥 스코프에서 실행된다
        for d in node.decorator_list:
            self.visit(d)
        for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(d)
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        ent = Entity(
            id=self._new_id(qual), kind=kind, name=node.name, qualname=qual,
            file_id=self.info.file_id, parent_id=parent_id,
            lineno=start, end_lineno=getattr(node, "end_lineno", None) or node.lineno,
            docstring=_docstring(node), signature=_signature(node), decorators=decorators,
            is_async=isinstance(node, ast.AsyncFunctionDef),
        )
        args = node.args
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            tname = _type_name(a.annotation)
            if tname and a.arg not in ("self", "cls"):
                ent.var_types[a.arg] = tname
        self.info.entities.append(ent)
        self.stack.append(ent)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    # x = Foo() / self.x = Foo() / x: Foo 를 기억해 두면 x.method() 호출을 정확히 연결할 수 있다
    def _bind_type(self, target: ast.AST, tname: str) -> None:
        if not self.stack:
            if isinstance(target, ast.Name):
                self.info.var_types[target.id] = tname
            return
        cur = self.stack[-1]
        if isinstance(target, ast.Name):
            cur.var_types[target.id] = tname  # 함수의 지역 변수 또는 클래스 본문의 속성 선언
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            owner = next((e for e in reversed(self.stack) if e.kind == "class"), None)
            if owner is not None:
                owner.var_types.setdefault(target.attr, tname)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            tname = dotted_name(node.value.func)
            if tname and not tname.startswith("?"):
                for t in node.targets:
                    self._bind_type(t, tname)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        tname = _type_name(node.annotation)
        if tname:
            self._bind_type(node.target, tname)
        elif isinstance(node.value, ast.Call):
            called = dotted_name(node.value.func)
            if called and not called.startswith("?"):
                self._bind_type(node.target, called)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent, parent_id = self._owner()
        qual = node.name if parent is None else f"{parent.qualname}.{node.name}"
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for kw in node.keywords:
            self.visit(kw.value)
        try:
            bases_src = ", ".join(ast.unparse(b) for b in node.bases)
        except Exception:
            bases_src = ""
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        ent = Entity(
            id=self._new_id(qual), kind="class", name=node.name, qualname=qual,
            file_id=self.info.file_id, parent_id=parent_id,
            lineno=start, end_lineno=getattr(node, "end_lineno", None) or node.lineno,
            docstring=_docstring(node), signature=f"class {node.name}({bases_src})" if bases_src else f"class {node.name}",
            decorators=[dotted_name(d.func if isinstance(d, ast.Call) else d) or "" for d in node.decorator_list],
            bases=[b for b in (dotted_name(x) for x in node.bases) if b and not b.startswith("?")],
        )
        self.info.entities.append(ent)
        self.stack.append(ent)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:  # import a.b as c  → c = 모듈 a.b
                self.info.imports[alias.asname] = (alias.name, None)
            else:  # import a.b  → 이름 a 가 생긴다
                head = alias.name.split(".")[0]
                self.info.imports.setdefault(head, (head, None))
            self.info.import_modules.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            base = resolve_relative(self.info.module, self.info.is_package, node.level, node.module)
        else:
            base = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.info.star_imports.append(base)
                continue
            self.info.imports[alias.asname or alias.name] = (base, alias.name)
            self.info.import_modules.append(f"{base}.{alias.name}" if base else alias.name)
        if base:
            self.info.import_modules.append(base)

    def visit_If(self, node: ast.If) -> None:
        if not self.stack and _is_main_guard(node.test):
            self.info.has_main_guard = True
            prev, self.in_main = self.in_main, True
            for stmt in node.body:
                self.visit(stmt)
            self.in_main = prev
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)


# ---------------------------------------------------------------- 진입 함수

def parse_file(root: Path, path: Path, max_bytes: int = 1_500_000) -> ModuleInfo:
    rel = path.relative_to(root).as_posix()
    module, is_pkg = module_name_for(rel)
    info = ModuleInfo(file_id=f"file:{rel}", path=rel, module=module, is_package=is_pkg)
    try:
        size = path.stat().st_size
        if size > max_bytes:
            info.error = f"파일이 너무 커서 건너뛰었습니다 ({size // 1024}KB)"
            return info
        source = read_source(path)
        info.lines = source.count("\n") + 1
        tree = ast.parse(source, filename=rel)
    except SyntaxError as e:
        info.error = f"문법 오류로 분석하지 못했습니다 ({e.lineno}번째 줄)"
        return info
    except (OSError, ValueError) as e:
        info.error = f"파일을 읽지 못했습니다: {e}"
        return info
    info.docstring = _docstring(tree)
    try:
        _Collector(info).visit(tree)
    except RecursionError:
        info.error = "코드가 너무 깊게 중첩되어 일부만 분석했습니다"
    return info


def parse_repository(
    root: Path,
    include_tests: bool = False,
    max_files: int = 3000,
    max_bytes: int = 1_500_000,
    progress: Callable[[int, int], None] | None = None,
) -> list[ModuleInfo]:
    files = iter_python_files(root, include_tests=include_tests, max_files=max_files)
    infos: list[ModuleInfo] = []
    for i, f in enumerate(files):
        infos.append(parse_file(root, f, max_bytes=max_bytes))
        if progress and (i % 25 == 0 or i == len(files) - 1):
            progress(i + 1, len(files))
    return infos
