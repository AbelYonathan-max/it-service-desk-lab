"""End-to-end smoke test on a live instance: incidents, SLAs, approvals and automated fulfilment.

It does what staff and analysts do, through the same APIs, and checks each result:
1. the configuration is in place (scripts, settings, SLA definitions);
2. a P1 incident gets its priority from impact and urgency, and both P1 SLAs attach;
3. starting work records Actual start, and resolving it completes the SLAs;
4. a shared drive request goes to the data owner for approval, and once approved becomes a task
   for the fulfilment agent (and, with --wait-for-agent, is carried out and closed by the lab);
5. a rejected request closes without anything being changed.

Everything it creates is marked "[Smoke test]" with a SMOKE- correlation ID, which the monthly
report leaves out.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from servicedesk.config import Config
from servicedesk.snow import Record, ServiceNow, quote, resolution_code

CALLER = "E10049"  # a customer services advisor
ANALYST = "E10031"  # a service desk analyst
OPEN_SHARE = "CustomerServices"
RESTRICTED_SHARE = "HR"
TASK_STATES = {
    "-5": "pending",
    "1": "open",
    "2": "work in progress",
    "3": "closed complete",
    "4": "closed incomplete",
    "7": "closed skipped",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'}  {self.name}{': ' + self.detail if self.detail else ''}"


def wait_for[T](
    probe: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> T | None:
    waited = 0.0
    while True:
        value = probe()
        if value:
            return value
        if waited >= timeout:
            return None
        sleep(interval)
        waited += interval


class SmokeTest:
    def __init__(self, sn: ServiceNow, config: Config, sleep: Callable[[float], None] = time.sleep) -> None:
        self.sn = sn
        self.config = config
        self.sleep = sleep
        self.checks: list[Check] = []
        self.tag = "SMOKE-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, passed, detail))
        return passed

    def wait[T](self, probe: Callable[[], T | None], timeout: float, interval: float = 5) -> T | None:
        return wait_for(probe, timeout=timeout, interval=interval, sleep=self.sleep)

    def user(self, employee_id: str) -> str:
        record = self.sn.first("sys_user", f"employee_number={employee_id}", fields=["sys_id"])
        if record is None:
            raise LookupError(f"No ServiceNow user with employee number {employee_id}: run `sdlab sn apply`.")
        return str(record["sys_id"])

    def sys_id(self, table: str, query: str) -> str:
        record = self.sn.first(table, query, fields=["sys_id"])
        return str(record["sys_id"]) if record else ""

    def tasks(self, ritm_id: str, query: str = "") -> str:
        """The item's catalogue tasks, said so that a failed check shows where each one came from."""
        found = self.sn.query(
            "sc_task",
            f"request_item={ritm_id}{query}^ORDERBYsys_created_on",
            fields=["number", "short_description", "assignment_group.name", "state", "sys_created_by"],
        )
        return "; ".join(
            f"{t.get('number')} '{t.get('short_description')}' for {t.get('assignment_group.name') or 'no group'}, "
            f"{TASK_STATES.get(str(t.get('state')), t.get('state'))}, created by {t.get('sys_created_by')}"
            for t in found
        )

    def last_work_note(self, sys_id: str) -> str:
        notes = self.sn.query(
            "sys_journal_field",
            f"element_id={sys_id}^element=work_notes^ORDERBYDESCsys_created_on",
            fields=["value"],
            limit=1,
        )
        return str(notes[0].get("value", "")) if notes else ""

    # -- 1 ---------------------------------------------------------------------------------------

    def configuration(self) -> bool:
        rules = self.sn.query("sys_script", "nameSTARTSWITHKestrel - ^active=true", fields=["name"])
        expected = {rule["name"] for rule in self.config.rules["business_rules"]}
        missing = expected - {rule["name"] for rule in rules}
        self.check(
            "Business rules installed and active",
            not missing,
            ", ".join(sorted(missing)) or f"{len(expected)} rules",
        )
        include = self.sys_id("sys_script_include", "name=KestrelServiceDesk^active=true")
        self.check("Script include installed", bool(include))
        slas = self.sn.query("contract_sla", "nameSTARTSWITHKestrel ^active=true", fields=["name"])
        self.check("SLA definitions active", len(slas) == 9, f"{len(slas)} of 9")
        return not missing and bool(include)

    # -- 2 and 3 ---------------------------------------------------------------------------------

    def incident(self) -> None:
        caller, analyst = self.user(CALLER), self.user(ANALYST)
        desk = self.sys_id("sys_user_group", "name=Service Desk")
        created = self.sn.create(
            "incident",
            {
                "caller_id": caller,
                "short_description": "[Smoke test] Nobody in Customer Services can open the case management system",
                "description": "Raised by the automated smoke test. Safe to ignore.",
                "impact": 1,
                "urgency": 1,
                "category": "software",
                "subcategory": "business_app",
                "contact_type": "phone",
                "assignment_group": desk,
                "correlation_id": self.tag,
            },
        )
        incident_id = created["sys_id"]
        number = created.get("number", "")
        self.check(
            "P1 from impact 1 and urgency 1",
            str(created.get("priority")) == "1",
            f"{number} priority {created.get('priority')}",
        )

        def slas() -> list[Record] | None:
            found = self.sn.query(
                "task_sla", f"task={incident_id}", fields=["sla.name", "stage", "has_breached"]
            )
            return found if len(found) >= 2 else None

        attached = self.wait(slas, timeout=90) or []
        names = sorted(str(s.get("sla.name")) for s in attached)
        self.check(
            "Both P1 SLAs attached",
            len(names) == 2 and all("P1" in n for n in names),
            "; ".join(names) or "none",
        )

        self.sn.update("incident", incident_id, {"state": 2, "assigned_to": analyst})
        record = self.sn.get("incident", incident_id, fields=["work_start"]) or {}
        self.check("Actual start set when work starts", bool(record.get("work_start")))

        self.sn.update(
            "incident",
            incident_id,
            {
                "state": 6,
                "close_code": resolution_code(self.sn),
                "close_notes": "Smoke test: resolved straight away.",
            },
        )

        def completed() -> list[Record] | None:
            found = self.sn.query("task_sla", f"task={incident_id}", fields=["sla.name", "stage"])
            return found if found and all(s.get("stage") == "completed" for s in found) else None

        done = self.wait(completed, timeout=90)
        stages = [
            str(s.get("stage")) for s in self.sn.query("task_sla", f"task={incident_id}", fields=["stage"])
        ]
        self.check("SLAs complete when resolved", done is not None, ", ".join(stages))

    # -- 4 and 5 ---------------------------------------------------------------------------------

    def order_share(self, share: str, reason: str) -> tuple[str, str]:
        item = self.sys_id("sc_cat_item", "name=Shared drive access^active=true")
        if not item:
            raise LookupError("No 'Shared drive access' catalogue item: run `sdlab sn apply`.")
        user = self.user(CALLER)
        request = self.sn.order(
            item,
            {
                "user": user,
                "share": share,
                "access_level": "read",
                "business_reason": f"[Smoke test] {reason}",
            },
            requested_for=user,
        )
        ritm = self.wait(
            lambda: self.sn.first("sc_req_item", f"request={request['sys_id']}", fields=["sys_id", "number"]),
            timeout=30,
        )
        if ritm is None:
            raise LookupError(f"{request['number']} has no requested item.")
        self.sn.update("sc_req_item", ritm["sys_id"], {"correlation_id": self.tag})
        return str(ritm["sys_id"]), str(ritm.get("number", ""))

    def approval_for(self, ritm_id: str) -> Record | None:
        return self.wait(
            lambda: self.sn.first(
                "sysapproval_approver", f"sysapproval={ritm_id}", fields=["sys_id", "approver", "state"]
            ),
            timeout=120,
        )

    def approved_request(self, wait_for_agent: bool) -> None:
        owner_id = next(s.owner_id for s in self.config.org.shares if s.name == OPEN_SHARE)
        ritm_id, number = self.order_share(OPEN_SHARE, "read access to check the automated access process")
        approval = self.approval_for(ritm_id)
        owner = self.user(owner_id)
        to_owner = approval is not None and approval.get("approver") == owner
        if not self.check("Approval goes to the data owner", to_owner, number) or approval is None:
            return
        ritm = self.sn.get("sc_req_item", ritm_id, fields=["approval"]) or {}
        self.check(
            "Item waits at Requested", ritm.get("approval") == "requested", f"approval={ritm.get('approval')}"
        )

        self.sn.update(
            "sysapproval_approver",
            approval["sys_id"],
            {"state": "approved", "comments": "Approved by the smoke test."},
        )
        group = self.config.catalog["automation_group"]
        task = self.wait(
            lambda: self.sn.first(
                "sc_task",
                f"request_item={ritm_id}^assignment_group.name={quote(group)}",
                fields=["sys_id", "number", "state"],
            ),
            timeout=60,
        )
        self.check(
            f"Approved item becomes a task for {group}", task is not None, (task or {}).get("number", "")
        )
        ritm = self.sn.get("sc_req_item", ritm_id, fields=["approval", "stage"]) or {}
        self.check(
            "Item approved and in fulfilment",
            ritm.get("approval") == "approved",
            f"approval={ritm.get('approval')}, stage={ritm.get('stage')}",
        )
        if not (wait_for_agent and task):
            return

        def task_closed() -> Record | None:
            record = self.sn.get("sc_task", task["sys_id"], fields=["state", "active", "close_notes"]) or {}
            return record if record.get("active") == "false" else None

        done = self.wait(task_closed, timeout=600, interval=15)
        if done is None:
            note = self.last_work_note(task["sys_id"])
            detail = (
                f"{task['number']} still open after 10 minutes; its last work note: {note}"
                if note
                else f"{task['number']} still open after 10 minutes and untouched: is the lab running and connected?"
            )
        else:
            state = TASK_STATES.get(str(done.get("state")), done.get("state"))
            detail = f"{task['number']} {state}: {done.get('close_notes')}"
        if not self.check(
            "Fulfilment agent carried out the task and closed it", (done or {}).get("state") == "3", detail
        ):
            return

        def item_closed() -> Record | None:
            record = self.sn.get("sc_req_item", ritm_id, fields=["state", "active"]) or {}
            return record if record.get("active") == "false" else None

        item = self.wait(item_closed, timeout=60)
        self.check(
            "Item closes with its task",
            (item or {}).get("state") == "3",
            "closed complete"
            if item
            else f"still open; open tasks: {self.tasks(ritm_id, '^active=true') or 'none'}",
        )

    def rejected_request(self) -> None:
        ritm_id, number = self.order_share(RESTRICTED_SHARE, "a request the data owner should turn down")
        approval = self.approval_for(ritm_id)
        if (
            not self.check("Restricted drive goes to its data owner", approval is not None, number)
            or approval is None
        ):
            return
        self.sn.update(
            "sysapproval_approver",
            approval["sys_id"],
            {"state": "rejected", "comments": "Rejected by the smoke test."},
        )

        def closed() -> Record | None:
            record = self.sn.get("sc_req_item", ritm_id, fields=["state", "approval", "active"]) or {}
            return record if record.get("active") == "false" else None

        record = self.wait(closed, timeout=60)
        self.check(
            "Rejected request closes with nothing done",
            bool(record) and (record or {}).get("approval") == "rejected",
            f"state={(record or {}).get('state')}",
        )
        tasks = self.tasks(ritm_id)
        self.check("No task for a rejected request", not tasks, tasks)


def run_smoke_test(
    sn: ServiceNow,
    config: Config,
    *,
    wait_for_agent: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Check]:
    test = SmokeTest(sn, config, sleep)
    steps: list[tuple[str, Callable[[], Any]]] = [
        ("incident", test.incident),
        ("approved request", lambda: test.approved_request(wait_for_agent)),
        ("rejected request", test.rejected_request),
    ]
    if test.configuration():
        for name, step in steps:
            try:
                step()
            except Exception as error:  # report and carry on with the other checks
                test.check(f"{name} step ran", False, str(error))
    return test.checks
