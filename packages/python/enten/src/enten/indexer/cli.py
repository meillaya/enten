"""CLI for the tree-sitter indexer.

Example:
    python -m enten.indexer ./my-repo --lang=python
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer

from enten.indexer import index_repo

app = typer.Typer(
    name="enten.indexer",
    help="Tree-sitter multi-language indexer.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def main(
    path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, dir_okay=True, readable=True
    ),
    lang: Literal["python", "typescript"] = typer.Option("python", "--lang"),  # noqa: B008
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
) -> None:
    """Index *path* and print a JSON summary of the chunks."""
    chunks = index_repo(str(path), lang)
    summary = {
        "path": str(path),
        "lang": lang,
        "chunks": len(chunks),
        "files": sorted({c.path for c in chunks}),
    }
    if output:
        output.write_text(
            json.dumps([c.model_dump() for c in chunks], indent=2),
            encoding="utf-8",
        )
        typer.echo(f"wrote {len(chunks)} chunks to {output}")
    else:
        typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
