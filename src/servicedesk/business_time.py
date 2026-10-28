"""Working-hours arithmetic, the same way ServiceNow measures SLAs against a schedule.

A working-hours clock only runs 08:00 to 18:00 on working days (UK time), so a ticket logged at
17:30 on a Friday with a 4-hour target is due at 11:30 on Monday. A 24x7 clock always runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


class Clock(Protocol):
    @property
    def name(self) -> str: ...

    def seconds_between(self, start: datetime, end: datetime) -> float: ...

    def add(self, start: datetime, seconds: float) -> datetime: ...


@dataclass(frozen=True)
class AlwaysOn:
    name: str = "24x7"

    def seconds_between(self, start: datetime, end: datetime) -> float:
        return max(0.0, (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds())

    def add(self, start: datetime, seconds: float) -> datetime:
        return (start.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(start.tzinfo)


@dataclass(frozen=True)
class WorkingHours:
    start: time = time(8, 0)
    end: time = time(18, 0)
    days: frozenset[int] = frozenset({1, 2, 3, 4, 5})  # ISO weekdays, Monday = 1
    holidays: frozenset[date] = field(default_factory=frozenset)
    tz: ZoneInfo = LONDON
    name: str = "working hours"

    @property
    def day_seconds(self) -> float:
        return (datetime.combine(date.min, self.end) - datetime.combine(date.min, self.start)).total_seconds()

    def is_working_day(self, day: date) -> bool:
        return day.isoweekday() in self.days and day not in self.holidays

    def _window(self, day: date) -> tuple[datetime, datetime]:
        return datetime.combine(day, self.start, self.tz), datetime.combine(day, self.end, self.tz)

    def seconds_between(self, start: datetime, end: datetime) -> float:
        begin, finish = start.astimezone(self.tz), end.astimezone(self.tz)
        if finish <= begin:
            return 0.0
        total = 0.0
        day = begin.date()
        while day <= finish.date():
            if self.is_working_day(day):
                opens, closes = self._window(day)
                low, high = max(opens, begin), min(closes, finish)
                if high > low:
                    total += (high - low).total_seconds()
            day += timedelta(days=1)
        return total

    def add(self, start: datetime, seconds: float) -> datetime:
        """The moment when ``seconds`` of working time have passed after ``start``."""
        current = start.astimezone(self.tz)
        remaining = seconds
        day = current.date()
        for _ in range(3660):  # ten years of days is plenty
            if self.is_working_day(day):
                opens, closes = self._window(day)
                low = max(opens, current)
                if closes > low:
                    available = (closes - low).total_seconds()
                    if remaining <= available:
                        return low + timedelta(seconds=remaining)
                    remaining -= available
            day += timedelta(days=1)
            current = datetime.combine(day, time(0, 0), self.tz)
        raise ValueError("The schedule has no working time.")

    def next_open(self, moment: datetime) -> datetime:
        """``moment`` if the desk is open then, otherwise when it next opens."""
        return self.add(moment, 0)


def working_hours(
    start: str = "08:00",
    end: str = "18:00",
    days: Iterable[int] = (1, 2, 3, 4, 5),
    holidays: Iterable[date] = (),
) -> WorkingHours:
    return WorkingHours(
        start=time.fromisoformat(start),
        end=time.fromisoformat(end),
        days=frozenset(days),
        holidays=frozenset(holidays),
    )
