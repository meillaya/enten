"""Failure attribution: map test failures to source symbols and git blame."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, computed_field
from tree_sitter import Language, Node, Parser

from enten.logging import get_logger
from enten.parsers.models import PytestResult
from enten.parsers.runners import SandboxHandle

logger = get_logger(__name__)

_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)',
    re.MULTILINE,
)

_DEFINITION_TYPES = frozenset(
    {
        "function_definition",
        "class_definition",
        "function_declaration",
        "class_declaration",
        "method_definition",
        "export_statement",
        "lexical_declaration",
        "decorated_definition",
    }
)


class Attribution(BaseModel):
    """Attribution of a single test failure to a source location and author."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    failure_id: str
    file: str
    line: int
    symbol_kind: str
    symbol_name: str
    parent_symbol: str | None
    blame_commit: str | None
    blame_author: str | None
    blame_message: str | None
    confidence: float

    @computed_field
    @property
    def low_confidence(self) -> bool:
        """True when the confidence is below the low-confidence threshold."""
        return self.confidence < 0.3


def _language_for_file(path: str) -> Literal["python", "typescript"] | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in (".ts", ".tsx"):
        return "typescript"
    return None


def _load_language(language: Literal["python", "typescript"]) -> Language:
    if language == "python":
        import tree_sitter_python as tsp

        return Language(tsp.language())
    import tree_sitter_typescript as tst

    return Language(tst.language_typescript())


def _load_tsx_language() -> Language:
    import tree_sitter_typescript as tst

    return Language(tst.language_tsx())


def _identifier(source: bytes, node: Node) -> str | None:
    """Return the text of the first identifier child using byte offsets."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            return source[child.start_byte : child.end_byte].decode("utf-8")
    return None


def _symbol_name(source: bytes, node: Node) -> str | None:
    """Best-effort extraction of an identifier name from an AST node."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _symbol_name(source, child)
        return None
    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration", "lexical_declaration"):
                return _symbol_name(source, child)
        return None
    if node.type == "lexical_declaration":
        for child in node.children:
            if child.type == "variable_declarator":
                return _symbol_name(source, child)
        return None
    return _identifier(source, node)


def _find_enclosing_symbol(
    source: bytes,
    path: str,
    line: int,
) -> tuple[str, str, str | None] | None:
    """Return (kind, name, parent_name) for the deepest symbol containing *line*."""
    lang = _language_for_file(path)
    if lang is None:
        return None

    if lang == "typescript" and path.endswith(".tsx"):
        language = _load_tsx_language()
    else:
        language = _load_language(lang)
    parser = Parser(language)
    tree = parser.parse(source)

    def visit(node: Node) -> Node | None:
        start = node.start_point.row + 1
        end = node.end_point.row + 1
        best: Node | None = None
        if start <= line <= end and node.type in _DEFINITION_TYPES:
            best = node
        for child in node.children:
            child_best = visit(child)
            if child_best is not None and (
                best is None
                or (
                    child_best.start_byte >= best.start_byte
                    and child_best.end_byte <= best.end_byte
                )
            ):
                best = child_best
        return best

    best = visit(tree.root_node)
    if best is None:
        return None

    name = _symbol_name(source, best)
    if name is None:
        return None

    kind = best.type.replace("_definition", "").replace("_declaration", "")
    if best.type == "lexical_declaration":
        snippet = source[best.start_byte : best.end_byte]
        if b"function" in snippet:
            kind = "function"

    parent: str | None = None
    parent_node = best.parent
    while parent_node is not None:
        if parent_node.type in _DEFINITION_TYPES:
            parent = _symbol_name(source, parent_node)
            if parent:
                break
        parent_node = parent_node.parent

    return kind, name, parent


def _extract_frames(message: str) -> list[tuple[str, int]]:
    """Parse Python-style traceback frames from a failure message."""
    return [(m.group("file"), int(m.group("line"))) for m in _FRAME_RE.finditer(message)]


def _sandbox_path(path: str) -> str:
    """Resolve a repository-relative path to an absolute sandbox path."""
    return path if path.startswith("/") else f"/workspace/{path}"


async def _read_file(handle: SandboxHandle, path: str) -> bytes | None:
    """Read *path* from the sandbox via ``cat``."""
    if not path:
        return None
    result = await handle.run(["cat", _sandbox_path(path)], timeout_seconds=10)
    if result.exit_code != 0:
        logger.debug("failed to read file in sandbox", path=path, stderr=result.stderr)
        return None
    return result.stdout.encode("utf-8")


async def _git_blame(
    handle: SandboxHandle,
    path: str,
    line: int,
) -> tuple[str | None, str | None, str | None]:
    """Run ``git blame -L`` in the sandbox and return commit, author, message."""
    if not path or line <= 0:
        return None, None, None
    result = await handle.run(
        ["git", "blame", "-L", f"{line},{line}", "-p", _sandbox_path(path)],
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        return None, None, None

    commit: str | None = None
    author: str | None = None
    message: str | None = None
    for raw in result.stdout.splitlines():
        if not raw or raw.startswith("\t"):
            continue
        if commit is None:
            parts = raw.split()
            if parts:
                commit = parts[0]
            continue
        if raw.startswith("author "):
            author = raw[len("author ") :].strip()
        elif raw.startswith("summary "):
            message = raw[len("summary ") :].strip()
    return commit, author, message


async def attribute_failures(
    test_results: PytestResult,
    sandbox_handle: SandboxHandle,
) -> list[Attribution]:
    """Attribute each failing test to a source symbol and recent git blame."""
    attributions: list[Attribution] = []
    for test in test_results.tests:
        if test.outcome not in ("failed", "error"):
            continue

        frames = _extract_frames(test.message or "")
        file: str = ""
        line: int = 0
        confidence = 0.0
        source: bytes | None = None

        if frames:
            for frame_file, frame_line in reversed(frames):
                source = await _read_file(sandbox_handle, frame_file)
                if source is not None:
                    file, line = frame_file, frame_line
                    confidence = 1.0
                    break
            if source is None:
                file, line = frames[-1]
                confidence = 0.2
        elif test.file and test.line:
            file, line = test.file, test.line
            source = await _read_file(sandbox_handle, file)
            confidence = 0.3 if source is not None else 0.2
        else:
            file = test.file or ""
            line = test.line or 0
            confidence = 0.0

        symbol_kind, symbol_name, parent_symbol = "unknown", "", None
        if source is not None:
            symbol = _find_enclosing_symbol(source, file, line)
            if symbol is not None:
                symbol_kind, symbol_name, parent_symbol = symbol

        blame_commit, blame_author, blame_message = await _git_blame(sandbox_handle, file, line)

        attributions.append(
            Attribution(
                failure_id=test.node_id,
                file=file,
                line=line,
                symbol_kind=symbol_kind,
                symbol_name=symbol_name,
                parent_symbol=parent_symbol,
                blame_commit=blame_commit,
                blame_author=blame_author,
                blame_message=blame_message,
                confidence=confidence,
            )
        )
    return attributions
