from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from openpyxl import load_workbook

from servicedesk.business_time import LONDON
from servicedesk.config import load_config
from servicedesk.measure import Period, add_months, describe, measure_all, month_bounds
from servicedesk.report import build_report
from servicedesk.sla import Targets, Ticket
from servicedesk.snow import ServiceNow
from servicedesk.sources import duration_seconds, load_from_servicenow, parse_time, tickets_for_report
from tests.fake_servicenow import FakeServiceNow

SEPTEMBER = datetime(2026, 9, 1, tzinfo=LONDON)


@pytest.fixture(scope="module")
def config() -> Any:
    return load_config()


def london(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=LONDON)


def test_months_are_uk_calendar_months() -> None:
    start, end = month_bounds("2026-12")
    assert (start, end) == (datetime(2026, 12, 1, tzinfo=LONDON), datetime(2027, 1, 1, tzinfo=LONDON))
    # 23:30 UTC on 30 September is already October in the UK (BST).
    assert month_bounds(datetime(2026, 9, 30, 23, 30, tzinfo=UTC))[0] == datetime(2026, 10, 1, tzinfo=LONDON)
    assert add_months(datetime(2026, 1, 1, tzinfo=LONDON), -1) == datetime(2025, 12, 1, tzinfo=LONDON)
    with pytest.raises(ValueError, match="2026-09"):
        month_bounds("September")


def test_servicenow_values() -> None:
    assert parse_time("2026-09-15 07:20:00") == datetime(2026, 9, 15, 7, 20, tzinfo=UTC)
    assert parse_time("") is None
    assert duration_seconds("1970-01-02 03:00:00") == 27 * 3600
    assert duration_seconds("") == 0
    assert describe(5400) == "1 h 30 min"
    assert describe(600) == "10 min"
    assert describe(7200) == "2 h"


def incident(number: str, opened: datetime, **fields: Any) -> Ticket:
    defaults: dict[str, Any] = {
        "priority": 3,
        "short_description": "Test",
        "assignment_group": "Service Desk",
    }
    return Ticket(kind="incident", number=number, opened_at=opened, **{**defaults, **fields})


def test_each_event_counts_in_its_own_month(config: Any) -> None:
    targets = Targets(config)
    late_friday = incident(
        "INC1",
        london(30, 17),
        work_start=london(30, 17, 30),
        resolved_at=london(2, 10, month=10),
        assignment_group="Desktop Support",
        reassignments=1,
    )
    quick = incident("INC2", london(10, 9), work_start=london(10, 9, 5), resolved_at=london(10, 9, 20))
    stuck = incident("INC3", london(1, 9), work_start=london(1, 9, 30))  # never resolved
    rejected = Ticket(
        kind="request",
        number="RITM1",
        opened_at=london(3, 9),
        priority=4,
        short_description="x",
        item="Shared drive access",
        state="cancelled",
        approval_set=london(3, 10),
        closed_at=london(3, 10),
    )
    now = london(15, 12, month=10)
    measured = measure_all([late_friday, quick, stuck, rejected], targets, now)
    september = Period(measured, SEPTEMBER, targets)
    october = Period(measured, add_months(SEPTEMBER, 1), targets)

    assert [m.ticket.number for m in september.logged("incident")] == ["INC3", "INC2", "INC1"]
    assert september.rate("response").total == 3  # all three were picked up in September
    assert september.rate("resolution").total == 1  # only INC2 was resolved in September
    assert october.rate("resolution").total == 1  # INC1 counts when it was resolved
    assert {m.ticket.number for m in september.open_at_end("incident")} == {"INC1", "INC3"}
    assert {m.ticket.number for m, _ in september.open_late()} == {"INC3"}  # 30 working hours passed
    assert september.first_contact().met == 1  # INC2: service desk, 15 minutes, never passed on
    assert september.rate("fulfilment").total == 0  # a rejected request is not measured
    assert [m.ticket.number for m in september.rejected()] == ["RITM1"]


def test_leavers_are_on_time_if_done_on_their_last_day(config: Any) -> None:
    targets = Targets(config)
    leaver = Ticket(
        kind="request",
        number="RITM2",
        opened_at=london(1, 9),
        priority=4,
        short_description="Leaver",
        item="Leaver",
        state="closed",
        approval_set=london(1, 9),
        due_at=london(14, 17, 30),
        closed_at=london(14, 17, 34),
    )
    (outcome,) = targets.evaluate(leaver, london(30, 12))
    assert outcome.target.on_date and outcome.met
    leaver.closed_at = london(15, 8, 4)
    (outcome,) = targets.evaluate(leaver, london(30, 12))
    assert outcome.breached and round(outcome.over_seconds) == 8 * 3600 + 4 * 60


@pytest.fixture(scope="module")
def sample(config: Any, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, Any]:
    tickets, extras = tickets_for_report(config, source="sample", month=SEPTEMBER)
    path = tmp_path_factory.mktemp("report") / "sla-report-2026-09.xlsx"
    headline = build_report(config, tickets, extras, month=SEPTEMBER, path=path)
    targets = Targets(config)
    return path, headline, Period(measure_all(tickets, targets, extras.as_at), SEPTEMBER, targets)


def test_sample_report_has_every_sheet(sample: tuple[Path, str, Any]) -> None:
    path, headline, _ = sample
    workbook = load_workbook(path)
    assert workbook.sheetnames == [
        "Summary",
        "Trend",
        "By priority",
        "Categories",
        "Teams",
        "Departments",
        "Requests",
        "Breaches",
        "Problems and changes",
        "Tickets",
        "Definitions",
    ]
    assert headline.startswith("September 2026: response ")


def test_summary_matches_the_measurements(sample: tuple[Path, str, Any]) -> None:
    path, _, period = sample
    summary = load_workbook(path)["Summary"]
    assert summary["A6"].value == "Incidents responded to within target"
    assert summary["B6"].value == pytest.approx(period.rate("response").value)
    assert summary["B7"].value == pytest.approx(period.rate("resolution").value)
    assert summary["D8"].value == "Missed"  # one leaver done the morning after their last day
    notes = [row[0].value for row in summary.iter_rows(min_row=17, max_col=1) if row[0].value]
    assert any("Nobody at the Civic Centre can sign in" in str(n) for n in notes)


def test_breaches_and_tickets_sheets(sample: tuple[Path, str, Any]) -> None:
    path, _, period = sample
    workbook = load_workbook(path)
    breaches = [row for row in workbook["Breaches"].iter_rows(min_row=5, values_only=True) if row[0]]
    assert len(breaches) == len(period.breaches()) + len(period.open_late())
    assert any(row[0].startswith("SIM-RITM") and row[3] == "Fulfilment" for row in breaches)
    tickets = workbook["Tickets"]
    assert tickets.tables["Tickets"].ref == f"A4:V{4 + len(period.active())}"
    problems = [row[0] for row in workbook["Problems and changes"].iter_rows(min_row=6, values_only=True)]
    assert "SIM-PRB-0001" in problems and "SIM-CHG-0001" in problems


def live_instance() -> FakeServiceNow:
    fake = FakeServiceNow()
    common = {
        "priority": "3",
        "category": "access",
        "subcategory": "lockout",
        "assignment_group.name": "Service Desk",
        "assigned_to.name": "Nadia Scott",
        "contact_type": "phone",
        "reassignment_count": "0",
        "problem_id.number": "",
    }
    fake.add(
        "incident",
        number="INC0010001",
        opened_at="2026-09-10 08:00:00",
        work_start="2026-09-10 08:05:00",
        resolved_at="2026-09-10 08:20:00",
        state="6",
        short_description="Locked out",
        correlation_id="",
        **{"caller_id.employee_number": "E10049", "caller_id.department.name": "Customer Services"},
        **common,
    )
    fake.add(
        "incident",
        number="INC0000001",
        opened_at="2026-09-10 08:00:00",
        state="1",
        short_description="Demo data",
        correlation_id="",
        **{"caller_id.employee_number": "", "caller_id.department.name": ""},
        **common,
    )
    fake.add(
        "incident",
        number="INC0010002",
        opened_at="2026-09-11 08:00:00",
        state="6",
        resolved_at="2026-09-11 09:00:00",
        short_description="[Smoke test]",
        correlation_id="SMOKE-1",
        **{"caller_id.employee_number": "E10049", "caller_id.department.name": "Customer Services"},
        **common,
    )
    first = fake.tables["incident"][0]["sys_id"]
    fake.add(
        "task_sla",
        task=first,
        has_breached="false",
        business_pause_duration="1970-01-01 00:30:00",
        **{"sla.target": "resolution", "sla.name": "Kestrel P3 resolution (30 working hours)"},
    )
    item = fake.add(
        "sc_req_item",
        number="RITM0010001",
        opened_at="2026-09-14 09:00:00",
        approval="approved",
        approval_set="2026-09-14 09:00:00",
        state="3",
        closed_at="2026-09-14 16:34:00",
        short_description="Leaver: Test Person",
        correlation_id="",
        **{
            "cat_item.name": "Leaver",
            "requested_for.employee_number": "E10048",
            "requested_for.department.name": "Customer Services",
        },
    )
    fake.add(
        "sc_task",
        request_item=item["sys_id"],
        due_date="2026-09-14 16:30:00",
        **{"assignment_group.name": "Identity and Access Automation"},
    )
    fake.add(
        "problem",
        number="PRB0040001",
        short_description="Lockouts",
        state="103",
        known_error="true",
        opened_at="2026-08-03 09:00:00",
        resolved_at="",
        **{"assignment_group.name": "Identity and Access"},
    )
    fake.add(
        "change_request",
        number="CHG0030001",
        type="normal",
        short_description="Wi-Fi",
        state="3",
        start_date="2026-09-09 06:00:00",
        end_date="2026-09-09 08:00:00",
        risk="4",
        close_code="successful",
    )
    return fake


def test_live_tickets_come_from_servicenow(config: Any) -> None:
    now = datetime(2026, 10, 1, 9, tzinfo=UTC)
    tickets, extras = load_from_servicenow(cast(ServiceNow, live_instance()), config, SEPTEMBER, now)
    numbers = sorted(t.number for t in tickets)
    assert numbers == ["INC0010001", "RITM0010001"]  # no demo data, no smoke test
    incident_ticket = next(t for t in tickets if t.kind == "incident")
    assert incident_ticket.paused_seconds == 1800
    assert incident_ticket.engine_breached == {"resolution": False}
    assert incident_ticket.department == "Customer Services"
    request = next(t for t in tickets if t.kind == "request")
    assert request.state == "closed" and request.automated
    assert request.due_at == datetime(2026, 9, 14, 16, 30, tzinfo=UTC)
    assert extras.problems[0].state == "Root cause analysis (known error)"
    assert extras.changes[0].outcome == "Successful" and extras.changes[0].risk == "Low"
    assert extras.source == "ServiceNow (live instance)"
    (outcome,) = Targets(config).evaluate(request, now)
    assert outcome.met  # disabled at 17:34 on the last day
