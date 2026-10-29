"""An in-memory stand-in for the ServiceNow Table API, enough for the configuration code."""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from servicedesk.snow import Record, ServiceNowError, encode

# What the instance fills in itself when a record is created.
DEFAULTS = {"kb_knowledge": {"workflow_state": "draft"}}
JOB_ARTICLES = re.compile(r"addQuery\('sys_id', 'IN', '([^']*)'\)")
JOB_CHANGES = "var changes = "

CONDITION = re.compile(
    r"^(?P<field>[\w.]+?)(?P<op>NOT IN|NOT LIKE|ISNOTEMPTY|ISEMPTY|STARTSWITH|IN|LIKE|!=|>=|<=|=|>|<)(?P<value>.*)$"
)


def _matches(record: Mapping[str, Any], condition: str) -> bool:
    match = CONDITION.match(condition)
    if not match:
        raise ValueError(f"fake cannot read condition {condition!r}")
    field, op, value = match["field"], match["op"], match["value"].replace("^^", "^")
    have = str(record.get(field, "") or "")
    if op == "=":
        return have == value
    if op == "!=":
        return have != value
    if op == "IN":
        return have in value.split(",")
    if op == "NOT IN":
        return have not in value.split(",")
    if op == "ISEMPTY":
        return have == ""
    if op == "ISNOTEMPTY":
        return have != ""
    if op == "STARTSWITH":
        return have.startswith(value)
    if op == "LIKE":
        return value in have
    if op in ("<", "<=", ">", ">="):
        # Dates as ServiceNow writes them ("2026-09-01 08:00:00") sort as text. Empty never matches.
        return (
            have != ""
            and {"<": have < value, "<=": have <= value, ">": have > value, ">=": have >= value}[op]
        )
    return value not in have  # NOT LIKE


def matches(record: Mapping[str, Any], query: str) -> bool:
    """Encoded query: ^ is AND, ^OR joins a condition to the one before it. ORDERBY is ignored."""
    if not query:
        return True
    groups: list[list[str]] = []
    for part in re.split(r"\^(?!\^)", query):
        if not part or part.startswith("ORDERBY"):
            continue
        if part.startswith("OR") and groups:
            groups[-1].append(part[2:])
        else:
            groups.append([part])
    return all(any(_matches(record, c) for c in group) for group in groups)


class FakeServiceNow:
    def __init__(self, seed: Mapping[str, list[Record]] | None = None) -> None:
        self.tables: dict[str, list[Record]] = {k: [dict(r) for r in v] for k, v in (seed or {}).items()}
        self._ids = itertools.count(1)
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.ordered: list[tuple[str, dict[str, Any], str | None]] = []
        self.requests_made = 0

    def new_id(self) -> str:
        return f"{next(self._ids):032x}"

    def add(self, table: str, **fields: Any) -> Record:
        record = {k: encode(v) for k, v in fields.items()}
        record.setdefault("sys_id", self.new_id())
        self.tables.setdefault(table, []).append(record)
        return record

    def query(
        self,
        table: str,
        query: str = "",
        *,
        fields: Iterable[str] | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[Record]:
        self.requests_made += 1
        found = [dict(r) for r in self.tables.get(table, []) if matches(r, query)]
        if order_by:
            found.sort(key=lambda r: str(r.get(order_by, "")))
        if fields:
            wanted = list(fields)
            found = [{k: r.get(k, "") for k in wanted} for r in found]
        return found[:limit] if limit else found

    def first(self, table: str, query: str, *, fields: Iterable[str] | None = None) -> Record | None:
        found = self.query(table, query, fields=fields, limit=1)
        return found[0] if found else None

    def get(self, table: str, sys_id: str, *, fields: Iterable[str] | None = None) -> Record | None:
        return self.first(table, f"sys_id={sys_id}", fields=fields)

    def _check_unique(self, table: str, data: Mapping[str, Any], sys_id: str = "") -> None:
        """User names are unique, whatever their case, and ServiceNow says little when one is taken."""
        name = str(data.get("user_name", "")).lower()
        if table != "sys_user" or not name:
            return
        if any(
            str(r.get("user_name", "")).lower() == name and r["sys_id"] != sys_id
            for r in self.tables.get(table, [])
        ):
            raise ServiceNowError(
                "POST /api/now/table/sys_user failed (403): Operation Failed. Error during insert of sys_user",
                403,
            )

    def create(self, table: str, data: Mapping[str, Any], *, display_values: bool = False) -> Record:
        self.requests_made += 1
        self._check_unique(table, data)
        record = self.add(table, **{**DEFAULTS.get(table, {}), **data})
        self.writes.append(("create", table, dict(record)))
        if table == "sysauto_script":
            self._run_job(str(data.get("script", "")))
        return dict(record)

    def _run_job(self, script: str) -> None:
        """The scheduler, running a one-off job straight away. It knows the two scripts the code
        starts: publish_knowledge.js and change_history.js."""
        articles = JOB_ARTICLES.search(script)
        for article in self.tables.get("kb_knowledge", []):
            if (
                articles
                and article["sys_id"] in articles[1].split(",")
                and article["workflow_state"] in ("draft", "review")
            ):
                article["workflow_state"] = "published"
        if JOB_CHANGES in script:
            start = script.index(JOB_CHANGES) + len(JOB_CHANGES)
            changes, _ = json.JSONDecoder().raw_decode(script, start)
            for change in self.tables.get("change_request", []):
                change.update({k: encode(v) for k, v in changes.get(change["sys_id"], {}).items()})

    def update(
        self, table: str, sys_id: str, data: Mapping[str, Any], *, display_values: bool = False
    ) -> Record:
        self.requests_made += 1
        self._check_unique(table, data, sys_id)
        for record in self.tables.get(table, []):
            if record["sys_id"] == sys_id:
                record.update({k: encode(v) for k, v in data.items()})
                self.writes.append(("update", table, {"sys_id": sys_id, **data}))
                return dict(record)
        raise KeyError(f"{table}/{sys_id}")

    def delete(self, table: str, sys_id: str) -> None:
        self.requests_made += 1
        self.tables[table] = [r for r in self.tables.get(table, []) if r["sys_id"] != sys_id]
        self.writes.append(("delete", table, {"sys_id": sys_id}))

    def order(
        self, item_sys_id: str, variables: Mapping[str, Any], *, requested_for: str | None = None
    ) -> Record:
        self.ordered.append((item_sys_id, dict(variables), requested_for))
        request = self.add(
            "sc_request", number=f"REQ{len(self.ordered):07d}", requested_for=requested_for or ""
        )
        self.add(
            "sc_req_item",
            number=f"RITM{len(self.ordered):07d}",
            request=request["sys_id"],
            cat_item=item_sys_id,
        )
        return {"sys_id": request["sys_id"], "number": request["number"]}


def base_instance() -> FakeServiceNow:
    """What a new developer instance already has that the configuration refers to."""
    fake = FakeServiceNow()
    fake.add("sys_user", user_name="admin", name="System Administrator")
    fake.add("sys_user_role", name="itil")
    fake.add("sys_user_role", name="user_admin")
    fake.add("sc_catalog", title="Service Catalog")
    fake.add("wf_workflow", name="Knowledge - Instant Publish")
    fake.add("wf_workflow", name="Knowledge - Instant Retire")
    fake.add("sys_hub_flow", name="Default SLA flow")
    fake.add("contract_sla", name="Priority 1 resolution (1 hour)", collection="incident", active=True)
    fake.add(
        "sys_choice",
        name="incident",
        element="category",
        value="software",
        label="Software",
        language="en",
        dependent_value="",
        sequence="20",
        inactive="false",
    )
    return fake
