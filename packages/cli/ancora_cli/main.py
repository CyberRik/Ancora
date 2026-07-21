"""Ancora CLI entrypoint (Phase 0 stub).

Commands beyond version/info are placeholders that will be filled in as the
runtime lands. Kept intentionally thin so `ancora --help` is real from day one.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ancora import __version__

app = typer.Typer(
    name="ancora",
    help="Ancora — a fault-tolerant runtime for durable AI workflows.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the Ancora version."""
    console.print(f"ancora {__version__}")


@app.command()
def info() -> None:
    """Show CLI + environment info."""
    table = Table(title="Ancora", show_header=False)
    table.add_row("version", __version__)
    table.add_row("phase", "0 — walking skeleton")
    table.add_row("docs", "docs/IMPLEMENTATION-PLAN.md")
    console.print(table)


@app.command()
def dev() -> None:
    """Run the local dev runtime (placeholder until Phase 1)."""
    console.print(
        "[yellow]`ancora dev` is not implemented yet.[/yellow] "
        "It will boot an in-process Temporal + local Ray in Phase 1."
    )
    raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
