"""Measuring a month of tickets: the figures behind the monthly SLA report.

Each event counts in the month (UK time) it happened: a ticket is logged in one month, may be
responded to in the same month, and resolved in the next. Clocks that stop in a month count in
that month; clocks still running at month end that are already past target are "open and late".
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from servicedesk.business_time import LONDON
from servicedesk.sla import Outcome, Target, Targets, Ticket

TREND_MONTHS = 6


# -- months ------------------------------------------------------------------------------------


def add_months(start: datetime, months: int) -> datetime:
    index = start.year * 12 + start.month - 1 + months
    return datetime.combine(date(index // 12, index % 12 + 1, 1), time(0), LONDON)


def month_bounds(month: str | date | datetime) -> tuple[datetime, datetime]:
    """Midnight (UK time) at the start of the month and at the start of the next."""
    if isinstance(month, str):
        try:
            year, number = (int(part) for part in month.split("-"))
            first = date(year, number, 1)
        except ValueError as error:
            raise ValueError(f"A month looks like 2026-09, not {month!r}.") from error
    elif isinstance(month, datetime):
        first = month.astimezone(LONDON).date().replace(day=1)
    else:
        first = month.replace(day=1)
    start = datetime.combine(first, time(0), LONDON)
    return start, add_months(start, 1)


# -- measuring a month -------------------------------------------------------------------------


@dataclass(frozen=True)
class Rate:
    met: int
    total: int

    @property
    def value(self) -> float | None:
        return self.met / self.total if self.total else None

    def __str__(self) -> str:
        return "no tickets" if self.value is None else f"{self.value:.1%} ({self.met} of {self.total})"


@dataclass
class Measured:
    ticket: Ticket
    outcomes: list[Outcome]

    def outcome(self, measure: str) -> Outcome | None:
        return next((o for o in self.outcomes if o.target.measure == measure), None)

    @property
    def ended_at(self) -> datetime | None:
        """When it stopped needing work: resolved, completed, rejected or cancelled."""
        return self.ticket.finished_at or self.ticket.closed_at


class Period:
    """One calendar month, UK time."""

    def __init__(self, measured: list[Measured], start: datetime, targets: Targets) -> None:
        self.start, self.end = month_bounds(start)
        self.measured = measured
        self.targets = targets

    @property
    def label(self) -> str:
        return f"{self.start:%B %Y}"

    def within(self, moment: datetime | None) -> bool:
        return moment is not None and self.start <= moment < self.end

    def kind(self, kind: str) -> list[Measured]:
        return [m for m in self.measured if m.ticket.kind == kind]

    def logged(self, kind: str) -> list[Measured]:
        return [m for m in self.kind(kind) if self.within(m.ticket.opened_at)]

    def resolved(self) -> list[Measured]:
        return [m for m in self.kind("incident") if self.within(m.ticket.resolved_at)]

    def completed(self) -> list[Measured]:
        return [
            m for m in self.kind("request") if m.ticket.state == "closed" and self.within(m.ticket.closed_at)
        ]

    def rejected(self) -> list[Measured]:
        return [
            m
            for m in self.kind("request")
            if m.ticket.state == "cancelled" and self.within(m.ticket.closed_at)
        ]

    def open_at_end(self, kind: str) -> list[Measured]:
        return [
            m
            for m in self.kind(kind)
            if m.ticket.opened_at < self.end and (m.ended_at is None or m.ended_at >= self.end)
        ]

    def active(self) -> list[Measured]:
        """Everything logged before the month ended and not finished before it began."""
        return [
            m
            for m in self.measured
            if m.ticket.opened_at < self.end and (m.ended_at is None or m.ended_at >= self.start)
        ]

    def outcomes(
        self, measure: str, where: Callable[[Measured], bool] | None = None
    ) -> list[tuple[Measured, Outcome]]:
        """Outcomes whose clock stopped in the month."""
        found = []
        for m in self.measured:
            o = m.outcome(measure)
            if o and o.finished and self.within(o.stopped) and (where is None or where(m)):
                found.append((m, o))
        return found

    def rate(self, measure: str, where: Callable[[Measured], bool] | None = None) -> Rate:
        found = self.outcomes(measure, where)
        return Rate(sum(o.met for _, o in found), len(found))

    def breaches(self) -> list[tuple[Measured, Outcome]]:
        return [
            (m, o)
            for measure in ("response", "resolution", "fulfilment")
            for m, o in self.outcomes(measure)
            if o.breached
        ]

    def open_late(self) -> list[tuple[Measured, Outcome]]:
        """Clocks still running when the month ended that had already passed their target."""
        found = []
        for m in self.measured:
            if m.ticket.opened_at >= self.end:
                continue
            for o in m.outcomes:
                if (o.stopped is None or o.stopped >= self.end) and o.due < self.end:
                    found.append((m, o))
        return found

    def first_contact(self) -> Rate:
        resolved = self.resolved()
        return Rate(sum(self.targets.first_contact(m.ticket) for m in resolved), len(resolved))

    def automation(self) -> Rate:
        done = self.completed()
        return Rate(sum(m.ticket.automated for m in done), len(done))

    def kpis(self) -> dict[str, Rate]:
        return {
            "response": self.rate("response"),
            "resolution": self.rate("resolution"),
            "fulfilment": self.rate("fulfilment"),
            "first_contact": self.first_contact(),
            "automation": self.automation(),
        }


def measure_all(tickets: Iterable[Ticket], targets: Targets, now: datetime) -> list[Measured]:
    return [Measured(t, targets.evaluate(t, now)) for t in sorted(tickets, key=lambda t: t.opened_at)]


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def percentile_90(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return values[0] if values else None
    return statistics.quantiles(values, n=10, method="inclusive")[-1]


def describe(seconds: float) -> str:
    """5400 -> '1 h 30 min'."""
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def target_text(target: Target, due: datetime | None = None) -> str:
    if target.on_date:
        if due is None:
            return "on the due date"
        day = (due - timedelta(seconds=1)).astimezone(LONDON)
        return f"by the end of {day.day} {day:%b}"
    clock = "24x7" if target.clock.name == "24x7" else "working"
    if target.seconds < 3600:
        return f"{target.seconds / 60:.0f} min ({clock})"
    return f"{target.hours:g} h ({clock})"
