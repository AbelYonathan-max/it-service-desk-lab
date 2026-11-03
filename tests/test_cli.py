"""The sdlab command line: what it prints, what it writes and the exit codes the workflows rely on."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from servicedesk import cli
from servicedesk.snow import InstanceAsleepError, ServiceNowError


def test_sample_report_writes_the_workbook_and_the_job_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "report.xlsx"
    assert cli.main(["report", "2026-09", "--sample", "--out", str(out)]) == 0
    assert f"Wrote {out}." in capsys.readouterr().out
    assert "Summary" in load_workbook(out).sheetnames
    assert summary.read_text(encoding="utf-8").startswith("### SLA report September 2026")


def test_servicenow_commands_need_a_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("SERVICENOW_INSTANCE", "SERVICENOW_PASSWORD", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["sn", "check"]) == 2
    assert "set SERVICENOW_INSTANCE and SERVICENOW_PASSWORD" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "code", "prefix"),
    [
        (ServiceNowError("refused"), 2, "::error::"),
        (InstanceAsleepError("asleep"), 3, "::warning::"),
    ],
)
def test_errors_become_exit_codes_and_annotations_in_github_actions(
    error: ServiceNowError,
    code: int,
    prefix: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> None:
        raise error

    monkeypatch.setattr(cli, "_connect", fail)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cli.main(["sn", "check"]) == code
    assert capsys.readouterr().err.strip() == f"{prefix}{error}"
