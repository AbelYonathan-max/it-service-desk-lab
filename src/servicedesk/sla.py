"""Tickets and their SLA outcomes, measured from the tickets' own timestamps.

The report does not trust any one system: it measures every ticket against the targets in
servicenow/config/sla.yaml itself. For tickets that ServiceNow's SLA engine also measured, the
engine's verdict is kept alongside, so the two can be compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from servicedesk.business_time import LONDON, AlwaysOn, Clock, WorkingHours, working_hours
from servicedesk.config import Config


@dataclass
class Ticket:
    kind: str  # incident or request
    number: str
    opened_at: datetime
    priority: int
    short_description: str
    category: str = ""
    subcategory: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller_id: str = ""  # employee ID
    department: str = ""
    contact_type: str = ""
    state: str = "open"  # open, in_progress, on_hold, resolved, closed or cancelled
    work_start: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    paused_seconds: float = 0.0  # working time spent On Hold (from ServiceNow's SLA records)
    item: str = ""  # catalogue item, for requests
    approval_set: datetime | None = None
    due_at: datetime | None = None  # requests with a date of their own, such as a leaver's last day
    automated: bool = False
    reassignments: int = 0
    problem: str = ""  # the linked problem's number
    correlation_id: str = ""
    engine_breached: dict[str, bool] = field(default_factory=dict)

    @property
    def finished_at(self) -> datetime | None:
        return self.resolved_at if self.kind == "incident" else self.closed_at

    @property
    def simulated(self) -> bool:
        return self.correlation_id.startswith("SIM-")


@dataclass(frozen=True)
class Target:
    name: str
    measure: str  # response, resolution or fulfilment
    seconds: float
    clock: Clock
    priority: int | None = None
    on_date: bool = False  # met if done by the end of the ticket's due date, however long it took

    @property
    def hours(self) -> float:
        return self.seconds / 3600


@dataclass(frozen=True)
class Outcome:
    target: Target
    elapsed: float  # seconds on the target's clock; for an on-date target, seconds past the deadline
    finished: bool
    breached: bool
    due: datetime
    stopped: datetime | None = None  # when the clock stopped, if it has

    @property
    def met(self) -> bool:
        return self.finished and not self.breached

    @property
    def over_seconds(self) -> float:
        """How far past the target, on the target's clock."""
        return self.elapsed if self.target.on_date else max(0.0, self.elapsed - self.target.seconds)


class Targets:
    """The targets in sla.yaml, on the service desk's working-hours schedule."""

    def __init__(self, config: Config) -> None:
        schedule = config.schedule
        self.working: WorkingHours = working_hours(
            schedule["start"], schedule["end"], schedule["days"], (d for d, _ in config.holiday_dates())
        )
        self.always = AlwaysOn()
        self.kpi = {k: float(v) for k, v in config.sla["kpi_targets"].items()}
        self.incident: dict[int, dict[str, Target]] = {}
        self.labels: dict[int, str] = {}
        for entry in config.sla["incident"]:
            clock: Clock = self.always if entry["clock"] == "24x7" else self.working
            priority = int(entry["priority"])
            self.labels[priority] = entry["label"]
            self.incident[priority] = {
                "response": Target(
                    f"P{priority} response", "response", entry["response_minutes"] * 60.0, clock, priority
                ),
                "resolution": Target(
                    f"P{priority} resolution",
                    "resolution",
                    entry["resolution_hours"] * 3600.0,
                    clock,
                    priority,
                ),
            }
        request = config.sla["request"]
        self.fulfilment = Target(
            "Request fulfilment",
            "fulfilment",
            request["fulfilment_hours"] * 3600.0,
            self.always if request["clock"] == "24x7" else self.working,
        )
        self.dated_items = dict(request.get("dated_items", {}))
        self.on_date = Target("Done on the due date", "fulfilment", 0.0, self.always, on_date=True)
        first_contact = config.sla.get("first_contact", {})
        self.first_contact_group = str(first_contact.get("group", "Service Desk"))
        self.first_contact_within = timedelta(minutes=float(first_contact.get("within_minutes", 60)))

    def for_ticket(self, ticket: Ticket) -> list[Target]:
        if ticket.kind == "request":
            dated = ticket.item in self.dated_items and ticket.due_at is not None
            return [self.on_date if dated else self.fulfilment]
        return list(self.incident.get(ticket.priority, {}).values())

    def evaluate(self, ticket: Ticket, now: datetime) -> list[Outcome]:
        """Each target's outcome. A ticket still open is measured up to ``now``.

        Cancelled tickets, including rejected requests, are not measured: nothing was owed.
        """
        if ticket.state == "cancelled":
            return []
        outcomes = []
        for target in self.for_ticket(ticket):
            if target.on_date:
                outcomes.append(self._on_date(ticket, target, now))
                continue
            if target.measure == "response":
                start = ticket.opened_at
                stop = ticket.work_start or ticket.resolved_at or ticket.closed_at
                paused = 0.0
            elif target.measure == "resolution":
                start = ticket.opened_at
                stop = ticket.resolved_at or ticket.closed_at
                paused = ticket.paused_seconds
            else:
                # The fulfilment clock starts once the request is approved (or when logged, if no approval).
                start = ticket.approval_set or ticket.opened_at
                stop = ticket.closed_at
                paused = 0.0
            end = stop or now
            elapsed = max(0.0, target.clock.seconds_between(start, end) - paused)
            due = target.clock.add(start, target.seconds + paused)
            outcomes.append(Outcome(target, elapsed, stop is not None, elapsed > target.seconds, due, stop))
        return outcomes

    def _on_date(self, ticket: Ticket, target: Target, now: datetime) -> Outcome:
        if ticket.due_at is None:
            raise ValueError(f"{ticket.number} has no due date.")
        day = ticket.due_at.astimezone(LONDON).date()
        deadline = datetime.combine(day + timedelta(days=1), time(0, 0), LONDON)
        stop = ticket.closed_at
        end = stop or now
        late = self.always.seconds_between(deadline, end)
        return Outcome(target, late, stop is not None, end > deadline, deadline, stop)

    def first_contact(self, ticket: Ticket) -> bool:
        """Resolved by the service desk itself, never passed on, soon after it was logged."""
        return (
            ticket.kind == "incident"
            and ticket.resolved_at is not None
            and ticket.assignment_group == self.first_contact_group
            and ticket.reassignments == 0
            and ticket.resolved_at - ticket.opened_at <= self.first_contact_within
        )
