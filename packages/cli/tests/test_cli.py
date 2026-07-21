"""Smoke tests for the CLI stub."""

from __future__ import annotations

from typer.testing import CliRunner

from ancora_cli.main import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "ancora" in result.stdout


def test_info_command() -> None:
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
