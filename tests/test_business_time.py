from datetime import UTC, date, datetime

from servicedesk.business_time import LONDON, AlwaysOn, working_hours
from servicedesk.config import load_config
from servicedesk.sla import Targets, Ticket

desk = working_hours(holidays=[date(2026, 8, 31)])


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=LONDON)  # type: ignore[misc]


def test_counts_only_working_hours() -> None:
    # Friday 17:00 to Monday 09:00: one hour on Friday, one on Monday.
    assert desk.seconds_between(at(2026, 10, 23, 17), at(2026, 10, 26, 9)) == 2 * 3600
    # Before opening and after closing count for nothing.
    assert desk.seconds_between(at(2026, 10, 26, 6), at(2026, 10, 26, 8)) == 0
    assert desk.seconds_between(at(2026, 10, 26, 18), at(2026, 10, 26, 23)) == 0


def test_skips_bank_holidays() -> None:
    # Friday 28 August 17:00 to Tuesday 1 September 09:00, Monday 31 August being a bank holiday.
    assert desk.seconds_between(at(2026, 8, 28, 17), at(2026, 9, 1, 9)) == 2 * 3600


def test_due_time_rolls_over_closed_days() -> None:
    # 4 working hours from Friday 16:00 is Monday 10:00.
    assert desk.add(at(2026, 10, 23, 16), 4 * 3600) == at(2026, 10, 26, 10)
    # Logged on a Saturday: the clock starts on Monday at 08:00.
    assert desk.add(at(2026, 10, 24, 11), 30 * 60) == at(2026, 10, 26, 8, 30)
    assert desk.next_open(at(2026, 10, 24, 11)) == at(2026, 10, 26, 8)


def test_working_day_across_the_clocks_going_back() -> None:
    # British Summer Time ended on Sunday 25 October 2026; Friday and Monday are both 10-hour days.
    assert desk.seconds_between(at(2026, 10, 23, 8), at(2026, 10, 26, 18)) == 20 * 3600


def test_always_on_counts_real_time_across_the_clocks_going_back() -> None:
    clock = AlwaysOn()
    start = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)  # 00:00 BST
    end = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)  # 23:00 GMT
    assert clock.seconds_between(start, end) == 24 * 3600


def test_tickets_are_measured_against_their_priority() -> None:
    targets = Targets(load_config())
    ticket = Ticket(
        kind="incident",
        number="INC1",
        opened_at=at(2026, 10, 23, 17),  # Friday
        priority=3,
        short_description="Monitor flickers",
        work_start=at(2026, 10, 26, 8, 45),  # 1h45 of working time later
        resolved_at=at(2026, 10, 29, 9),
        state="resolved",
    )
    response, resolution = targets.evaluate(ticket, now=at(2026, 11, 2, 9))
    assert response.elapsed == 1.75 * 3600
    assert response.met  # target 2 working hours
    assert resolution.elapsed == (1 + 30 + 1) * 3600  # Fri 1h + Mon, Tue, Wed 10h each + Thu 1h
    assert resolution.breached  # target 30 working hours


def test_open_tickets_are_measured_up_to_now_and_p1_runs_all_night() -> None:
    targets = Targets(load_config())
    ticket = Ticket("incident", "INC2", at(2026, 10, 24, 22), 1, "Nobody can sign in")
    response, resolution = targets.evaluate(ticket, now=at(2026, 10, 25, 1))
    assert not resolution.finished
    assert resolution.breached is False
    assert response.breached  # 3 hours with no response, on a 24x7 clock


def test_requests_are_measured_from_approval() -> None:
    targets = Targets(load_config())
    ticket = Ticket(
        kind="request",
        number="RITM1",
        opened_at=at(2026, 10, 19, 9),
        priority=4,
        short_description="Shared drive access",
        approval_set=at(2026, 10, 21, 9),
        closed_at=at(2026, 10, 21, 9, 5),
        state="closed",
    )
    (fulfilment,) = targets.evaluate(ticket, now=at(2026, 10, 22, 9))
    assert fulfilment.elapsed == 5 * 60
    assert fulfilment.met
