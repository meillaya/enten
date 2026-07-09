"""Language-agnostic AST chunking backed by tree-sitter.

Supports Python and TypeScript/TSX. Chunks are deterministic: the same
source bytes always produce the same ``chunk_id``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tree_sitter import Language, Node, Parser, Tree

from enten.logging import get_logger

logger = get_logger(__name__)

MAX_LINES = 100_000
BINARY_EXTENSIONS = frozenset({
    ".pyc",
    ".pyo",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
})

Lang = Literal["python", "typescript"]


def _load_language(language: Lang) -> Language:
    if language == "python":
        import tree_sitter_python as tsp

        return Language(tsp.language())
    if language == "typescript":
        import tree_sitter_typescript as tst

        return Language(tst.language_typescript())
    raise ValueError(f"unsupported language: {language}")


def _load_tsx_language() -> Language:
    import tree_sitter_typescript as tst

    return Language(tst.language_tsx())


@dataclass
class Symbol:
    kind: str
    name: str
    line: int
    parent: str | None = None


@dataclass
class Chunk:
    chunk_id: str
    path: str
    lang: Lang
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    text: str
    symbols: list[Symbol] = field(default_factory=list)
    scope_kind: Literal["module", "function", "class", "block"] = "module"


def _detect_binary(data: bytes) -> bool:
    """Heuristic: presence of a null byte means binary."""
    return b"\x00" in data


def _read_file_bounded(path: Path, max_lines: int = MAX_LINES) -> bytes | None:
    """Stream-count lines; return full content only when under the threshold."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        logger.warning("skipping binary file by extension", path=str(path))
        return None

    try:
        with path.open("rb") as fh:
            lines: list[bytes] = []
            for count, line in enumerate(fh, start=1):
                if count > max_lines:
                    logger.warning(
                        "skipping oversized file",
                        path=str(path),
                        max_lines=max_lines,
                    )
                    return None
                lines.append(line)
    except OSError as exc:
        logger.warning("unable to read file", path=str(path), error=str(exc))
        return None

    data = b"".join(lines)
    if _detect_binary(data):
        logger.warning("skipping binary file", path=str(path))
        return None
    return data


def _read_git_blob(
    repo_path: Path,
    commit: str,
    rel_path: str,
    max_lines: int = MAX_LINES,
) -> bytes | None:
    """Read a file from ``commit:rel_path`` without loading huge blobs."""
    if Path(rel_path).suffix.lower() in BINARY_EXTENSIONS:
        logger.warning("skipping binary file by extension", path=rel_path, commit=commit)
        return None

    cmd = ["git", "-C", str(repo_path), "show", f"{commit}:{rel_path}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        lines: list[bytes] = []
        for count, raw in enumerate(proc.stdout, start=1):
            if count > max_lines:
                logger.warning(
                    "skipping oversized file from git blob",
                    path=rel_path,
                    commit=commit,
                    max_lines=max_lines,
                )
                return None
            lines.append(raw)
        proc.wait(timeout=30)
        if proc.returncode != 0:
            err = proc.stderr.read().decode("utf-8", errors="ignore")[:200]
            logger.warning("git show failed", path=rel_path, commit=commit, error=err)
            return None
    except OSError as exc:
        logger.warning("git show error", path=rel_path, commit=commit, error=str(exc))
        return None
    finally:
        if proc.poll() is None:
            proc.kill()

    data = b"".join(lines)
    if _detect_binary(data):
        logger.warning("skipping binary git blob", path=rel_path, commit=commit)
        return None
    return data


def _extensions(language: Lang) -> frozenset[str]:
    if language == "python":
        return frozenset({".py"})
    return frozenset({".ts", ".tsx"})


def _language_for_file(path: str, language: Lang) -> Language:
    if language == "typescript" and path.endswith(".tsx"):
        return _load_tsx_language()
    return _load_language(language)


def _identifier_from_node(source: bytes, node: Node) -> str | None:
    """Return the text of the first identifier child using byte offsets."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            return source[child.start_byte : child.end_byte].decode("utf-8")
    return None


def _node_name(source: bytes, node: Node) -> str | None:
    """Best-effort extraction of an identifier name from an AST node."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _node_name(source, child)
        return None
    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration", "lexical_declaration"):
                return _node_name(source, child)
        return None
    if node.type == "lexical_declaration":
        for child in node.children:
            if child.type == "variable_declarator":
                return _node_name(source, child)
        return None
    return _identifier_from_node(source, node)


def _node_line(node: Node) -> int:
    return node.start_point.row + 1


def _node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _chunk_id(text: str, start_byte: int, lang: Lang) -> str:
    payload = f"{text}|{start_byte}|{lang}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_chunk(
    source: bytes,
    path: str,
    lang: Lang,
    node: Node,
    scope_kind: Literal["module", "function", "class", "block"],
    symbols: list[Symbol],
) -> Chunk:
    text = _node_text(source, node)
    return Chunk(
        chunk_id=_chunk_id(text, node.start_byte, lang),
        path=path,
        lang=lang,
        start_line=_node_line(node),
        end_line=node.end_point.row + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        text=text,
        symbols=symbols,
        scope_kind=scope_kind,
    )


_DEFINITION_TYPES = frozenset({
    "function_definition",
    "class_definition",
    "function_declaration",
    "class_declaration",
    "method_definition",
    "export_statement",
    "lexical_declaration",
})


def _collect_symbols(
    source: bytes,
    node: Node,
    parent: str | None = None,
) -> list[Symbol]:
    """Return the named symbol represented by *node* itself, if any."""
    if node.type not in _DEFINITION_TYPES:
        return []
    name = _node_name(source, node)
    if name is None:
        return []
    kind = node.type.replace("_definition", "").replace("_declaration", "")
    return [Symbol(kind=kind, name=name, line=_node_line(node), parent=parent)]


def _python_definition_node(node: Node) -> Node:
    """Return the concrete function/class node, unwrapping decorators."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


def _python_scope_kind(node: Node) -> Literal["function", "class", "block"]:
    inner = _python_definition_node(node)
    if inner.type == "function_definition":
        return "function"
    if inner.type == "class_definition":
        return "class"
    return "block"


def _body_block(node: Node) -> Node | None:
    """Return the ``block``/``statement_block`` child of a scoped node."""
    inner = _python_definition_node(node)
    for child in inner.children:
        if child.type in ("block", "statement_block"):
            return child
    return None


def _chunk_python(source: bytes, path: str, tree: Tree) -> Iterator[Chunk]:
    root = tree.root_node
    for child in root.children:
        if child.type not in ("function_definition", "class_definition", "decorated_definition"):
            continue
        start_line = _node_line(child)
        end_line = child.end_point.row + 1
        if end_line - start_line <= 200:
            symbols = _collect_symbols(source, child)
            yield _make_chunk(source, path, "python", child, _python_scope_kind(child), symbols)
            continue

        body = _body_block(child)
        if body is None:
            symbols = _collect_symbols(source, child)
            yield _make_chunk(source, path, "python", child, _python_scope_kind(child), symbols)
            continue

        # Oversized body: split each direct statement into its own block chunk.
        parent_name = _node_name(source, child)
        for stmt in body.children:
            if stmt.type == "pass_statement":
                continue
            symbols = _collect_symbols(source, stmt, parent=parent_name)
            yield _make_chunk(source, path, "python", stmt, "block", symbols)


def _ts_scope_kind(node: Node) -> Literal["function", "class", "block"]:
    inner = node
    if node.type == "export_statement":
        for c in node.children:
            if c.type in (
                "class_declaration",
                "function_declaration",
                "lexical_declaration",
            ):
                inner = c
                break
    if inner.type == "class_declaration":
        return "class"
    if inner.type in ("function_declaration", "lexical_declaration"):
        return "function"
    return "block"


def _is_ts_chunkable(node: Node) -> bool:
    if node.type in (
        "class_declaration",
        "function_declaration",
        "interface_declaration",
        "type_alias_declaration",
    ):
        return True
    if node.type == "export_statement":
        decl = next(
            (
                c
                for c in node.children
                if c.type
                in ("class_declaration", "function_declaration", "lexical_declaration")
            ),
            None,
        )
        return decl is not None
    if node.type == "lexical_declaration":
        for declarator in node.children:
            if declarator.type == "variable_declarator":
                value = next(
                    (
                        c
                        for c in declarator.children
                        if c.type in ("arrow_function", "function_expression")
                    ),
                    None,
                )
                if value is not None:
                    return True
    return False


def _chunk_typescript(source: bytes, path: str, tree: Tree) -> Iterator[Chunk]:
    root = tree.root_node
    for child in root.children:
        if not _is_ts_chunkable(child):
            continue
        symbols = _collect_symbols(source, child)
        yield _make_chunk(source, path, "typescript", child, _ts_scope_kind(child), symbols)


def _chunk_file(source: bytes, path: str, language: Lang, parser: Parser) -> Iterator[Chunk]:
    tree = parser.parse(source)
    if language == "python":
        yield from _chunk_python(source, path, tree)
    else:
        yield from _chunk_typescript(source, path, tree)


def index_repo(repo_path: Path, language: Lang) -> list[Chunk]:
    extensions = _extensions(language)
    parser = Parser(_load_language(language))
    tsx_parser: Parser | None = None
    if language == "typescript":
        tsx_parser = Parser(_load_tsx_language())

    chunks: list[Chunk] = []
    for root, _dirs, files in os.walk(repo_path):
        for filename in files:
            path = Path(root) / filename
            if path.suffix.lower() not in extensions:
                continue
            source = _read_file_bounded(path)
            if source is None:
                continue
            p = tsx_parser if language == "typescript" and path.suffix.lower() == ".tsx" else parser
            chunks.extend(_chunk_file(source, str(path.relative_to(repo_path)), language, p))
    return chunks


def _changed_files(repo_path: Path, base_sha: str, head_sha: str) -> list[str]:
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        base_sha,
        head_sha,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "git diff failed",
            base_sha=base_sha,
            head_sha=head_sha,
            error=exc.stderr[:200],
        )
        return []
    except OSError as exc:
        logger.warning(
            "git diff error",
            base_sha=base_sha,
            head_sha=head_sha,
            error=str(exc),
        )
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def index_diff(repo_path: Path, language: Lang, base_sha: str, head_sha: str) -> list[Chunk]:
    extensions = _extensions(language)
    changed = _changed_files(repo_path, base_sha, head_sha)
    parser = Parser(_load_language(language))
    tsx_parser: Parser | None = None
    if language == "typescript":
        tsx_parser = Parser(_load_tsx_language())

    chunks: list[Chunk] = []
    for rel_path in changed:
        if Path(rel_path).suffix.lower() not in extensions:
            continue
        source = _read_git_blob(repo_path, head_sha, rel_path)
        if source is None:
            continue
        p = tsx_parser if language == "typescript" and rel_path.endswith(".tsx") else parser
        chunks.extend(_chunk_file(source, rel_path, language, p))
    return chunks


def chunk(repo_path: str | Path) -> list[Chunk]:
    repo_path = Path(repo_path)
    chunks: list[Chunk] = []
    for language in ("python", "typescript"):
        chunks.extend(index_repo(repo_path, language))
    return chunks
