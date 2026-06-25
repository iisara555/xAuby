"""Static (AST) capability scan for strategy plugins.

Marketplace/third-party strategies are imported into the host process, so the
"pure analyst" contract (no DB, no orders, no network, no engine access) cannot
be guaranteed by convention alone. This module statically scans a strategy's
own source for capabilities it must never use, before the engine trusts it.

Scope and limits: this is defense-in-depth, not a true sandbox. It catches the
obvious and accidental (and raises the bar for the malicious), but real
isolation needs a separate process / seccomp / RestrictedPython. It complements
the runtime timeout in :class:`xauby.strategies.sandbox.StrategyRunner`.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import List, Optional

# Strategies may import the plugin framework, but nothing else under xauby.
ALLOWED_XAUBY_PREFIX = "xauby.strategies"

# Third-party / stdlib modules a pure analyst must not touch (network, DB,
# process, filesystem, messaging, dynamic import/serialization).
FORBIDDEN_IMPORT_TOPS = frozenset({
    "os", "sys", "subprocess", "multiprocessing", "threading", "ctypes",
    "socket", "asyncio", "selectors",
    "requests", "httpx", "urllib", "urllib3", "http", "websocket", "websockets",
    "aiohttp",
    "sqlite3", "sqlalchemy", "psycopg2", "pymongo", "redis", "mysql",
    "telegram", "smtplib", "ftplib", "poplib", "imaplib",
    "shutil", "pathlib", "tempfile", "glob", "fileinput",
    "pickle", "marshal", "shelve", "importlib", "imp", "builtins", "__builtin__",
})

# Builtins that execute code or do I/O.
FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
})

# Attribute access used for sandbox-escape / introspection.
SUSPICIOUS_ATTRS = frozenset({
    "__globals__", "__builtins__", "__subclasses__", "__bases__", "__mro__",
    "__import__", "__code__", "__dict__",
})


@dataclass(frozen=True)
class Violation:
    kind: str          # "import" | "call" | "attribute"
    name: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"line {self.lineno}: {self.message}"


def _module_forbidden(module: Optional[str]) -> Optional[str]:
    """Return a reason string if importing ``module`` is forbidden, else None."""
    if not module:
        return None
    top = module.split(".")[0]
    if top == "xauby":
        if module == ALLOWED_XAUBY_PREFIX or module.startswith(ALLOWED_XAUBY_PREFIX + "."):
            return None
        return f"imports engine-internal module {module!r}; strategies may only use {ALLOWED_XAUBY_PREFIX}.*"
    if top in FORBIDDEN_IMPORT_TOPS:
        return f"imports forbidden module {module!r} (no network/DB/process/fs/dynamic access in a strategy)"
    return None


class _ScanVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: List[Violation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            reason = _module_forbidden(alias.name)
            if reason:
                self.violations.append(Violation("import", alias.name, node.lineno, reason))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Relative imports (level > 0) stay inside the plugin package -> allowed.
        if node.level == 0:
            reason = _module_forbidden(node.module)
            if reason:
                self.violations.append(
                    Violation("import", node.module or "", node.lineno, reason)
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
            self.violations.append(
                Violation("call", func.id, node.lineno, f"calls forbidden builtin {func.id!r}")
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in SUSPICIOUS_ATTRS:
            self.violations.append(
                Violation("attribute", node.attr, node.lineno, f"accesses {node.attr!r} (sandbox-escape pattern)")
            )
        self.generic_visit(node)


def scan_source(source: str, filename: str = "<strategy>") -> List[Violation]:
    """Scan strategy source code, returning a list of capability violations."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [Violation("syntax", filename, e.lineno or 0, f"could not parse source: {e}")]
    visitor = _ScanVisitor()
    visitor.visit(tree)
    return visitor.violations


def scan_strategy(strategy: object) -> List[Violation]:
    """Locate and scan the source module of a strategy instance.

    Returns an empty list when the source cannot be located (e.g. a strategy
    defined in a REPL) — callers decide how to treat that.
    """
    try:
        module = inspect.getmodule(type(strategy))
        source = inspect.getsource(module) if module else None
    except (OSError, TypeError):
        source = None
    if not source:
        return []
    name = getattr(module, "__name__", "<strategy>")
    return scan_source(source, filename=name)
