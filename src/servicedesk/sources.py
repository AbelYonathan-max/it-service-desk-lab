"""Where the monthly report's tickets come from: the simulated history, or a live ServiceNow instance.

From ServiceNow it reads incidents, requested items and their catalogue tasks, problems and changes,
and the SLA engine's own records (for time On Hold and the engine's verdict). Only tickets for the
council's staff count (employee numbers E1...): a new instance comes with demo data, which is left out,
as are the smoke test's tickets.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from servicedesk.config import Config
from servicedesk.measure import TREND_MONTHS, add_months, month_bounds
from servicedesk.sla import Ticket
from servicedesk.snow import ServiceNow, in_query

STAFF_ONLY = "STARTSWITHE1"  # Kestrel employee numbers; leaves out the demo data every new instance has

INCIDENT_STATES = {
    "1": "open",
    "2": "in_progress",
    "3": "on_hold",
    "6": "resolved",
    "7": "closed",
    "8": "cancelled",
}
ITEM_STATES = {"3": "closed", "4": "cancelled", "7": "cancelled", "2": "in_progress"}
PROBLEM_STATES = {
    "101": "New",
    "102": "Assess",
    "103": "Root cause analysis",
    "104": "Fix in progress",
    "106": "Resolved",
    "107": "Closed",
}
CHANGE_STATES = {
    "-5": "New",
    "-4": "Assess",
    "-3": "Authorise",
    "-2": "Scheduled",
    "-1": "Implement",
    "0": "Review",
    "3": "Closed",
    "4": "Cancelled",
}
RISKS = {"1": "Very high", "2": "High", "3": "Moderate", "4": "Low"}
CLOSE_CODES = {
    "successful": "Successful",
    "successful_issues": "Successful with issues",
    "unsuccessful": "Unsuccessful",
}


@dataclass(frozen=True)
class ProblemRow:
    number: str
    title: str
    state: str
    group: str
    opened_at: datetime
    resolved_at: datetime | None
    known_error: bool


@dataclass(frozen=True)
class ChangeRow:
    number: str
    type: str
    title: str
    state: str
    start: datetime | None
    end: datetime | None
    risk: str
    outcome: str


@dataclass
class Extras:
    problems: list[ProblemRow]
    changes: list[ChangeRow]
    as_at: datetime
    source: str


def tickets_for_report(
    config: Config, *, source: str | ServiceNow, month: datetime, seed: int | None = None
) -> tuple[list[Ticket], Extras]:
    """The tickets and records the report needs: from the simulated history, or from ServiceNow."""
    if isinstance(source, str):
        if source != "sample":
            raise ValueError(f"Unknown source {source!r}.")
        from servicedesk.simulate import generate

        history = generate(config, seed)
        problems = [
            ProblemRow(
                p.correlation_id,
                p.short_description,
                {"resolved": "Resolved", "known_error": "Known error"}.get(p.state, "Fix in progress"),
                p.group,
                p.opened_at,
                p.resolved_at,
                p.state == "known_error",
            )
            for p in history.problems
        ]
        changes = [
            ChangeRow(
                c.correlation_id,
                c.type.title(),
                c.short_description,
                "Closed" if c.state == "closed" else "Scheduled",
                c.start,
                c.end,
                c.risk.title(),
                CLOSE_CODES[c.outcome] if c.state == "closed" else "",
            )
            for c in history.changes
        ]
        label = "simulated history" + (f" (seed {seed})" if seed is not None else "")
        return history.tickets, Extras(problems, changes, history.as_at, label)
    return load_from_servicenow(source, config, month, datetime.now(UTC))


def parse_time(value: Any) -> datetime | None:
    """A ServiceNow date-time (always UTC through the API) as an aware datetime."""
    text = str(value or "").strip()
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) if text else None


def duration_seconds(value: Any) -> float:
    """A ServiceNow duration ("1970-01-02 03:00:00" is 27 hours) in seconds."""
    moment = parse_time(value)
    return (moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() if moment else 0.0


def _chunks(values: Sequence[str], size: int = 100) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def load_from_servicenow(
    sn: ServiceNow, config: Config, month: datetime, now: datetime
) -> tuple[list[Ticket], Extras]:
    """Everything active during the trend window. Queries are a day wider than needed, because
    the instance may read dates in its own time zone; the report then filters to the exact month."""
    first, end = add_months(month_bounds(month)[0], -(TREND_MONTHS - 1)), month_bounds(month)[1]
    since = (first - timedelta(days=1)).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    until = (end + timedelta(days=1)).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

    incidents = sn.query(
        "incident",
        f"opened_at<{until}^caller_id.employee_number{STAFF_ONLY}^resolved_atISEMPTY^ORresolved_at>={since}",
        fields=[
            "sys_id",
            "number",
            "opened_at",
            "priority",
            "short_description",
            "category",
            "subcategory",
            "assignment_group.name",
            "assigned_to.name",
            "caller_id.employee_number",
            "caller_id.department.name",
            "contact_type",
            "state",
            "work_start",
            "resolved_at",
            "closed_at",
            "reassignment_count",
            "correlation_id",
            "problem_id.number",
        ],
        order_by="sys_id",
    )
    items = sn.query(
        "sc_req_item",
        f"opened_at<{until}^requested_for.employee_number{STAFF_ONLY}^closed_atISEMPTY^ORclosed_at>={since}",
        fields=[
            "sys_id",
            "number",
            "opened_at",
            "cat_item.name",
            "short_description",
            "requested_for.employee_number",
            "requested_for.department.name",
            "approval",
            "approval_set",
            "state",
            "closed_at",
            "correlation_id",
        ],
        order_by="sys_id",
    )
    tickets: dict[str, Ticket] = {}
    for row in incidents:
        if str(row.get("correlation_id", "")).startswith("SMOKE-"):
            continue
        opened = parse_time(row["opened_at"])
        if opened is None:
            continue
        tickets[row["sys_id"]] = Ticket(
            kind="incident",
            number=row["number"],
            opened_at=opened,
            priority=int(row.get("priority") or 5),
            short_description=row.get("short_description", ""),
            category=row.get("category", ""),
            subcategory=row.get("subcategory", ""),
            assignment_group=row.get("assignment_group.name", ""),
            assigned_to=row.get("assigned_to.name", ""),
            caller_id=row.get("caller_id.employee_number", ""),
            department=row.get("caller_id.department.name", ""),
            contact_type=row.get("contact_type", ""),
            state=INCIDENT_STATES.get(str(row.get("state")), "open"),
            work_start=parse_time(row.get("work_start")),
            resolved_at=parse_time(row.get("resolved_at")),
            closed_at=parse_time(row.get("closed_at")),
            reassignments=int(row.get("reassignment_count") or 0),
            problem=row.get("problem_id.number", ""),
            correlation_id=row.get("correlation_id", ""),
        )
    automation = config.catalog["automation_group"]
    for row in items:
        if str(row.get("correlation_id", "")).startswith("SMOKE-"):
            continue
        opened = parse_time(row["opened_at"])
        if opened is None:
            continue
        tickets[row["sys_id"]] = Ticket(
            kind="request",
            number=row["number"],
            opened_at=opened,
            priority=4,
            short_description=row.get("short_description", ""),
            category="request",
            caller_id=row.get("requested_for.employee_number", ""),
            department=row.get("requested_for.department.name", ""),
            contact_type="self-service",
            state=ITEM_STATES.get(str(row.get("state")), "open"),
            closed_at=parse_time(row.get("closed_at")),
            item=row.get("cat_item.name", ""),
            approval_set=parse_time(row.get("approval_set"))
            if row.get("approval") in ("approved", "rejected")
            else None,
            correlation_id=row.get("correlation_id", ""),
        )

    item_ids = [sys_id for sys_id, t in tickets.items() if t.kind == "request"]
    task_groups: dict[str, list[str]] = defaultdict(list)
    for batch in _chunks(item_ids):
        for task in sn.query(
            "sc_task",
            in_query("request_item", list(batch)),
            fields=["request_item", "assignment_group.name", "due_date"],
        ):
            ticket = tickets[task["request_item"]]
            task_groups[task["request_item"]].append(task.get("assignment_group.name", ""))
            due = parse_time(task.get("due_date"))
            if due and (ticket.due_at is None or due < ticket.due_at):
                ticket.due_at = due
    for sys_id, groups in task_groups.items():
        ticket = tickets[sys_id]
        ticket.assignment_group = groups[-1]
        ticket.automated = ticket.state == "closed" and all(g == automation for g in groups)

    for batch in _chunks(list(tickets)):
        for sla in sn.query(
            "task_sla",
            in_query("task", list(batch)) + "^sla.nameSTARTSWITHKestrel ",
            fields=["task", "sla.target", "has_breached", "business_pause_duration"],
        ):
            ticket = tickets[sla["task"]]
            measure = sla.get("sla.target", "")
            if ticket.kind == "request":
                measure = "fulfilment"
            ticket.engine_breached[measure] = str(sla.get("has_breached")) == "true"
            if measure == "resolution":
                ticket.paused_seconds = duration_seconds(sla.get("business_pause_duration"))

    problems = [
        ProblemRow(
            row["number"],
            row.get("short_description", ""),
            PROBLEM_STATES.get(str(row.get("state")), str(row.get("state")))
            + (" (known error)" if str(row.get("known_error")) == "true" else ""),
            row.get("assignment_group.name", ""),
            parse_time(row["opened_at"]) or now,
            parse_time(row.get("resolved_at")),
            str(row.get("known_error")) == "true",
        )
        for row in sn.query(
            "problem",
            f"opened_at<{until}^resolved_atISEMPTY^ORresolved_at>={since}",
            fields=[
                "number",
                "short_description",
                "state",
                "known_error",
                "assignment_group.name",
                "opened_at",
                "resolved_at",
            ],
        )
    ]
    changes = [
        ChangeRow(
            row["number"],
            str(row.get("type", "")).title(),
            row.get("short_description", ""),
            CHANGE_STATES.get(str(row.get("state")), str(row.get("state"))),
            parse_time(row.get("start_date")),
            parse_time(row.get("end_date")),
            RISKS.get(str(row.get("risk")), ""),
            CLOSE_CODES.get(str(row.get("close_code")), str(row.get("close_code") or "")),
        )
        for row in sn.query(
            "change_request",
            f"start_date<{until}^end_date>={since}",
            fields=[
                "number",
                "type",
                "short_description",
                "state",
                "start_date",
                "end_date",
                "risk",
                "close_code",
            ],
        )
    ]
    return list(tickets.values()), Extras(problems, changes, now, "ServiceNow (live instance)")
