"""Tests for the determinism lint (AN-022)."""

from __future__ import annotations

import textwrap

from ancora.lint import check_source

_WORKFLOW_HEADER = """
from ancora import Workflow, workflow

@workflow.defn(name="bad")
class Bad(Workflow):
    @workflow.run
    async def run(self, params: dict) -> dict:
"""


def _wf(body: str) -> str:
    # Dedent the raw block, then re-indent uniformly to the method body level.
    dedented = textwrap.dedent(body).strip("\n")
    indented = textwrap.indent(dedented, " " * 8)
    return _WORKFLOW_HEADER + indented + "\n"


def test_flags_wall_clock_read() -> None:
    src = _wf(
        """
        import datetime
        now = datetime.datetime.now()
        return {"now": str(now)}
        """
    )
    issues = check_source(src, "bad_workflow.py")
    codes = {i.code for i in issues}
    assert "AND001" in codes


def test_flags_forbidden_import() -> None:
    src = _wf(
        """
        import requests
        r = requests.get("http://x")
        return {"r": r.status_code}
        """
    )
    issues = check_source(src, "bad_workflow.py")
    assert any(i.code == "AND002" for i in issues)


def test_flags_random() -> None:
    src = _wf(
        """
        import random
        return {"n": random.random()}
        """
    )
    assert any(i.code == "AND001" for i in check_source(src, "bad.py"))


def test_ignores_non_workflow_modules() -> None:
    # A plain module (no @workflow.defn) must not be flagged — activities may
    # legitimately read the clock and do I/O.
    src = "import datetime\nx = datetime.datetime.now()\n"
    assert check_source(src, "activity.py") == []


def test_clean_workflow_has_no_issues() -> None:
    src = _wf(
        """
        greeting = params["name"]
        return {"message": greeting}
        """
    )
    assert check_source(src, "good.py") == []
