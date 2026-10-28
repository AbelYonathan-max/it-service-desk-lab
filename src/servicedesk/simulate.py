"""Made-up but realistic service desk history: 1 July to 23 October 2026.

The generator is seeded, so the same seed always produces the same tickets: the sample report in
docs/reports and a live instance loaded with ``sdlab sn history`` show the same numbers.

What is in it, so the report has a story to tell:
- about 3 incidents per working day, busiest on Monday mornings, mostly password, access, Microsoft
  365 and hardware problems, with business application and network faults that take longer;
- 17 to 28 August: two analysts on leave, so responses and fixes are slower and SLA compliance dips
  (and a P1 email outage breaches its 4-hour target);
- 15 September: a standard change to the DNS forwarders fails and causes a P1 major incident
  (nobody at the Civic Centre can sign in), with related incidents, an emergency change to put it
  right, and a problem record that changes how DNS changes are made;
- repeated lockouts after password changes: a known error with a problem record and KB article;
- poor Wi-Fi at Riverside Depot: a problem fixed by a normal change through the CAB;
- starters (the people in lab/config/people.csv who joined in the period), leavers, moves and
  shared drive requests, approved and mostly carried out by the fulfilment agent within minutes;
  a few handed to a person, one leaver done a day late, and a few rejected by the data owner.

Every record carries a correlation ID starting SIM-, which the SLA definitions ignore (their
timings are back-dated, so the SLA engine cannot measure them) and the report measures itself.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from servicedesk.business_time import LONDON, WorkingHours
from servicedesk.config import Config
from servicedesk.people import Person
from servicedesk.sla import Targets, Ticket
from servicedesk.snow import ServiceNow, ServiceNowError, in_query

DEFAULT_SEED = 15  # gives the history described above; other seeds tell the same story with other numbers
PERIOD_START = date(2026, 7, 1)
AS_AT = datetime(2026, 10, 23, 17, 0, tzinfo=LONDON)
SHORT_STAFFED = (date(2026, 8, 10), date(2026, 8, 28))
ON_LEAVE = {"E10035", "E10036"}  # a service desk analyst and a desktop engineer, during SHORT_STAFFED

ANALYSTS = {
    "Service Desk": ["E10031", "E10035"],
    "Desktop Support": ["E10032", "E10036"],
    "Infrastructure": ["E10033", "E10037"],
    "Business Applications": ["E10034"],
    "Identity and Access": ["E10031", "E10035", "E10033"],
}
# fmt: off
IMPACT_URGENCY = {1: [(1, 1)], 2: [(1, 2), (2, 1)], 3: [(2, 2), (1, 3), (3, 1)], 4: [(2, 3), (3, 2)], 5: [(3, 3)]}
CONTACT_TYPES = [("phone", 45), ("self-service", 30), ("email", 15), ("walk-in", 10)]
HOURS = [(8, 2.0), (9, 2.2), (10, 1.6), (11, 1.3), (12, 0.8), (13, 1.0), (14, 1.2), (15, 1.1), (16, 0.9), (17, 0.5)]
WEEKDAY_LOAD = {0: 1.3, 1: 1.1, 2: 1.0, 3: 0.95, 4: 0.75}
# fmt: on

CIVIC_DNS, LOCKOUTS, RIVERSIDE_WIFI = "SIM-PRB-0001", "SIM-PRB-0002", "SIM-PRB-0003"


@dataclass(frozen=True)
class Timing:
    """How long things take. Times are log-normal: a median and a spread (sigma)."""

    response_share: float = 0.28  # median time to pick a ticket up, as a share of its response target
    response_sigma: float = 0.8
    queue_hours: float = 3.0  # median working hours in a resolver group's queue after being passed on
    queue_sigma: float = 1.0
    fix_sigma: float = 0.9  # spread of the fix time around the template's typical hours
    short_staffed: float = 2.5  # everything takes this many times longer while two analysts are on leave


@dataclass(frozen=True)
class Template:
    short: str
    category: str
    subcategory: str
    group: str
    priorities: tuple[int, ...]
    weight: float
    first_contact: float  # chance the service desk fixes it on the first contact
    hours: float  # typical working hours to fix when it is not fixed at first contact
    fix: str
    departments: tuple[str, ...] = ()  # only callers from these department codes
    problem: str = ""  # correlation ID of the problem these incidents link to


# fmt: off
TEMPLATES = [
    Template("Account locked out", "access", "lockout", "Service Desk", (3, 4), 9, 0.85, 0.5,
             "Unlocked the account after checking the caller's identity."),
    Template("Account keeps locking after password change", "access", "lockout", "Service Desk", (3,), 4, 0.6, 1.0,
             "Found the source of the bad passwords (mail app on phone); caller removed and re-added the account.",
             problem=LOCKOUTS),
    Template("Forgotten password", "access", "password", "Service Desk", (3, 4), 7, 0.95, 0.3,
             "Identity checked; temporary password set, to be changed at sign-in. Pointed to self-service reset."),
    Template("New phone: cannot approve sign-in requests", "access", "mfa", "Service Desk", (3,), 3, 0.7, 1.0,
             "Issued a temporary access pass; caller registered Authenticator on the new phone."),
    Template("Cannot open a folder on the {share} shared drive", "access", "permissions", "Identity and Access", (4,),
             3, 0.1, 6.0, "Caller needed access, not a fault: raised a Shared drive access request for the data owner."),
    Template("Laptop will not turn on", "hardware", "laptop", "Desktop Support", (3,), 3, 0.05, 8.0,
             "Faulty power supply replaced; laptop starts and signs in."),
    Template("Laptop very slow", "hardware", "laptop", "Desktop Support", (4,), 4, 0.2, 10.0,
             "Disk nearly full and pending updates: cleared temporary files, installed updates, restarted."),
    Template("Docking station not showing monitors", "hardware", "monitor", "Desktop Support", (4,), 4, 0.4, 5.0,
             "Dock firmware updated and cables reseated; both monitors working."),
    Template("Printer at {office} not printing", "hardware", "printer", "Desktop Support", (4,), 5, 0.3, 6.0,
             "Paper jam cleared and print queue restarted; test page printed."),
    Template("Email not syncing on council phone", "hardware", "mobile", "Service Desk", (4,), 3, 0.6, 2.0,
             "Removed and re-added the work account on the phone."),
    Template("Outlook keeps asking for password", "software", "m365", "Service Desk", (3,), 4, 0.7, 1.0,
             "Cleared stale credentials from Credential Manager; Outlook signs in silently again."),
    Template("No sound in Teams calls", "software", "m365", "Desktop Support", (3,), 4, 0.5, 3.0,
             "Wrong audio device selected after a dock change; set the headset as default."),
    Template("OneDrive not syncing", "software", "m365", "Desktop Support", (4,), 3, 0.5, 4.0,
             "Reset the OneDrive client and resolved a file name conflict."),
    Template("Excel crashes opening a shared workbook", "software", "m365", "Desktop Support", (4,), 2, 0.3, 6.0,
             "Disabled a faulty add-in; workbook opens normally."),
    Template("Windows updates failing", "software", "os", "Desktop Support", (4,), 2, 0.1, 8.0,
             "Repaired the update components and installed the outstanding updates."),
    Template("Case management system: error saving a case note", "software", "business_app", "Business Applications",
             (2, 3), 3, 0.0, 12.0, "Supplier fix applied to the form; case notes save again.", departments=("ASC", "CFS")),
    Template("Revenues and benefits system very slow", "software", "business_app", "Business Applications", (3,), 2,
             0.0, 16.0, "Overnight job had overrun; supplier rescheduled it and performance is back to normal.",
             departments=("FIN", "CS")),
    Template("Planning portal: documents will not upload", "software", "business_app", "Business Applications", (3,),
             2, 0.0, 14.0, "File size limit raised by the supplier; uploads work.", departments=("PLN",)),
    Template("Payroll system: cannot sign in", "software", "business_app", "Business Applications", (2, 3), 1, 0.0,
             6.0, "Account was disabled at the supplier's end after a password reset; re-enabled.", departments=("HR",)),
    Template("Wi-Fi keeps dropping at {office}", "network", "wireless", "Infrastructure", (3,), 3, 0.0, 12.0,
             "Access point restarted and moved to a less crowded channel."),
    Template("Cannot connect to the VPN from home", "network", "vpn", "Service Desk", (3,), 4, 0.5, 3.0,
             "Home router blocking the VPN port; caller switched to the backup connection profile."),
    Template("Internet very slow at {office}", "network", "internet", "Infrastructure", (3,), 2, 0.0, 10.0,
             "Line provider fixed a fault on the circuit."),
    Template("Reported a phishing email", "security", "phishing", "Service Desk", (3,), 5, 0.9, 0.5,
             "Email reported to the security team and removed from all mailboxes; caller thanked."),
    Template("Clicked a link in a phishing email", "security", "phishing", "Infrastructure", (2,), 1, 0.0, 4.0,
             "Password reset, sessions revoked, laptop scanned clean; no data accessed."),
    Template("Lost council mobile phone", "security", "lost_device", "Desktop Support", (2,), 1, 0.0, 3.0,
             "Phone wiped through Intune and sign-in blocked; replacement issued."),
    Template("How do I set up an out-of-office reply?", "inquiry", "how_to", "Service Desk", (4, 5), 3, 0.98, 0.2,
             "Talked the caller through it and sent the KB article."),
    Template("How do I share my calendar with my team?", "inquiry", "how_to", "Service Desk", (4, 5), 2, 0.98, 0.2,
             "Showed the caller how to share their calendar."),
]

# People who left during the period. They are not in people.csv: their accounts were disabled on
# their last day and deleted 30 days later.
FORMER_STAFF = [
    ("Grace", "Okonjo", "ASC", "Social Worker"), ("Peter", "Lewis", "FIN", "Payments Officer"),
    ("Hannah", "Wright", "CS", "Customer Services Advisor"), ("Robert", "Green", "ENV", "Waste Services Officer"),
    ("Fatima", "Begum", "HSG", "Lettings Officer"), ("Sean", "Doyle", "PLN", "Planning Officer"),
    ("Laura", "Kim", "HR", "Recruitment Coordinator"), ("Mark", "Jennings", "LGL", "Paralegal"),
    ("Aisha", "Yusuf", "CFS", "Family Support Worker"), ("Tom", "Hart", "CS", "Customer Services Advisor"),
]
# fmt: on


@dataclass
class SimRecord:
    """One simulated incident or requested item: the ticket the report measures, plus what ServiceNow needs."""

    ticket: Ticket
    table: str
    caller: str = ""  # employee ID (requested for, on a request)
    assignee: str = ""  # employee ID
    impact: int = 3
    urgency: int = 3
    description: str = ""
    close_notes: str = ""
    approver: str = ""  # employee ID
    rejected: bool = False
    parent: str = ""  # correlation ID of the parent incident
    caused_by: str = ""  # correlation ID of the change that caused it


@dataclass
class Problem:
    correlation_id: str
    short_description: str
    description: str
    group: str
    assignee: str
    opened_at: datetime
    state: str  # resolved, known_error or fix_in_progress
    cause: str
    fix: str
    resolved_at: datetime | None = None


@dataclass
class Change:
    correlation_id: str
    type: str  # standard, normal or emergency
    short_description: str
    description: str
    group: str
    assignee: str
    start: datetime
    end: datetime
    state: str  # closed or scheduled
    risk: str
    justification: str
    implementation_plan: str
    backout_plan: str
    test_plan: str
    outcome: str = "successful"
    close_notes: str = "Implemented as planned; tests passed."
    problem: str = ""  # correlation ID of the problem it fixes


@dataclass
class History:
    incidents: list[SimRecord]
    requests: list[SimRecord]
    problems: list[Problem]
    changes: list[Change]
    as_at: datetime = AS_AT

    @property
    def tickets(self) -> list[Ticket]:
        return [r.ticket for r in self.incidents + self.requests]


class Generator:
    def __init__(self, config: Config, seed: int, timing: Timing | None = None) -> None:
        self.config = config
        # Made-up tickets need repeatable randomness, not secrecy.
        self.rng = random.Random(seed)  # nosec B311
        self.timing = timing or Timing()
        self.targets = Targets(config)
        self.desk: WorkingHours = self.targets.working
        self.people = list(config.org.people)
        self.by_id = {p.employee_id: p for p in self.people}
        self.heads = config.department_heads()
        self.department_shares = {d["code"]: d["share"] for d in config.org.departments}
        self.incident_count = 0
        self.request_count = 0

    # -- helpers ---------------------------------------------------------------------------------

    def pick[T](self, options: Sequence[tuple[T, float]]) -> T:
        total = sum(weight for _, weight in options)
        point = self.rng.uniform(0, total)
        for value, weight in options:
            point -= weight
            if point <= 0:
                return value
        return options[-1][0]

    def poisson(self, mean: float) -> int:
        limit, k, p = math.exp(-mean), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def lognormal(self, median: float, sigma: float = 0.7) -> float:
        return median * math.exp(self.rng.gauss(0, sigma))

    def working_days(self, first: date = PERIOD_START, last: date | None = None) -> Iterator[date]:
        day, last = first, last or AS_AT.date()
        while day <= last:
            if self.desk.is_working_day(day):
                yield day
            day += timedelta(days=1)

    def working_days_before(self, day: date, count: int) -> date:
        while count > 0:
            day -= timedelta(days=1)
            if self.desk.is_working_day(day):
                count -= 1
        return day

    def arrival(self, day: date) -> datetime:
        hour = self.pick(HOURS)
        return datetime.combine(day, time(hour, self.rng.randrange(60)), LONDON)

    def short_staffed(self, moment: datetime) -> bool:
        return SHORT_STAFFED[0] <= moment.date() <= SHORT_STAFFED[1]

    def staff_on(self, day: date) -> list[Person]:
        return [p for p in self.people if p.start_date <= day and (p.end_date is None or p.end_date >= day)]

    def caller(self, day: date, departments: Sequence[str] = ()) -> Person:
        staff = self.staff_on(day)
        pool = [p for p in staff if not departments or p.department_code in departments]
        return self.rng.choice(pool or staff)

    def analyst(self, group: str, moment: datetime) -> str:
        names = ANALYSTS.get(group, ANALYSTS["Service Desk"])
        if self.short_staffed(moment):
            names = [n for n in names if n not in ON_LEAVE] or names
        return self.rng.choice(names)

    def after(self, start: datetime, hours: float, clock: str = "working") -> datetime:
        if clock == "24x7":
            return start + timedelta(hours=hours)
        return self.desk.add(start, hours * 3600)

    def next_incident(self) -> str:
        self.incident_count += 1
        return f"SIM-INC-{self.incident_count:04d}"

    def next_request(self) -> str:
        self.request_count += 1
        return f"SIM-RITM-{self.request_count:04d}"

    # -- incidents -------------------------------------------------------------------------------

    def incident(
        self,
        template: Template,
        opened: datetime,
        *,
        priority: int | None = None,
        caller: Person | None = None,
        response_minutes: float | None = None,
        fix_hours: float | None = None,
        clock: str = "working",
        parent: str = "",
        caused_by: str = "",
        first_contact: bool | None = None,
        notes: str = "",
    ) -> SimRecord:
        rng = self.rng
        priority = priority or rng.choice(template.priorities)
        caller = caller or self.caller(opened.date(), template.departments)
        share = rng.choice(self.config.org.shares).name
        short = template.short.format(office=caller.office, share=share)
        contact = self.pick(CONTACT_TYPES)
        fixed_first_time = (rng.random() < template.first_contact) if first_contact is None else first_contact
        timing = self.timing
        slow = timing.short_staffed if self.short_staffed(opened) else 1.0
        target = self.targets.incident.get(priority, {}).get("response")
        target_minutes = target.seconds / 60 if target else 240.0

        if response_minutes is None:
            if fixed_first_time and contact in ("phone", "walk-in"):
                response_minutes = rng.uniform(1, 8)
            else:
                median = target_minutes * timing.response_share * slow
                response_minutes = min(self.lognormal(median, timing.response_sigma), target_minutes * 4)
        work_start = self.after(opened, response_minutes / 60, clock)

        group = "Service Desk" if fixed_first_time else template.group
        reassignments = 0
        if not fixed_first_time and template.group != "Service Desk":
            reassignments = 1 if rng.random() < 0.85 else 2
        if fix_hours is None:
            if fixed_first_time:
                fix_hours = rng.uniform(5, 25) / 60
            else:
                factor = {1: 0.4, 2: 0.5, 3: 1.0, 4: 1.2, 5: 1.5}[priority]
                queue = (
                    self.lognormal(timing.queue_hours * factor * slow, timing.queue_sigma)
                    if reassignments
                    else 0
                )
                fix_hours = min(queue + self.lognormal(template.hours * factor * slow, timing.fix_sigma), 90)
        resolved = self.after(work_start, fix_hours, clock)

        closes = resolved + timedelta(days=3)  # closed automatically 3 days after it is resolved
        resolved_at: datetime | None = resolved if resolved <= AS_AT else None
        closed_at: datetime | None = closes if closes <= AS_AT else None
        if resolved_at is None:
            state = "in_progress" if work_start <= AS_AT else "open"
        else:
            state = "closed" if closed_at else "resolved"
        impact, urgency = rng.choice(IMPACT_URGENCY[priority])
        assignee = self.analyst(group, opened)
        correlation = self.next_incident()
        ticket = Ticket(
            kind="incident",
            number=correlation,
            opened_at=opened,
            priority=priority,
            short_description=short,
            category=template.category,
            subcategory=template.subcategory,
            assignment_group=group,
            assigned_to=self.by_id[assignee].name,
            caller_id=caller.employee_id,
            department=caller.department,
            contact_type=contact,
            state=state,
            work_start=work_start if work_start <= AS_AT else None,
            resolved_at=resolved_at,
            closed_at=closed_at,
            reassignments=reassignments,
            problem=template.problem,
            correlation_id=correlation,
        )
        description = (
            notes
            or f"{caller.first_name} ({caller.job_title}, {caller.department}) reported: {short.lower()}."
        )
        return SimRecord(
            ticket=ticket,
            table="incident",
            caller=caller.employee_id,
            assignee=assignee,
            impact=impact,
            urgency=urgency,
            description=description,
            close_notes=template.fix,
            parent=parent,
            caused_by=caused_by,
        )

    def everyday_incidents(self) -> list[SimRecord]:
        records = []
        options = [(t, t.weight) for t in TEMPLATES]
        for day in self.working_days():
            load = 3.1 * WEEKDAY_LOAD[day.weekday()]
            for _ in range(self.poisson(load)):
                template = self.pick(options)
                opened = self.arrival(day)
                if opened < AS_AT:
                    records.append(self.incident(template, opened))
        return records

    # -- scripted events -------------------------------------------------------------------------

    def major_incidents(self) -> tuple[list[SimRecord], list[Problem], list[Change]]:
        records: list[SimRecord] = []
        liam, tomasz = "E10033", "E10037"

        # 27 August, short-staffed: email delayed for everyone; the P1 misses its 4 hours.
        email = Template(
            "Email delayed for all staff",
            "software",
            "m365",
            "Infrastructure",
            (1,),
            0,
            0.0,
            0,
            "Mail connector certificate had expired; renewed it and the queue cleared by 15:10.",
        )
        records.append(
            self.incident(
                email,
                datetime(2026, 8, 27, 9, 50, tzinfo=LONDON),
                priority=1,
                clock="24x7",
                response_minutes=22,
                fix_hours=5.3,
                first_contact=False,
                caller=self.by_id["E10048"],
                notes="Customer Services reported that emails to and from residents are delayed "
                "by over an hour.",
            )
        )

        # 15 September: a standard change to the DNS forwarders at 07:45 breaks sign-in at the Civic Centre.
        failed_change = Change(
            correlation_id="SIM-CHG-0001",
            type="standard",
            short_description="Update the DNS forwarders on DC01",
            description="Point DC01's DNS forwarders at the new resolvers (standard change template).",
            group="Infrastructure",
            assignee=liam,
            start=datetime(2026, 9, 15, 7, 45, tzinfo=LONDON),
            end=datetime(2026, 9, 15, 7, 55, tzinfo=LONDON),
            state="closed",
            risk="low",
            justification="Pre-approved standard change: DNS forwarder update.",
            implementation_plan="Replace the forwarder addresses in DNS Manager on DC01.",
            backout_plan="Put the previous forwarder addresses back.",
            test_plan="None in the template.",
            outcome="unsuccessful",
            close_notes="Caused the Civic Centre sign-in outage: one of the new addresses was a decommissioned "
            "resolver. Reverted by emergency change SIM-CHG-0002.",
            problem=CIVIC_DNS,
        )
        fix_change = Change(
            correlation_id="SIM-CHG-0002",
            type="emergency",
            short_description="Restore the previous DNS forwarders on DC01",
            description="The DNS change at 07:45 left DC01 forwarding to a decommissioned resolver, so sign-in and "
            "internet access failed at the Civic Centre. Restore the previous forwarders.",
            group="Infrastructure",
            assignee=liam,
            start=datetime(2026, 9, 15, 9, 40, tzinfo=LONDON),
            end=datetime(2026, 9, 15, 10, 0, tzinfo=LONDON),
            state="closed",
            risk="moderate",
            justification="Major incident: everyone at the Civic Centre is unable to sign in or reach the internet.",
            implementation_plan="Set the forwarders back to the values recorded before this morning's change; clear "
            "the DNS cache; test name resolution from a Civic Centre PC.",
            backout_plan="None needed: this restores the last known good configuration.",
            test_plan="nslookup of internal and external names from DC01 and a Civic Centre PC; staff sign-in test.",
            problem=CIVIC_DNS,
        )
        sign_in = Template(
            "Nobody at the Civic Centre can sign in",
            "network",
            "internet",
            "Infrastructure",
            (1,),
            0,
            0.0,
            0,
            "Major incident. DNS forwarders restored by emergency change; sign-in working by 10:05.",
            problem=CIVIC_DNS,
        )
        major = self.incident(
            sign_in,
            datetime(2026, 9, 15, 8, 20, tzinfo=LONDON),
            priority=1,
            clock="24x7",
            response_minutes=6,
            fix_hours=1.75,
            first_contact=False,
            caller=self.by_id["E10049"],
            caused_by=failed_change.correlation_id,
            notes="Staff across the Civic Centre get 'The trust relationship failed' or no internet. "
            "Started at about 08:00.",
        )
        records.append(major)
        related = Template(
            "Cannot sign in at the Civic Centre",
            "access",
            "password",
            "Service Desk",
            (3,),
            0,
            0.0,
            0,
            "Part of the Civic Centre major incident; sign-in works again after the fix.",
            problem=CIVIC_DNS,
        )
        civic = [p for p in self.staff_on(date(2026, 9, 15)) if p.office == "Civic Centre"]
        for minute in sorted(self.rng.sample(range(22, 60), 9)):
            records.append(
                self.incident(
                    related,
                    datetime(2026, 9, 15, 8, minute, tzinfo=LONDON),
                    priority=3,
                    caller=self.rng.choice(civic),
                    response_minutes=3,
                    fix_hours=1.2,
                    first_contact=False,
                    parent=major.ticket.correlation_id,
                )
            )
        problems = [
            Problem(
                correlation_id=CIVIC_DNS,
                short_description="Civic Centre sign-in outage after DNS change (15 September)",
                description="Major incident on 15 September: staff at the Civic Centre could not sign in or reach the "
                "internet for 1 hour 45 minutes.",
                group="Infrastructure",
                assignee=liam,
                opened_at=datetime(2026, 9, 15, 11, 0, tzinfo=LONDON),
                state="resolved",
                cause="A standard change updated the DNS forwarders on DC01 to a resolver that had been decommissioned. The "
                "change template had no test step, so the error was found by staff arriving at 08:00.",
                fix="Forwarders restored by emergency change. The DNS change template now includes a resolution test and "
                "is no longer a standard change: forwarder changes go to the CAB.",
                resolved_at=datetime(2026, 9, 22, 15, 0, tzinfo=LONDON),
            )
        ]

        # Repeated lockouts after password changes: known error, fix (Intune for phones) scheduled.
        problems.append(
            Problem(
                correlation_id=LOCKOUTS,
                short_description="Accounts lock repeatedly after a password change",
                description="About one in three lockout calls follow a password change. Trend found in the monthly review.",
                group="Identity and Access",
                assignee=liam,
                opened_at=datetime(2026, 8, 3, 10, 0, tzinfo=LONDON),
                state="known_error",
                cause="Mail apps and Teams on personal and council phones keep retrying the old password in the "
                "background; repeated failures lock the account.",
                fix="Workaround: KB article and Get-LabLockoutSource to name the device. Permanent fix: enrol phones in "
                "Intune with modern authentication (change SIM-CHG-0003, scheduled for November).",
            )
        )
        intune = Change(
            correlation_id="SIM-CHG-0003",
            type="normal",
            short_description="Enrol council phones in Intune with modern authentication",
            description="Move council phones to Intune-managed Outlook and Teams, which use tokens instead of a saved "
            "password. Fixes problem SIM-PRB-0002: accounts lock repeatedly after a password change.",
            group="Desktop Support",
            assignee="E10032",
            start=datetime(2026, 11, 10, 9, 0, tzinfo=LONDON),
            end=datetime(2026, 11, 20, 17, 0, tzinfo=LONDON),
            state="scheduled",
            risk="moderate",
            justification="About 15 lockout calls a month and lost time for staff; also lets lost phones be wiped.",
            implementation_plan="Pilot with IT (10 phones), then one department a day. Staff get a how-to and a "
            "drop-in session.",
            backout_plan="Unenrol the device in Intune; the existing mail profile still works.",
            test_plan="Pilot group changes passwords and confirms no lockouts for a week.",
            problem=LOCKOUTS,
        )

        # Riverside Depot Wi-Fi: problem, then a normal change through the CAB.
        problems.append(
            Problem(
                correlation_id=RIVERSIDE_WIFI,
                short_description="Wi-Fi and Teams calls drop at Riverside Depot",
                description="Recurring Wi-Fi and call quality incidents from the depot since July.",
                group="Infrastructure",
                assignee=tomasz,
                opened_at=datetime(2026, 7, 27, 14, 0, tzinfo=LONDON),
                state="resolved",
                cause="Two ageing access points cover the whole depot; the mess room and yard office are at the edge of "
                "coverage and both run on a congested channel.",
                fix="Access points replaced and a third added (change SIM-CHG-0004).",
                resolved_at=datetime(2026, 9, 11, 12, 0, tzinfo=LONDON),
            )
        )
        access_points = Change(
            correlation_id="SIM-CHG-0004",
            type="normal",
            short_description="Replace and add Wi-Fi access points at Riverside Depot",
            description="Replace the two access points at Riverside Depot and add a third in the yard office.",
            group="Infrastructure",
            assignee=tomasz,
            start=datetime(2026, 9, 9, 7, 0, tzinfo=LONDON),
            end=datetime(2026, 9, 9, 9, 0, tzinfo=LONDON),
            state="closed",
            risk="low",
            justification="Recurring incidents (problem SIM-PRB-0003); depot staff lose calls with residents.",
            implementation_plan="Before the depot opens: swap the two access points, mount the third, apply the "
            "standard wireless profile.",
            backout_plan="Refit the old access points (kept on site for a week).",
            test_plan="Walk test with a laptop and a Teams call in the mess room and yard office.",
            problem=RIVERSIDE_WIFI,
        )
        wifi = replace(next(t for t in TEMPLATES if t.subcategory == "wireless"), problem=RIVERSIDE_WIFI)
        for day in (
            date(2026, 7, 8),
            date(2026, 7, 21),
            date(2026, 8, 4),
            date(2026, 8, 19),
            date(2026, 9, 1),
        ):
            depot = [p for p in self.staff_on(day) if p.office == "Riverside Depot"]
            records.append(self.incident(wifi, self.arrival(day), priority=3, caller=self.rng.choice(depot)))

        changes = [failed_change, fix_change, intune, access_points]
        # Monthly Windows updates for the servers: standard changes.
        for month, patch_day in ((7, 16), (8, 13), (9, 10), (10, 15)):
            changes.append(
                Change(
                    correlation_id=f"SIM-CHG-{len(changes) + 1:04d}",
                    type="standard",
                    short_description=f"Monthly Windows security updates for servers ({date(2026, month, 1):%B})",
                    description="Install this month's Windows security updates on DC01 and restart it out of hours.",
                    group="Infrastructure",
                    assignee=liam,
                    start=datetime(2026, month, patch_day, 19, 0, tzinfo=LONDON),
                    end=datetime(2026, month, patch_day, 20, 0, tzinfo=LONDON),
                    state="closed",
                    risk="low",
                    justification="Pre-approved standard change: monthly security updates.",
                    implementation_plan="Back up system state, install updates, restart, run the health check.",
                    backout_plan="Uninstall the update, or restore the system state backup.",
                    test_plan="Lab doctor report: dcdiag, DNS, shares and agent tasks all healthy.",
                )
            )
        return records, problems, changes

    # -- requests --------------------------------------------------------------------------------

    def request(
        self,
        item: str,
        opened: datetime,
        requester: Person,
        department: str,
        *,
        approver: Person | None,
        approval_hours: float = 0.0,
        rejected: bool = False,
        due: datetime | None = None,
        handed_over: bool = False,
        late: bool = False,
        summary: str,
        handover_note: str = "Completed by hand after the fulfilment agent handed it over.",
    ) -> SimRecord:
        rng = self.rng
        approved = opened if approver is None or approval_hours == 0 else self.after(opened, approval_hours)
        start = max(approved, due) if due else approved
        if late and due:
            # Missed on the day: done first thing the next working morning.
            next_day = datetime.combine(due.astimezone(LONDON).date() + timedelta(days=1), time(0), LONDON)
            closed = self.desk.next_open(next_day) + timedelta(minutes=rng.uniform(2, 20))
        elif handed_over:
            closed = self.after(start, rng.uniform(1.5, 5))
        else:
            closed = start + timedelta(minutes=rng.uniform(1.5, 6))
        state = "cancelled" if rejected else "closed"
        closed_at: datetime | None = approved if rejected else closed
        approval_set: datetime | None = approved
        if approved > AS_AT:
            state, closed_at, approval_set = "open", None, None
        elif closed_at is not None and closed_at > AS_AT:
            state, closed_at = "in_progress", None
        correlation = self.next_request()
        ticket = Ticket(
            kind="request",
            number=correlation,
            opened_at=opened,
            priority=4,
            short_description=summary,
            category="request",
            assignment_group=self.config.catalog["fallback_group" if handed_over else "automation_group"],
            caller_id=requester.employee_id,
            department=department,
            contact_type="self-service",
            state=state,
            closed_at=closed_at,
            item=item,
            approval_set=approval_set,
            due_at=due,
            automated=state == "closed" and not handed_over,
            correlation_id=correlation,
        )
        if rejected:
            notes = "Rejected by the approver."
        else:
            notes = handover_note if handed_over else "Completed by the fulfilment agent."
        return SimRecord(
            ticket=ticket,
            table="sc_req_item",
            caller=requester.employee_id,
            assignee="E10031" if handed_over else "",
            approver=approver.employee_id if approver else "",
            rejected=rejected,
            description=summary,
            close_notes=notes,
        )

    def starters(self) -> list[SimRecord]:
        """Everyone in people.csv who joined in the period (or joins in the next few weeks)."""
        rng = self.rng
        records = []
        hr_advisors = [
            p
            for p in self.people
            if p.department_code == "HR" and p.job_title == "HR Advisor" and p.start_date < PERIOD_START
        ]
        for person in self.people:
            if not PERIOD_START < person.start_date <= AS_AT.date() + timedelta(days=30):
                continue
            manager = self.by_id[person.manager_id]
            opened = self.arrival(self.working_days_before(person.start_date, rng.randint(5, 15)))
            opened = max(opened, datetime.combine(PERIOD_START, time(9, 5), LONDON))
            if opened >= AS_AT:
                continue
            by_manager = rng.random() < 0.6
            records.append(
                self.request(
                    "New starter",
                    opened,
                    manager if by_manager else rng.choice(hr_advisors),
                    person.department,
                    approver=manager,
                    approval_hours=0 if by_manager else self.lognormal(4, 0.9),
                    handed_over=rng.random() < 0.1,
                    summary=f"New starter: {person.name}, {person.job_title} ({person.department}), "
                    f"starting {person.start_date.day} {person.start_date:%b}",
                )
            )
        return records

    def leavers(self) -> list[SimRecord]:
        rng = self.rng
        departments = {d["code"]: d["name"] for d in self.config.org.departments}
        days = list(self.working_days(PERIOD_START + timedelta(days=10), AS_AT.date() + timedelta(days=10)))
        last_days = sorted(rng.sample(days, len(FORMER_STAFF)))
        records = []
        late_done = False
        for (first, last, code, title), last_day in zip(FORMER_STAFF, last_days, strict=True):
            department = departments[code]
            manager = self.by_id[self.heads[department]]
            opened = self.arrival(self.working_days_before(last_day, rng.randint(2, 12)))
            if opened >= AS_AT:
                continue
            late = not late_done and last_day.month == 9
            late_done = late_done or late
            records.append(
                self.request(
                    "Leaver",
                    opened,
                    manager,
                    department,
                    approver=None,
                    due=datetime.combine(last_day, time(17, 30), LONDON),
                    handed_over=late,
                    late=late,
                    handover_note="The fulfilment agent handed it over: the mailbox had to be kept for a records "
                    "request. Disabled by hand the next morning, after the mailbox was put on hold.",
                    summary=f"Leaver: {first} {last}, {title} ({department}), last day {last_day.day} {last_day:%b}",
                )
            )
        return records

    def movers(self) -> list[SimRecord]:
        rng = self.rng
        heads = set(self.heads.values())
        candidates = [
            p
            for p in self.staff_on(PERIOD_START)
            if p.employee_id not in heads and p.department_code != "IT" and p.manager_id
        ]
        hr_advisors = [p for p in self.staff_on(PERIOD_START) if p.job_title == "HR Advisor"]
        records = []
        days = list(self.working_days(PERIOD_START + timedelta(days=14)))
        for person, effective in zip(rng.sample(candidates, 4), sorted(rng.sample(days, 4)), strict=True):
            manager = self.by_id[person.manager_id]  # the new line manager approves
            opened = self.arrival(self.working_days_before(effective, rng.randint(3, 10)))
            records.append(
                self.request(
                    "Change of role or department",
                    opened,
                    rng.choice(hr_advisors),
                    person.department,
                    approver=manager,
                    approval_hours=self.lognormal(3, 0.9),
                    due=datetime.combine(effective, time(0, 0), LONDON),
                    summary=f"Change of role: {person.name} to {person.job_title} ({person.department}) "
                    f"from {effective.day} {effective:%b}",
                )
            )
        return records

    def share_requests(self) -> list[SimRecord]:
        rng = self.rng
        records = []
        shares = self.config.org.shares
        for day in self.working_days():
            if day.weekday() != 0:
                continue
            week = [d for d in (day + timedelta(days=i) for i in range(5)) if self.desk.is_working_day(d)]
            for _ in range(self.poisson(1.3)):
                opened = self.arrival(rng.choice(week))
                if opened >= AS_AT:
                    continue
                person = self.caller(opened.date())
                own = self.department_shares.get(person.department_code)
                share = rng.choice([s for s in shares if s.name != own and s.owner_id != person.employee_id])
                owner = self.by_id[share.owner_id]
                rejected = rng.random() < (0.2 if share.restricted else 0.04)
                records.append(
                    self.request(
                        "Shared drive access",
                        opened,
                        person,
                        person.department,
                        approver=owner,
                        approval_hours=self.lognormal(3, 1.0),
                        rejected=rejected,
                        handed_over=not rejected and rng.random() < 0.05,
                        summary=f"Shared drive access: {person.name} to {share.name} (read)",
                    )
                )
        return records


def renumber(records: list[SimRecord], prefix: str) -> None:
    """Numbers records in the order they were logged, as ServiceNow would, and updates links between them."""
    numbers: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        numbers[record.ticket.correlation_id] = record.ticket.number = record.ticket.correlation_id = (
            f"{prefix}{index:04d}"
        )
    for record in records:
        record.parent = numbers.get(record.parent, record.parent)


def generate(config: Config, seed: int | None = None, timing: Timing | None = None) -> History:
    generator = Generator(config, DEFAULT_SEED if seed is None else seed, timing)
    everyday = generator.everyday_incidents()
    scripted, problems, changes = generator.major_incidents()
    incidents = sorted(everyday + scripted, key=lambda r: r.ticket.opened_at)
    requests = generator.starters() + generator.leavers() + generator.movers() + generator.share_requests()
    requests.sort(key=lambda r: r.ticket.opened_at)
    renumber(incidents, "SIM-INC-")
    renumber(requests, "SIM-RITM-")
    for record in incidents + requests:
        whole_seconds(record.ticket)
    return History(incidents, requests, problems, changes)


def whole_seconds(ticket: Ticket) -> None:
    """ServiceNow keeps times to the second, so the sample report and a loaded instance agree exactly."""
    for name in ("opened_at", "work_start", "resolved_at", "closed_at", "approval_set", "due_at"):
        moment = getattr(ticket, name)
        if moment is not None:
            setattr(ticket, name, moment.replace(microsecond=0))


# -- writing the history to ServiceNow ----------------------------------------------------------


def utc(moment: datetime | None) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if moment else ""


class Writer:
    """Creates the simulated records through the Table API, back-dated, skipping any already there."""

    def __init__(self, sn: ServiceNow, config: Config, progress: Callable[[str], None]) -> None:
        self.sn = sn
        self.config = config
        self.progress = progress
        self.users: dict[str, str] = {}
        self.groups: dict[str, str] = {}
        self.items: dict[str, str] = {}
        self.ids: dict[str, str] = {}  # correlation ID -> sys_id

    def prepare(self) -> None:
        people = [p.employee_id for p in self.config.org.people]
        for user in self.sn.query(
            "sys_user", in_query("employee_number", people), fields=["sys_id", "employee_number"]
        ):
            self.users[user["employee_number"]] = user["sys_id"]
        missing = sorted(set(people) - set(self.users))
        if missing:
            raise ServiceNowError(
                f"{len(missing)} staff are missing from ServiceNow (for example {missing[0]}): run `sdlab sn apply` first."
            )
        names = [g["name"] for g in self.config.organisation["groups"]]
        for group in self.sn.query("sys_user_group", in_query("name", names), fields=["sys_id", "name"]):
            self.groups[group["name"]] = group["sys_id"]
        item_names = [i["name"] for i in self.config.catalog["items"]]
        for item in self.sn.query("sc_cat_item", in_query("name", item_names), fields=["sys_id", "name"]):
            self.items[item["name"]] = item["sys_id"]
        for table in ("incident", "sc_req_item", "sc_request", "problem", "change_request"):
            for record in self.sn.query(
                table, "correlation_idSTARTSWITHSIM-", fields=["sys_id", "correlation_id"]
            ):
                self.ids[record["correlation_id"]] = record["sys_id"]

    def choice_values(self, table: str, element: str) -> list[str]:
        found = self.sn.query(
            "sys_choice",
            f"name={table}^element={element}^inactive=false^language=en",
            fields=["value", "sequence"],
        )
        return [c["value"] for c in sorted(found, key=lambda c: int(c.get("sequence") or 0))]

    def create(self, table: str, correlation: str, fields: dict[str, Any], fix: dict[str, Any]) -> str | None:
        """Creates a record, then puts back any back-dated field that a business rule overwrote."""
        if correlation in self.ids:
            return None
        created = self.sn.create(table, {**fields, "correlation_id": correlation})
        sys_id = str(created["sys_id"])
        self.ids[correlation] = sys_id
        changed = {k: v for k, v in fix.items() if v != "" and str(created.get(k, "")) != str(v)}
        if changed:
            self.sn.update(table, sys_id, changed)
        return sys_id

    def step(self, table: str, sys_id: str, label: str, steps: list[dict[str, Any]]) -> None:
        """Walks a record through its states as an analyst would. Stops quietly where the instance's
        state model wants more than the API can give: the record is still there, one state short."""
        for fields in steps:
            try:
                self.sn.update(table, sys_id, fields)
            except ServiceNowError as error:
                self.progress(f"   {label} stopped before state {fields.get('state')}: {error}")
                return

    def write_problems(self, problems: list[Problem]) -> int:
        count = 0
        paths = {
            "resolved": ["102", "103", "104", "106"],
            "known_error": ["102", "103"],
            "fix_in_progress": ["102", "103", "104"],
        }
        for problem in problems:
            fields = {
                "short_description": problem.short_description,
                "description": problem.description,
                "assignment_group": self.groups.get(problem.group, ""),
                "assigned_to": self.users.get(problem.assignee, ""),
                "opened_at": utc(problem.opened_at),
                "impact": 2,
                "urgency": 2,
            }
            sys_id = self.create(
                "problem", problem.correlation_id, fields, {"opened_at": utc(problem.opened_at)}
            )
            if sys_id is None:
                continue
            count += 1
            steps: list[dict[str, Any]] = []
            for state in paths[problem.state]:
                step: dict[str, Any] = {"state": state}
                if state == "103":
                    step["cause_notes"] = problem.cause
                if state == "104":
                    step["fix_notes"] = problem.fix
                if state == "106":
                    step.update(
                        {
                            "resolution_code": "fix_applied",
                            "fix_notes": problem.fix,
                            "resolved_at": utc(problem.resolved_at),
                        }
                    )
                steps.append(step)
            if problem.state == "known_error":
                steps.append({"known_error": True, "workaround": problem.fix, "cause_notes": problem.cause})
            self.step("problem", sys_id, f"problem {problem.correlation_id}", steps)
        return count

    def write_changes(self, changes: list[Change]) -> int:
        count = 0
        for change in changes:
            fields = {
                "type": change.type,
                "short_description": change.short_description,
                "description": change.description,
                "assignment_group": self.groups.get(change.group, ""),
                "assigned_to": self.users.get(change.assignee, ""),
                "start_date": utc(change.start),
                "end_date": utc(change.end),
                "risk": {"low": 4, "moderate": 3, "high": 2}.get(change.risk, 3),
                "impact": 3,
                "justification": change.justification,
                "implementation_plan": change.implementation_plan,
                "backout_plan": change.backout_plan,
                "test_plan": change.test_plan,
                "opened_at": utc(change.start - timedelta(days=7 if change.type == "normal" else 0, hours=1)),
            }
            sys_id = self.create(
                "change_request", change.correlation_id, fields, {"opened_at": fields["opened_at"]}
            )
            if sys_id is None:
                continue
            count += 1
            path = {
                "standard": ["-2", "-1", "0", "3"],
                "normal": ["-4", "-3", "-2", "-1", "0", "3"],
                "emergency": ["-3", "-2", "-1", "0", "3"],
            }[change.type]
            if change.state == "scheduled":
                path = path[: path.index("-2") + 1]
            steps: list[dict[str, Any]] = []
            for state in path:
                step: dict[str, Any] = {"state": state}
                if state == "-2":
                    step["approval"] = "approved"
                if state == "-1":
                    step["work_start"] = utc(change.start)
                if state == "0":
                    step["work_end"] = utc(change.end)
                if state == "3":
                    step.update({"close_code": change.outcome, "close_notes": change.close_notes})
                steps.append(step)
            self.step("change_request", sys_id, f"change {change.correlation_id}", steps)
            if change.problem in self.ids:
                try:
                    self.sn.update("problem", self.ids[change.problem], {"rfc": sys_id})
                except ServiceNowError as error:
                    self.progress(f"   could not link {change.correlation_id} to {change.problem}: {error}")
        return count

    def write_incidents(self, records: list[SimRecord]) -> int:
        close_codes = self.choice_values("incident", "close_code")
        close_code = next(
            (c for c in close_codes if "olution" in c or "olved" in c), close_codes[0] if close_codes else ""
        )
        states = {"open": 1, "in_progress": 2, "on_hold": 3, "resolved": 6, "closed": 7}
        count = 0
        for index, record in enumerate(records, start=1):
            t = record.ticket
            fields: dict[str, Any] = {
                "caller_id": self.users[record.caller],
                "opened_at": utc(t.opened_at),
                "impact": record.impact,
                "urgency": record.urgency,
                "category": t.category,
                "subcategory": t.subcategory,
                "short_description": t.short_description,
                "description": record.description,
                "contact_type": t.contact_type,
                "assignment_group": self.groups.get(t.assignment_group, ""),
                "assigned_to": self.users.get(record.assignee, ""),
                "state": states[t.state],
                "work_start": utc(t.work_start),
                "reassignment_count": t.reassignments,
                "problem_id": self.ids.get(t.problem, ""),
                "parent_incident": self.ids.get(record.parent, ""),
                "caused_by": self.ids.get(record.caused_by, ""),
            }
            if t.resolved_at:
                fields.update(
                    {
                        "close_code": close_code,
                        "close_notes": record.close_notes,
                        "resolved_by": self.users.get(record.assignee, ""),
                        "resolved_at": utc(t.resolved_at),
                        "calendar_stc": int((t.resolved_at - t.opened_at).total_seconds()),
                    }
                )
            if t.closed_at:
                fields["closed_at"] = utc(t.closed_at)
            fix = {
                k: fields[k]
                for k in ("opened_at", "resolved_at", "closed_at", "work_start", "calendar_stc")
                if fields.get(k)
            }
            if self.create("incident", t.correlation_id, fields, fix):
                count += 1
            if index % 50 == 0:
                self.progress(f"   {index} of {len(records)} incidents")
        return count

    def write_requests(self, records: list[SimRecord]) -> int:
        count = 0
        for record in records:
            t = record.ticket
            closed = t.state in ("closed", "cancelled")
            approval = ("rejected" if record.rejected else "approved") if t.approval_set else "requested"
            state = (4 if record.rejected else 3) if closed else 2
            request_key = t.correlation_id.replace("RITM", "REQ")
            request_fields = {
                "requested_for": self.users[record.caller],
                "opened_at": utc(t.opened_at),
                "short_description": t.short_description,
                "description": record.description,
                "request_state": ("closed_rejected" if record.rejected else "closed_complete")
                if closed
                else "in_process",
                "state": state,
                "approval": approval,
                "closed_at": utc(t.closed_at),
            }
            self.create(
                "sc_request",
                request_key,
                request_fields,
                {"opened_at": request_fields["opened_at"], "closed_at": request_fields["closed_at"]},
            )
            item_fields = {
                "request": self.ids[request_key],
                "cat_item": self.items.get(t.item, ""),
                "requested_for": self.users[record.caller],
                "opened_at": utc(t.opened_at),
                "short_description": t.short_description,
                "description": record.description,
                "approval": approval,
                "approval_set": utc(t.approval_set),
                "state": state,
                "stage": ("Request Cancelled" if record.rejected else "complete")
                if closed
                else ("fulfillment" if t.approval_set else "waiting_for_approval"),
                "closed_at": utc(t.closed_at),
            }
            item_id = self.create(
                "sc_req_item",
                t.correlation_id,
                item_fields,
                {k: item_fields[k] for k in ("opened_at", "approval_set", "closed_at", "approval", "state")},
            )
            if item_id is None:
                continue
            count += 1
            if record.approver:
                self.sn.create(
                    "sysapproval_approver",
                    {
                        "sysapproval": item_id,
                        "document_id": item_id,
                        "source_table": "sc_req_item",
                        "approver": self.users[record.approver],
                        "state": {"approved": "approved", "rejected": "rejected"}.get(approval, "requested"),
                    },
                )
            if not record.rejected and t.approval_set:
                self.sn.create(
                    "sc_task",
                    {
                        "request_item": item_id,
                        "request": self.ids[request_key],
                        "assignment_group": self.groups.get(t.assignment_group, ""),
                        "assigned_to": self.users.get(record.assignee, ""),
                        "short_description": t.short_description,
                        "state": 3 if closed else 1,
                        "due_date": utc(t.due_at),
                        "close_notes": record.close_notes if closed else "",
                        "closed_at": utc(t.closed_at),
                        "correlation_id": t.correlation_id.replace("RITM", "TASK"),
                    },
                )
        return count


def write_history(
    sn: ServiceNow, config: Config, history: History, progress: Callable[[str], None] = print
) -> str:
    writer = Writer(sn, config, progress)
    progress("Reading what is already there")
    writer.prepare()
    progress(f"Problems ({len(history.problems)})")
    problems = writer.write_problems(history.problems)
    progress(f"Changes ({len(history.changes)})")
    changes = writer.write_changes(history.changes)
    progress(f"Incidents ({len(history.incidents)})")
    incidents = writer.write_incidents(history.incidents)
    progress(f"Requests ({len(history.requests)})")
    requests = writer.write_requests(history.requests)
    return (
        f"{incidents} incidents, {requests} requests, {problems} problems and {changes} changes created "
        f"({sn.requests_made} API calls); records already there were skipped"
    )
