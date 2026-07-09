"""enten CLI entry point (scaffold).

The full command tree is introduced in later tasks (T12: run loop, T13:
plan/exec dispatch, etc.). This stub exists so that ``uv run enten --help``
and the ``[project.scripts]`` entry point resolve during initial bring-up.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="enten",
    help="enten - AI engineering agent.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def main() -> None:
    """Print the canonical greeting. Placeholder for the real CLI tree."""
    typer.echo("enten")


if __name__ == "__main__":
    app()
