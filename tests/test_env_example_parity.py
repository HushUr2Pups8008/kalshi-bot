#!/usr/bin/env python3
"""Every production env read is in .env.example, and every example name is read.

Process variables (PATH, git, Python) are not app config. Secrets are names only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
PROCESS = {
    "GIT_CONFIG_COUNT",
    "GIT_EDITOR",
    "GIT_SSH_COMMAND",
    "GIT_TERMINAL_PROMPT",
    "PATH",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
}
SKIP_PARTS = {".venv", "__pycache__", "windows_archive"}
ENV_OWNERS = {"environ", "env", "environment"}
ENV_METHODS = {"get", "pop", "setdefault"}
WRAPPERS = {
    "getenv",
    "_parse_bool_env",
    "_raw_flag",
    "_positive_int",
    "_env_truthy",
    "event_news_desk_flag_enabled",
}


def skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def py_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if not skip(path)]


def module_key(path: Path) -> str:
    return path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")


def _const_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and ENV_NAME.match(node.value):
        return node.value
    return None


def local_bindings(tree: ast.AST) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        value = None
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
            targets = [node.target]
        name = _const_name(value) if value is not None else None
        if name is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.setdefault(target.id, set()).add(name)
    return found


def imported_aliases(tree: ast.AST) -> list[tuple[str, str, str]]:
    """(module, remote_name, local_name)."""
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            rows.append((node.module, alias.name, alias.asname or alias.name))
    return rows


def resolve_bindings(modules: dict[str, dict[str, set[str]]], imports: dict[str, list[tuple[str, str, str]]]) -> None:
    changed = True
    while changed:
        changed = False
        for mod, rows in imports.items():
            dest = modules.setdefault(mod, {})
            for source, remote, local in rows:
                values = modules.get(source, {}).get(remote)
                if not values:
                    continue
                bucket = dest.setdefault(local, set())
                before = len(bucket)
                bucket.update(values)
                changed = changed or len(bucket) != before


def _owner_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_env_container(node: ast.AST) -> bool:
    name = _owner_name(node)
    return name in ENV_OWNERS or name.endswith("environ")


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _env_aliases(fn: ast.AST) -> set[str]:
    aliases = {"environ", "env", "environment"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            value = node.value
            hit = _is_os_environ(value) or (isinstance(value, ast.Name) and value.id in aliases)
            if isinstance(value, ast.IfExp) and (
                _is_os_environ(value.body) or _is_os_environ(value.orelse)
                or (isinstance(value.body, ast.Name) and value.body.id in aliases)
                or (isinstance(value.orelse, ast.Name) and value.orelse.id in aliases)
            ):
                hit = True
            if hit and target.id not in aliases:
                aliases.add(target.id)
                changed = True
    return aliases


def _first_param(fn: ast.AST) -> str | None:
    args = getattr(fn, "args", None)
    if args is None or not args.args:
        return None
    return args.args[0].arg


def _key_is_param(node: ast.AST | None, param: str | None) -> bool:
    return param is not None and isinstance(node, ast.Name) and node.id == param


def _function_is_reader(fn: ast.AST, readers: set[str]) -> bool:
    param = _first_param(fn)
    if param is None:
        return False
    aliases = _env_aliases(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ENV_METHODS and node.args and _key_is_param(node.args[0], param):
                owner = node.func.value
                if _is_os_environ(owner) or (isinstance(owner, ast.Name) and owner.id in aliases):
                    return True
        if isinstance(node, ast.Subscript) and _key_is_param(node.slice, param):
            if _is_os_environ(node.value) or (isinstance(node.value, ast.Name) and node.value.id in aliases):
                return True
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _key_is_param(node.left, param):
                    if _is_os_environ(comp) or (isinstance(comp, ast.Name) and comp.id in aliases):
                        return True
        if isinstance(node, ast.Call):
            callee = _func_name(node.func)
            if callee in readers and node.args and _key_is_param(node.args[0], param):
                return True
    return False


class Reader(ast.NodeVisitor):
    def __init__(self, bindings: dict[str, set[str]], path: Path, readers: set[str]) -> None:
        self.bindings = bindings
        self.path = path
        self.readers = readers
        self.aliases = {"environ", "env", "environment"}
        self.sites: list[tuple[str, int]] = []

    def add(self, names: set[str], line: int) -> None:
        for name in names:
            if ENV_NAME.match(name):
                self.sites.append((name, line))

    def resolve(self, node: ast.AST | None) -> set[str]:
        if node is None:
            return set()
        direct = _const_name(node)
        if direct:
            return {direct}
        if isinstance(node, ast.Name):
            return set(self.bindings.get(node.id, set()))
        return set()

    def _take_key(self, owner: ast.AST, key: ast.AST | None, line: int) -> None:
        if _is_os_environ(owner) or (isinstance(owner, ast.Name) and owner.id in self.aliases):
            self.add(self.resolve(key), line)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.AST) -> None:
        previous = self.aliases
        self.aliases = _env_aliases(node)
        self.generic_visit(node)
        self.aliases = previous

    def visit_Call(self, node: ast.Call) -> None:
        func = _func_name(node.func)
        if func == "getenv" and node.args:
            self.add(self.resolve(node.args[0]), node.lineno)
        elif func in self.readers and node.args:
            self.add(self.resolve(node.args[0]), node.lineno)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ENV_METHODS and node.args:
            self._take_key(node.func.value, node.args[0], node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._take_key(node.value, node.slice, node.lineno)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                self._take_key(comp, node.left, node.lineno)
                self._take_key(node.left, comp, node.lineno)
        self.generic_visit(node)


def reads(root: Path) -> dict[str, list[str]]:
    parsed: dict[str, tuple[Path, ast.AST]] = {}
    modules: dict[str, dict[str, set[str]]] = {}
    imports: dict[str, list[tuple[str, str, str]]] = {}
    for path in py_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError, OSError):
            continue
        key = module_key(path)
        parsed[key] = (path, tree)
        modules[key] = local_bindings(tree)
        imports[key] = imported_aliases(tree)
    resolve_bindings(modules, imports)
    readers = set(WRAPPERS)
    changed = True
    while changed:
        changed = False
        for _key, (_path, tree) in parsed.items():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in readers:
                    if _function_is_reader(node, readers):
                        readers.add(node.name)
                        changed = True
    found: dict[str, list[str]] = {}
    for key, (path, tree) in parsed.items():
        reader = Reader(modules.get(key, {}), path, readers)
        reader.visit(tree)
        for name, line in reader.sites:
            site = f"{path.relative_to(root).as_posix()}:{line}"
            bucket = found.setdefault(name, [])
            if site not in bucket:
                bucket.append(site)
    return found


def example_names(path: Path) -> list[str]:
    names = []
    if not path.is_file():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        name = stripped.split("=", 1)[0].strip()
        if ENV_NAME.match(name):
            names.append(name)
    return names


def production_names(root: Path) -> set[str]:
    names: set[str] = set()
    for name, sites in reads(root).items():
        if name in PROCESS:
            continue
        if any(not site.startswith("tests/") for site in sites):
            names.add(name)
    return names


def test_env_example_matches_production_reads() -> None:
    """A name in .env.example is read by production code, and the reverse."""
    prod = production_names(ROOT)
    listed = example_names(ROOT / ".env.example")
    assert len(listed) == len(set(listed)), "duplicate name in .env.example"
    missing = sorted(prod - set(listed))
    extra = sorted(set(listed) - prod)
    assert not missing and not extra, f"missing={missing} extra={extra}"


def test_boot_config_masks_client_id(monkeypatch) -> None:
    """A client id is a secret. Boot prints set, never the value."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "client-id-sentinel")
    monkeypatch.setenv("KALSHI_ENV", "demo")
    from utils.output_paths import boot_config_lines

    lines = boot_config_lines()
    assert [line for line in lines if line.startswith("REDDIT_CLIENT_ID=")] == [
        "REDDIT_CLIENT_ID=set"
    ]
    assert [line for line in lines if line.startswith("KALSHI_ENV=")] == ["KALSHI_ENV=demo"]
    for line in lines:
        assert "client-id-sentinel" not in line
