from collections import Counter
from datetime import date, datetime
from typing import Any, cast

import pytest

from servicedesk.apply import apply_all
from servicedesk.business_time import LONDON
from servicedesk.config import load_config
from servicedesk.measure import Period, measure_all
from servicedesk.simulate import AS_AT, ON_LEAVE, SHORT_STAFFED, History, generate, write_history
from servicedesk.sla import Targets
from servicedesk.snow import ServiceNow
from tests.fake_servicenow import FakeServiceNow, base_instance


@pytest.fixture(scope="module")
def config() -> Any:
    return load_config()


@pytest.fixture(scope="module")
def history(config: Any) -> History:
    return generate(config)


def key(history: History) -> list[tuple[Any, ...]]:
    return [(t.number, t.opened_at, t.resolved_at, t.closed_at, t.assignment_group) for t in history.tickets]


def test_the_same_seed_gives_the_same_history(config: Any, history: History) -> None:
    assert key(generate(config)) == key(history)
    assert key(generate(config, seed=1)) != key(history)


def test_numbers_follow_the_order_tickets_were_logged(history: History) -> None:
    for records in (history.incidents, history.requests):
        numbers = [r.ticket.number for r in records]
        assert numbers == sorted(numbers)
        opened = [r.ticket.opened_at for r in records]
        assert opened == sorted(opened)
    assert all(t.correlation_id.startswith("SIM-") for t in history.tickets)
    assert all(t.opened_at.microsecond == 0 for t in history.tickets)


def test_nothing_happens_after_the_as_at_time(history: History) -> None:
    for t in history.tickets:
        assert t.opened_at < AS_AT
        for moment in (t.work_start, t.resolved_at, t.closed_at, t.approval_set):
            assert moment is None or moment <= AS_AT


def test_callers_are_staff_on_the_day(config: Any, history: History) -> None:
    people = {p.employee_id: p for p in config.org.people}
    for record in history.incidents:
        person = people[record.caller]
        assert person.start_date <= record.ticket.opened_at.date()


def test_analysts_on_leave_get_no_tickets(history: History) -> None:
    for record in history.incidents:
        if SHORT_STAFFED[0] <= record.ticket.opened_at.date() <= SHORT_STAFFED[1]:
            assert record.assignee not in ON_LEAVE


def test_the_major_incidents_tell_their_story(history: History) -> None:
    p1 = [r for r in history.incidents if r.ticket.priority == 1]
    assert [r.ticket.opened_at.date() for r in p1] == [date(2026, 8, 27), date(2026, 9, 15)]
    email, civic = p1
    targets = Targets(load_config())
    assert [o.met for o in targets.evaluate(email.ticket, AS_AT)] == [False, False]
    assert [o.met for o in targets.evaluate(civic.ticket, AS_AT)] == [True, True]
    assert civic.caused_by == "SIM-CHG-0001"
    assert civic.ticket.problem == "SIM-PRB-0001"
    children = [r for r in history.incidents if r.parent == civic.ticket.number]
    assert len(children) == 9
    assert all(r.ticket.problem == "SIM-PRB-0001" for r in children)
    failed = next(c for c in history.changes if c.correlation_id == "SIM-CHG-0001")
    assert failed.outcome == "unsuccessful"


def test_august_dips_while_two_analysts_are_away(config: Any, history: History) -> None:
    targets = Targets(config)
    measured = measure_all(history.tickets, targets, AS_AT)
    rates = {
        month: Period(measured, datetime(2026, month, 1, tzinfo=LONDON), targets).rate("response").value
        for month in (7, 8, 9)
    }
    assert rates[8] is not None and rates[7] is not None and rates[9] is not None
    assert rates[8] < rates[7] - 0.05
    assert rates[8] < rates[9] - 0.05


def test_requests_cover_starters_leavers_moves_and_shares(config: Any, history: History) -> None:
    items = Counter(r.ticket.item for r in history.requests)
    assert set(items) == {"New starter", "Leaver", "Change of role or department", "Shared drive access"}
    starters = {
        r.ticket.short_description.split(":")[1].split(",")[0].strip()
        for r in history.requests
        if r.ticket.item == "New starter"
    }
    joined = {p.name for p in config.org.people if date(2026, 7, 1) < p.start_date <= date(2026, 10, 23)}
    assert joined <= starters
    leavers = [r.ticket for r in history.requests if r.ticket.item == "Leaver"]
    assert all(t.due_at is not None and t.due_at.hour == 17 for t in leavers)
    late = [t for t in leavers if t.closed_at and t.due_at and t.closed_at.date() > t.due_at.date()]
    assert len(late) == 1 and late[0].closed_at is not None and late[0].closed_at.month == 9
    assert any(r.rejected for r in history.requests)
    assert all(not r.ticket.automated for r in history.requests if r.ticket.state != "closed")


def loaded_instance(config: Any) -> FakeServiceNow:
    fake = base_instance()
    apply_all(cast(ServiceNow, fake), config, dry_run=False)
    fake.add(
        "sys_choice",
        name="incident",
        element="close_code",
        value="Solution provided",
        language="en",
        inactive="false",
        sequence="10",
    )
    return fake


def test_history_loads_into_servicenow_once(config: Any, history: History) -> None:
    fake = loaded_instance(config)
    summary = write_history(cast(ServiceNow, fake), config, history, progress=lambda _: None)
    assert summary.startswith(
        f"{len(history.incidents)} incidents, {len(history.requests)} requests, 3 problems"
    )
    tables = fake.tables
    sim = {t: [r for r in tables[t] if str(r.get("correlation_id", "")).startswith("SIM-")] for t in tables}
    assert len(sim["incident"]) == len(history.incidents)
    assert len(sim["sc_req_item"]) == len(sim["sc_request"]) == len(history.requests)
    assert len(sim["change_request"]) == len(history.changes)

    ids = {
        r["correlation_id"]: r["sys_id"] for t in ("incident", "problem", "change_request") for r in sim[t]
    }
    civic = next(r for r in history.incidents if r.ticket.priority == 1 and r.ticket.opened_at.month == 9)
    record = next(r for r in sim["incident"] if r["correlation_id"] == civic.ticket.number)
    assert record["caused_by"] == ids["SIM-CHG-0001"]
    assert record["problem_id"] == ids["SIM-PRB-0001"]
    assert record["opened_at"] == "2026-09-15 07:20:00"  # 08:20 BST, stored in UTC
    child = next(r for r in sim["incident"] if r.get("parent_incident"))
    assert child["parent_incident"] == record["sys_id"]

    leaver = next(r for r in history.requests if r.ticket.item == "Leaver" and r.ticket.state == "closed")
    item = next(r for r in sim["sc_req_item"] if r["correlation_id"] == leaver.ticket.number)
    task = next(r for r in sim["sc_task"] if r["request_item"] == item["sys_id"])
    assert task["due_date"].endswith(":30:00")
    approvals = [
        r
        for r in tables["sysapproval_approver"]
        if r["sysapproval"] in {i["sys_id"] for i in sim["sc_req_item"]}
    ]
    assert len(approvals) == sum(1 for r in history.requests if r.approver)

    writes = len(fake.writes)
    again = write_history(cast(ServiceNow, fake), config, history, progress=lambda _: None)
    assert again.startswith("0 incidents, 0 requests, 0 problems and 0 changes")
    assert len(fake.writes) == writes
