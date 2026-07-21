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
def lint(
    paths: list[str] = typer.Argument(None, help="Files or directories to check."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero if any issue is found."),
) -> None:
    """Warn on non-deterministic patterns in workflow code (best-effort)."""
    from ancora.lint import check_paths

    targets = paths or ["."]
    issues = check_paths(targets)
    if not issues:
        console.print("[green]No determinism issues found.[/green]")
        raise typer.Exit(code=0)
    for issue in issues:
        console.print(f"[yellow]{issue}[/yellow]")
    console.print(f"\n{len(issues)} issue(s).")
    raise typer.Exit(code=1 if strict else 0)


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
