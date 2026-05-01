#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FunctionDefRecord:
    module: str
    qualname: str
    path: str
    lineno: int
    is_async: bool
    parent_class: str | None
    body_stmt_count: int
    thin_forwarder: bool

    @property
    def symbol(self) -> str:
        return f"{self.module}:{self.qualname}"

    @property
    def name(self) -> str:
        return self.qualname.split(".")[-1]


@dataclass(frozen=True)
class CallSiteRecord:
    caller_symbol: str | None
    caller_path: str
    lineno: int


class _ModuleAnalyzer(ast.NodeVisitor):
    def __init__(self, module: str, relative_path: str) -> None:
        self.module = module
        self.relative_path = relative_path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.functions: dict[str, FunctionDefRecord] = {}
        self.imported_functions: dict[str, str] = {}
        self.imported_modules: dict[str, str] = {}
        self.calls: list[tuple[str, CallSiteRecord]] = []
        self._record_calls = False

    def index(self, tree: ast.AST) -> None:
        self._record_calls = False
        self.visit(tree)

    def collect_calls(self, tree: ast.AST) -> None:
        self.class_stack.clear()
        self.function_stack.clear()
        self._record_calls = True
        self.visit(tree)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if self._record_calls:
            return
        if node.level < 0:
            return
        resolved_module = _resolve_imported_module(self.module, node.module, level=node.level)
        if resolved_module is None:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.imported_functions[local_name] = f"{resolved_module}:{alias.name}"

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        if self._record_calls:
            return
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.imported_modules[local_name] = alias.name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node, is_async=True)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if not self._record_calls:
            self.generic_visit(node)
            return
        callee_symbol = self._resolve_call_target(node.func)
        if callee_symbol is not None:
            caller_symbol = (
                f"{self.module}:{'.'.join([*self.class_stack, *self.function_stack])}"
                if self.function_stack
                else None
            )
            self.calls.append(
                (
                    callee_symbol,
                    CallSiteRecord(
                        caller_symbol=caller_symbol,
                        caller_path=self.relative_path,
                        lineno=node.lineno,
                    ),
                )
            )
        self.generic_visit(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        qualname_parts = [*self.class_stack, *self.function_stack, node.name]
        qualname = ".".join(qualname_parts)
        if not self._record_calls:
            self.functions[qualname] = FunctionDefRecord(
                module=self.module,
                qualname=qualname,
                path=self.relative_path,
                lineno=node.lineno,
                is_async=is_async,
                parent_class=self.class_stack[-1] if self.class_stack else None,
                body_stmt_count=len(node.body),
                thin_forwarder=_is_thin_forwarder(node),
            )
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def _resolve_call_target(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            local_symbol = self._resolve_local_name(func.id)
            if local_symbol is not None:
                return local_symbol
            return self.imported_functions.get(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in {"self", "cls"} and self.class_stack:
                method_symbol = f"{self.module}:{self.class_stack[-1]}.{func.attr}"
                if any(record.symbol == method_symbol for record in self.functions.values()):
                    return method_symbol
            module_name = self.imported_modules.get(func.value.id)
            if module_name is not None:
                return f"{module_name}:{func.attr}"
        return None

    def _resolve_local_name(self, name: str) -> str | None:
        if self.class_stack:
            method_symbol = f"{self.module}:{self.class_stack[-1]}.{name}"
            if any(record.symbol == method_symbol for record in self.functions.values()):
                return method_symbol
        function_stack = list(self.function_stack)
        while function_stack:
            candidate = ".".join([*self.class_stack, *function_stack, name])
            if candidate in self.functions:
                return f"{self.module}:{candidate}"
            function_stack.pop()
        candidate = ".".join([*self.class_stack, name]) if self.class_stack else name
        if candidate in self.functions:
            return f"{self.module}:{candidate}"
        return None


def _resolve_imported_module(
    current_module: str,
    imported_module: str | None,
    *,
    level: int,
) -> str | None:
    if level == 0:
        return imported_module
    module_parts = current_module.split(".")
    if level > len(module_parts):
        return None
    base_parts = module_parts[: len(module_parts) - level]
    if imported_module:
        base_parts.extend(imported_module.split("."))
    return ".".join(part for part in base_parts if part)


def _is_thin_forwarder(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = node.body
    if not body or len(body) > 2:
        return False
    if any(
        isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for stmt in body
    ):
        return False
    tail = body[-1]
    if isinstance(tail, ast.Return):
        return _is_forwarding_expr(tail.value)
    if isinstance(tail, ast.Expr):
        return _is_forwarding_expr(tail.value)
    return False


def _is_forwarding_expr(value: ast.expr | None) -> bool:
    if value is None:
        return False
    if isinstance(value, ast.Await):
        return _is_forwarding_expr(value.value)
    if isinstance(value, ast.Call):
        return True
    return False


def _module_name_from_path(path: Path, roots: list[Path]) -> str | None:
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        parts = list(relative.with_suffix("").parts)
        if (root / "__init__.py").is_file():
            parts.insert(0, root.name)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    return None


def _python_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
            continue
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return sorted(set(files))


def _build_report(roots: list[Path], max_callers: int) -> list[dict[str, Any]]:
    analyzers: list[_ModuleAnalyzer] = []
    functions_by_symbol: dict[str, FunctionDefRecord] = {}
    calls_by_symbol: dict[str, list[CallSiteRecord]] = defaultdict(list)

    for file_path in _python_files(roots):
        module = _module_name_from_path(file_path, roots)
        if not module:
            continue
        relative_path = file_path.as_posix()
        analyzer = _ModuleAnalyzer(module=module, relative_path=relative_path)
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=relative_path)
        analyzer.index(tree)
        analyzer.collect_calls(tree)
        analyzers.append(analyzer)
        for record in analyzer.functions.values():
            functions_by_symbol[record.symbol] = record

    for analyzer in analyzers:
        for callee_symbol, callsite in analyzer.calls:
            if callee_symbol in functions_by_symbol:
                calls_by_symbol[callee_symbol].append(callsite)

    rows: list[dict[str, Any]] = []
    for symbol, record in sorted(functions_by_symbol.items(), key=lambda item: item[0]):
        callsites = calls_by_symbol.get(symbol, [])
        distinct_callers = {
            callsite.caller_symbol or f"<module>:{callsite.caller_path}"
            for callsite in callsites
        }
        if len(distinct_callers) > max_callers:
            continue
        rows.append(
            {
                "symbol": symbol,
                "path": record.path,
                "lineno": record.lineno,
                "is_async": record.is_async,
                "parent_class": record.parent_class,
                "body_stmt_count": record.body_stmt_count,
                "thin_forwarder": record.thin_forwarder,
                "direct_callsite_count": len(callsites),
                "distinct_caller_count": len(distinct_callers),
                "callers": [
                    {
                        "symbol": callsite.caller_symbol,
                        "path": callsite.caller_path,
                        "lineno": callsite.lineno,
                    }
                    for callsite in callsites
                ],
            }
        )
    rows.sort(
        key=lambda row: (
            row["distinct_caller_count"],
            not row["thin_forwarder"],
            row["path"],
            row["lineno"],
        )
    )
    return rows


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find low-consumer local functions and flag likely thin forwarding wrappers."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src/opencode_a2a"],
        help="Source roots to analyze. Defaults to src/opencode_a2a.",
    )
    parser.add_argument(
        "--max-callers",
        type=int,
        default=2,
        help="Only report functions with direct local caller count up to this value.",
    )
    parser.add_argument(
        "--thin-only",
        action="store_true",
        help="Only show candidates flagged as likely thin forwarders.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    roots = [Path(path) for path in args.paths]
    rows = _build_report(roots, max_callers=args.max_callers)
    if args.thin_only:
        rows = [row for row in rows if row["thin_forwarder"]]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("No matching local functions found.")
        return 0

    for row in rows:
        label = "thin-forwarder" if row["thin_forwarder"] else "candidate"
        print(
            f"[{label}] {row['symbol']} "
            f"callers={row['distinct_caller_count']} "
            f"callsites={row['direct_callsite_count']} "
            f"body_stmts={row['body_stmt_count']} "
            f"{row['path']}:{row['lineno']}"
        )
        for caller in row["callers"]:
            caller_symbol = caller["symbol"] or "<module>"
            print(f"  <- {caller_symbol} ({caller['path']}:{caller['lineno']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
