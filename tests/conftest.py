import pytest

from servicedesk import snow


@pytest.fixture(autouse=True)
def jobs_never_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake instance runs a one-off job as soon as it is created, so a test that waits for one
    has found a bug: it fails at once instead of waiting out the five-minute timeout."""

    def refuse(_: float) -> None:
        raise AssertionError("A one-off ServiceNow job did not do its work.")

    assert snow.run_once.__kwdefaults__ is not None
    monkeypatch.setitem(snow.run_once.__kwdefaults__, "sleep", refuse)
