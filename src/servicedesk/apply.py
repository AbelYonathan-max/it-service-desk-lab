"""Configuration as code: makes a ServiceNow instance match this repository.

``plan`` reads the instance and lists what would change; ``apply`` makes the changes. Both are safe
to run again and again: every record has a natural key (a group's name, a person's employee number,
a variable's catalogue item and name), so a second run finds what the first one made and only
changes fields that differ.

Staff records are the exception: they are created from the HR extract (lab/config/people.csv) and
never updated afterwards, because once the lab's domain controller is running, its sync from Active
Directory owns them (powershell/ServiceDeskLab/Public/Sync-LabServiceNowUser.ps1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import unescape
from typing import Any

from servicedesk.config import Config
from servicedesk.kb import load_articles
from servicedesk.snow import (
    InstanceAsleepError,
    Record,
    ServiceNow,
    ServiceNowError,
    encode,
    in_query,
    quote,
    run_once,
)

SIMULATED = "correlation_idNOT LIKESIM-^ORcorrelation_idISEMPTY"
VARIABLE_TYPES = {"yes_no": 1, "multi_line": 2, "select": 5, "single_line": 6, "reference": 8, "date": 9}
STAFF_QUALIFIER = "active=true^employee_numberSTARTSWITHE"
DEFAULT_SLA_FLOW = "Default SLA flow"
SLA_PREFIX = "Kestrel "
# Fields the instance keeps as sanitised HTML: it escapes quotes, drops line breaks between tags and
# adds attributes to links.
HTML_FIELDS = {("kb_knowledge", "text"), ("sc_cat_item", "description")}
LINK = re.compile(r'<a\s[^>]*?(href="[^"]*")[^>]*>')
PUBLISH_JOB = "Kestrel - publish knowledge articles"
SLA_NAME_LENGTH = 40  # what contract_sla.name holds: ServiceNow cuts a longer name off without a word


@dataclass
class Change:
    action: str  # create, update, delete or same
    table: str
    label: str
    fields: list[str] = field(default_factory=list)
    before: dict[str, str] = field(default_factory=dict)  # an update's old and new values
    after: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        sign = {"create": "+", "update": "~", "delete": "-", "same": "="}[self.action]
        detail = f" ({', '.join(self.fields)})" if self.action == "update" else ""
        return f"{sign} {self.table:<24} {self.label}{detail}"

    def differences(self, width: int = 60) -> list[str]:
        """What an update would change, field by field: short values whole, long ones where they part."""
        lines = []
        for name in self.fields:
            old, new = self.before.get(name, ""), self.after.get(name, "")
            if len(old) <= width and len(new) <= width and "\n" not in old + new:
                lines.append(f"{name}: {old!r} -> {new!r}")
                continue
            if (self.table, name) in HTML_FIELDS:
                old, new = tidy(old, html=True), tidy(new, html=True)  # where they differ once compared
            at = next(
                (i for i, (x, y) in enumerate(zip(old, new, strict=False)) if x != y), min(len(old), len(new))
            )
            start = max(0, at - 20)
            lines.append(
                f"{name}: {len(old)} -> {len(new)} characters, first difference at {at}: "
                f"{old[start : at + 40]!r} -> {new[start : at + 40]!r}"
            )
        return lines


def tidy(text: str, *, html: bool = False) -> str:
    """A value as compared: without trailing spaces or Windows line endings and, for HTML, without
    what the instance's sanitiser changes (escaped quotes, line breaks between tags, link attributes)."""
    if html:
        text = LINK.sub(r"<a \1>", re.sub(r">\s+<", "><", unescape(text)))
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def same(current: Any, desired: str, *, html: bool = False) -> bool:
    """Compares a stored value with the wanted one, as ``tidy`` leaves them."""
    have = "" if current is None else str(current)
    return have == desired or tidy(have, html=html) == tidy(desired, html=html)


def duration(hours: float = 0, minutes: float = 0) -> str:
    """A ServiceNow duration value: the time after 1970-01-01 00:00:00 (30 hours is 1970-01-02 06:00:00)."""
    total = round(hours * 3600 + minutes * 60)
    days, rest = divmod(total, 86400)
    clock = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"1970-01-{days + 1:02d} {clock}"


class Applier:
    """Finds, creates and updates records, keeping a list of every change."""

    def __init__(self, sn: ServiceNow, *, dry_run: bool) -> None:
        self.sn = sn
        self.dry_run = dry_run
        self.changes: list[Change] = []
        self._index: dict[tuple[str, tuple[str, ...]], dict[tuple[str, ...], Record]] = {}

    def prefetch(self, table: str, query: str, key: Iterable[str], fields: Iterable[str]) -> None:
        """Reads many records at once, so ``ensure`` does not need one request per record."""
        key_fields = tuple(key)
        wanted = sorted({"sys_id", *key_fields, *fields})
        index: dict[tuple[str, ...], Record] = {}
        for record in self.sn.query(table, query, fields=wanted):
            index.setdefault(tuple(str(record.get(k, "")) for k in key_fields), record)
        self._index[(table, key_fields)] = index

    def find(self, table: str, key: Mapping[str, str], fields: Iterable[str] = ()) -> Record | None:
        key_fields = tuple(key)
        index = self._index.get((table, key_fields))
        values = tuple(key[k] for k in key_fields)
        if index is not None:
            return index.get(values)
        if any(v.startswith("(new ") for v in values):
            return None
        query = "^".join(f"{k}={quote(v)}" for k, v in key.items())
        wanted = sorted({"sys_id", *key_fields, *fields})
        return self.sn.first(table, query, fields=wanted)

    def ensure(
        self,
        table: str,
        key: Mapping[str, Any],
        fields: Mapping[str, Any],
        *,
        label: str,
        create_only: Iterable[str] = (),
        if_empty: Iterable[str] = (),
    ) -> str:
        """Makes one record match. Returns its sys_id (a placeholder for a record a plan would create).

        ``create_only`` fields are set when the record is created and never changed afterwards;
        ``if_empty`` fields are only filled in when the record has no value yet.
        """
        key_values = {k: encode(v) for k, v in key.items()}
        desired = {k: encode(v) for k, v in fields.items()}
        current = self.find(table, key_values, desired)
        if current is None:
            self.changes.append(Change("create", table, label, sorted(desired)))
            if self.dry_run:
                return f"(new {table})"
            with self.explained(label):
                created = self.sn.create(table, {**key_values, **desired})
            index = self._index.get((table, tuple(key_values)))
            if index is not None:
                index[tuple(key_values.values())] = {**key_values, **desired, "sys_id": created["sys_id"]}
            return str(created["sys_id"])

        missing = [name for name in desired if name not in current]
        if missing:
            # Read in bulk without these fields: fetch the record itself before comparing.
            fresh = self.sn.get(table, str(current["sys_id"]), fields=["sys_id", *desired])
            current.update(fresh or {})
        skip = set(create_only)
        fill = set(if_empty)
        diff = {
            name: value
            for name, value in desired.items()
            if name not in skip
            and not same(current.get(name), value, html=(table, name) in HTML_FIELDS)
            and not (name in fill and str(current.get(name) or ""))
        }
        if not diff:
            self.changes.append(Change("same", table, label))
            return str(current["sys_id"])
        before = {name: "" if current.get(name) is None else str(current.get(name)) for name in diff}
        self.changes.append(Change("update", table, label, sorted(diff), before, dict(diff)))
        if not self.dry_run:
            with self.explained(label):
                self.sn.update(table, str(current["sys_id"]), diff)
            current.update(diff)
        return str(current["sys_id"])

    def remove(self, table: str, sys_id: str, *, label: str) -> None:
        self.changes.append(Change("delete", table, label))
        if not self.dry_run:
            with self.explained(label):
                self.sn.delete(table, sys_id)

    @contextmanager
    def explained(self, label: str) -> Iterator[None]:
        """Names the record when ServiceNow refuses a change, as its own message often does not."""
        try:
            yield
        except InstanceAsleepError:
            raise
        except ServiceNowError as error:
            raise ServiceNowError(f"{label}: {error}", error.status) from error

    def lookup(self, table: str, query: str, what: str) -> str:
        record = self.sn.first(table, query, fields=["sys_id"])
        if record is None:
            raise LookupError(f"ServiceNow has no {what} ({table}: {query}).")
        return str(record["sys_id"])

    def optional_lookup(self, table: str, query: str) -> str:
        record = self.sn.first(table, query, fields=["sys_id"])
        return str(record["sys_id"]) if record else ""

    def summary(self) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "delete": 0, "same": 0}
        for change in self.changes:
            counts[change.action] += 1
        return counts


# -- the configuration, step by step -----------------------------------------------------------


def catalog_property(config: Config) -> dict[str, Any]:
    """What the KestrelServiceDesk script include reads (system property <prefix>.catalog)."""
    catalog = config.catalog
    return {
        "automation_group": catalog["automation_group"],
        "fallback_group": catalog["fallback_group"],
        "items": {
            item["name"]: {key: item[key] for key in ("approval", "approval_note", "task") if key in item}
            for item in catalog["items"]
        },
        "share_owners": {share.name: share.owner_id for share in config.org.shares},
    }


def apply_properties(a: Applier, config: Config) -> None:
    value = json.dumps(catalog_property(config), sort_keys=True, separators=(",", ":"))
    a.ensure(
        "sys_properties",
        {"name": f"{config.prefix}.catalog"},
        {
            "value": value,
            "type": "string",
            "description": "People and access catalogue: approvals, fulfilment groups and shared drive owners. "
            "Managed from the it-service-desk-lab repository (servicenow/config/catalog.yaml); edits here are overwritten.",
        },
        label="catalogue settings (JSON)",
    )


def apply_locations(a: Applier, config: Config) -> dict[str, str]:
    result = {}
    for location in config.organisation["locations"]:
        fields = {k: location[k] for k in ("street", "city", "zip", "country") if k in location}
        result[location["name"]] = a.ensure(
            "cmn_location", {"name": location["name"]}, fields, label=f"office {location['name']}"
        )
    return result


def apply_departments(a: Applier, config: Config) -> dict[str, str]:
    result = {}
    for department in config.org.departments:
        result[department["name"]] = a.ensure(
            "cmn_department",
            {"name": department["name"]},
            {"id": department["code"]},
            label=f"department {department['name']}",
        )
    return result


def apply_staff(
    a: Applier, config: Config, departments: dict[str, str], locations: dict[str, str]
) -> dict[str, str]:
    people = config.org.people
    a.prefetch(
        "sys_user",
        in_query("employee_number", [p.employee_id for p in people]),
        ["employee_number"],
        ["user_name", "manager", "active"],
    )
    # A developer instance comes with demo users, and one of them can hold a user name the HR
    # extract needs. User names are unique, so that user is renamed and made inactive, keeping its
    # history, before the council's person is created.
    staff_ids = {p.employee_id for p in people}
    by_user_name = {p.user_name.lower(): p for p in people}
    for user in a.sn.query(
        "sys_user",
        in_query("user_name", list(by_user_name)),
        fields=["sys_id", "user_name", "employee_number"],
    ):
        holder = by_user_name.get(str(user.get("user_name", "")).lower())
        if holder is None or user.get("employee_number") in staff_ids:
            continue
        a.ensure(
            "sys_user",
            {"sys_id": user["sys_id"]},
            {"user_name": f"{user['user_name']}.demo", "active": False},
            label=f"demo user {user['user_name']} renamed for {holder.employee_id} {holder.name}",
        )
    users: dict[str, str] = {}
    profile_fields = [
        "user_name",
        "first_name",
        "last_name",
        "email",
        "title",
        "department",
        "location",
        "active",
    ]
    for person in people:
        users[person.employee_id] = a.ensure(
            "sys_user",
            {"employee_number": person.employee_id},
            {
                "user_name": person.user_name,
                "first_name": person.first_name,
                "last_name": person.last_name,
                "email": f"{person.upn_prefix}@{config.email_domain}",
                "title": person.job_title,
                "department": departments.get(person.department, ""),
                "location": locations.get(person.office, ""),
                "active": True,
            },
            label=f"staff {person.employee_id} {person.name}",
            create_only=profile_fields,
        )
    # Line managers once everyone exists. Only filled in where empty: after that, AD owns them.
    for person in people:
        if person.manager_id and person.manager_id in users:
            a.ensure(
                "sys_user",
                {"employee_number": person.employee_id},
                {"manager": users[person.manager_id]},
                label=f"line manager of {person.employee_id}",
                if_empty=["manager"],
            )
    return users


def apply_department_heads(a: Applier, config: Config, users: dict[str, str]) -> None:
    for department, employee_id in config.department_heads().items():
        a.ensure(
            "cmn_department",
            {"name": department},
            {"dept_head": users[employee_id]},
            label=f"head of {department}",
        )


def apply_groups(a: Applier, config: Config, users: dict[str, str]) -> dict[str, str]:
    groups: dict[str, str] = {}
    roles: dict[str, str] = {}
    for group in config.organisation["groups"]:
        manager = users.get(group.get("manager", ""), "")
        group_id = a.ensure(
            "sys_user_group",
            {"name": group["name"]},
            {"description": group.get("description", ""), "manager": manager, "active": True},
            label=f"group {group['name']}",
        )
        groups[group["name"]] = group_id
        for employee_id in group.get("members", []):
            a.ensure(
                "sys_user_grmember",
                {"group": group_id, "user": users[employee_id]},
                {},
                label=f"{group['name']} member {employee_id}",
            )
        for role in group.get("roles", []):
            if role not in roles:
                roles[role] = a.lookup("sys_user_role", f"name={quote(role)}", f"role {role}")
            a.ensure(
                "sys_group_has_role",
                {"group": group_id, "role": roles[role]},
                {"inherits": True},
                label=f"{group['name']} role {role}",
            )
    return groups


def apply_schedule(a: Applier, config: Config) -> str:
    schedule = config.schedule
    schedule_id = a.ensure(
        "cmn_schedule",
        {"name": schedule["name"]},
        {"description": schedule["description"], "time_zone": schedule["time_zone"]},
        label=f"schedule {schedule['name']}",
    )
    start = str(schedule["start"]).replace(":", "") + "00"
    end = str(schedule["end"]).replace(":", "") + "00"
    a.ensure(
        "cmn_schedule_span",
        {"schedule": schedule_id, "name": "Working day"},
        {
            "start_date_time": f"20250106T{start}",  # a Monday; repeats weekly from here
            "end_date_time": f"20250106T{end}",
            "repeat_type": "weekly",
            "repeat_count": 1,
            "days_of_week": "".join(str(d) for d in schedule["days"]),
            "all_day": False,
            "type": "",
        },
        label="working hours",
    )
    a.prefetch(
        "cmn_schedule_span",
        f"schedule={schedule_id}",
        ["schedule", "name"],
        ["start_date_time", "end_date_time", "all_day", "type", "repeat_type"],
    )
    for day, title in config.holiday_dates():
        stamp = day.strftime("%Y%m%d")
        a.ensure(
            "cmn_schedule_span",
            {"schedule": schedule_id, "name": f"{title} {day.isoformat()}"},
            {
                "start_date_time": f"{stamp}T000000",
                "end_date_time": f"{stamp}T235959",
                "all_day": True,
                "type": "exclude",
                "repeat_type": "",
            },
            label=f"bank holiday {day.isoformat()}",
        )
    return schedule_id


def apply_categories(a: Applier, config: Config) -> None:
    a.prefetch(
        "sys_choice",
        "name=incident^elementINcategory,subcategory^language=en",
        ["element", "value", "dependent_value"],
        ["label", "sequence", "inactive", "name", "language"],
    )
    for position, (value, category) in enumerate(config.organisation["categories"].items(), start=1):
        a.ensure(
            "sys_choice",
            {"element": "category", "value": value, "dependent_value": ""},
            {
                "name": "incident",
                "language": "en",
                "label": category["label"],
                "sequence": position * 10,
                "inactive": False,
            },
            label=f"category {category['label']}",
        )
        for sub_position, (sub_value, sub_label) in enumerate(
            category.get("subcategories", {}).items(), start=1
        ):
            a.ensure(
                "sys_choice",
                {"element": "subcategory", "value": sub_value, "dependent_value": value},
                {
                    "name": "incident",
                    "language": "en",
                    "label": sub_label,
                    "sequence": sub_position * 10,
                    "inactive": False,
                },
                label=f"subcategory {category['label']} / {sub_label}",
            )


def apply_knowledge(a: Applier, config: Config, users: dict[str, str]) -> int:
    settings = config.knowledge
    base = settings["knowledge_base"]
    fields: dict[str, Any] = {
        "description": base["description"],
        "owner": users[base["owner"]],
        "kb_managers": ",".join(users[m] for m in base.get("managers", [])),
        "active": True,
    }
    for field_name, workflow in (
        ("publish_workflow", "Knowledge - Instant Publish"),
        ("retire_workflow", "Knowledge - Instant Retire"),
    ):
        found = a.optional_lookup("wf_workflow", f"name={quote(workflow)}")
        if found:
            fields[field_name] = found
    kb_id = a.ensure(
        "kb_knowledge_base", {"title": base["title"]}, fields, label=f"knowledge base {base['title']}"
    )

    categories = {}
    for label in settings["categories"]:
        categories[label] = a.ensure(
            "kb_category",
            {"parent_id": kb_id, "label": label},
            {"parent_table": "kb_knowledge_base", "value": label.lower().replace(" ", "_"), "active": True},
            label=f"knowledge category {label}",
        )

    articles = load_articles(config.root / settings["articles"])
    if not kb_id.startswith("(new "):
        a.prefetch(
            "kb_knowledge",
            f"kb_knowledge_base={kb_id}",
            ["kb_knowledge_base", "short_description"],
            ["text", "kb_category", "workflow_state", "meta", "valid_to"],
        )
    unpublished: dict[str, str] = {}  # title -> state
    for article in articles:
        if article.category not in categories:
            raise ValueError(f"docs/kb/{article.slug}.md: unknown category {article.category!r}.")
        sys_id = a.ensure(
            "kb_knowledge",
            {"kb_knowledge_base": kb_id, "short_description": article.title},
            {
                "kb_category": categories[article.category],
                "text": article.html,
                "meta": article.keywords,
                "valid_to": "2100-01-01",
                "author": users[base["owner"]],
            },
            label=f"article {article.title}",
            create_only=["author"],
        )
        if sys_id.startswith("(new "):
            unpublished[article.title] = "draft"
    drafts: list[str] = []
    if not kb_id.startswith("(new "):
        titles = {article.title for article in articles}
        for record in a.sn.query(
            "kb_knowledge",
            f"kb_knowledge_base={kb_id}^workflow_stateINdraft,review",
            fields=["sys_id", "short_description", "workflow_state"],
        ):
            if record["short_description"] in titles:
                unpublished[record["short_description"]] = record["workflow_state"]
                drafts.append(record["sys_id"])
    for title, state in unpublished.items():
        a.changes.append(
            Change(
                "update",
                "kb_knowledge",
                f"article {title}",
                ["workflow_state"],
                {"workflow_state": state},
                {"workflow_state": "published"},
            )
        )
    if drafts and not a.dry_run:
        publish_articles(a.sn, config, drafts)
    return len(articles)


def publish_articles(sn: ServiceNow, config: Config, article_ids: list[str]) -> None:
    """Publishes draft articles with a one-off job (servicenow/scripts/publish_knowledge.js)."""
    ids = ",".join(article_ids)
    run_once(
        sn,
        PUBLISH_JOB,
        config.script("publish_knowledge.js").replace("'IN', 'ARTICLE_IDS'", f"'IN', '{ids}'"),
        lambda: (
            not sn.query("kb_knowledge", f"sys_idIN{ids}^workflow_stateINdraft,review", fields=["sys_id"])
        ),
    )


def _choices(config: Config, spec: Any) -> list[tuple[str, str]]:
    if spec == "departments":
        return [(name, name) for name in config.department_names()]
    if spec == "offices":
        return [(name, name) for name in config.org.offices]
    if spec == "shares":
        return [
            (share.name, f"{share.name}{' (special category data)' if share.restricted else ''}")
            for share in config.org.shares
        ]
    return [(str(value), str(text)) for value, text in spec]


def apply_catalog(a: Applier, config: Config, groups: dict[str, str]) -> dict[str, str]:
    settings = config.catalog
    catalog_id = a.lookup(
        "sc_catalog", f"title={quote(settings['catalog'])}", f"catalogue {settings['catalog']}"
    )
    category = settings["category"]
    category_id = a.ensure(
        "sc_category",
        {"sc_catalog": catalog_id, "title": category["title"]},
        {"description": category["description"], "active": True},
        label=f"catalogue category {category['title']}",
    )
    items: dict[str, str] = {}
    for position, item in enumerate(settings["items"], start=1):
        item_id = a.ensure(
            "sc_cat_item",
            {"name": item["name"], "sys_class_name": "sc_cat_item"},
            {
                "short_description": item["short_description"],
                "description": f"<p>{item['description']}</p>",
                "category": category_id,
                "sc_catalogs": catalog_id,
                "group": groups.get(settings["automation_group"], ""),
                "delivery_time": duration(hours=24 * int(settings.get("delivery_days", 3))),
                "no_quantity": True,
                "active": True,
                "order": position * 100,
            },
            label=f"catalogue item {item['name']}",
        )
        items[item["name"]] = item_id
        for order, variable in enumerate(item["variables"], start=1):
            kind = variable["type"]
            fields: dict[str, Any] = {
                "question_text": variable["question"],
                "type": VARIABLE_TYPES[kind],
                "order": order * 100,
                "mandatory": bool(variable.get("mandatory", False)),
                "active": True,
                "help_text": variable.get("help", ""),
                "default_value": variable.get("default", ""),
            }
            if kind == "reference":
                fields["reference"] = variable["table"]
                if variable["table"] == "sys_user":
                    fields["use_reference_qualifier"] = "simple"
                    fields["reference_qual_condition"] = STAFF_QUALIFIER
            if kind == "select":
                fields["include_none"] = not variable.get("default")
            variable_id = a.ensure(
                "item_option_new",
                {"cat_item": item_id, "name": variable["name"]},
                fields,
                label=f"{item['name']} variable {variable['name']}",
            )
            if kind == "select":
                for choice_order, (value, text) in enumerate(_choices(config, variable["choices"]), start=1):
                    a.ensure(
                        "question_choice",
                        {"question": variable_id, "value": value},
                        {"text": text, "order": choice_order * 100},
                        label=f"{item['name']} {variable['name']} choice {value}",
                    )
    return items


def sla_definitions(config: Config, schedule_id: str, flow_id: str) -> list[dict[str, Any]]:
    """The SLA definitions (contract_sla records) that the targets in sla.yaml describe."""
    definitions: list[dict[str, Any]] = []

    def clock(kind: str) -> dict[str, Any]:
        if kind == "24x7":
            return {"schedule": "", "schedule_source": "no_schedule"}
        return {"schedule": schedule_id, "schedule_source": "sla_definition"}

    common = {
        "type": "SLA",
        "duration_type": "",
        "timezone_source": "sla.timezone",
        "timezone": config.schedule["time_zone"],
        "retroactive": True,
        "set_start_to": "opened_at",
        "retroactive_pause": True,
        "when_to_cancel": "no_match",
        "flow": flow_id,
        "enable_logging": False,
        "active": True,
    }
    for target in config.sla["incident"]:
        priority = target["priority"]
        start = f"priority={priority}^{SIMULATED}"
        response = target["response_minutes"]
        working = "" if target["clock"] == "24x7" else " working"
        response_text = f"{response} minutes" if response < 60 else f"{response // 60}{working} hours"
        definitions.append(
            {
                **common,
                **clock(target["clock"]),
                "name": f"{SLA_PREFIX}P{priority} response ({response_text})",
                "collection": "incident",
                "target": "response",
                "duration": duration(minutes=response),
                "start_condition": start,
                "stop_condition": "work_startISNOTEMPTY^ORstateIN6,7",
                "pause_condition": "",
                "resume_condition": "",
                "when_to_resume": "on_condition",
            }
        )
        definitions.append(
            {
                **common,
                **clock(target["clock"]),
                "name": f"{SLA_PREFIX}P{priority} resolution ({target['resolution_hours']}{working} hours)",
                "collection": "incident",
                "target": "resolution",
                "duration": duration(hours=target["resolution_hours"]),
                "start_condition": start,
                "stop_condition": "stateIN6,7",
                "pause_condition": "state=3",
                "resume_condition": "state!=3",
                "when_to_resume": "on_condition",
            }
        )
    request = config.sla["request"]
    # Dated requests (a leaver's last day) are measured by the report on whether they were done that day.
    undated = "".join(f"^cat_item.name!={quote(name)}" for name in request.get("dated_items", {}))
    definitions.append(
        {
            **common,
            **clock(request["clock"]),
            "name": f"{SLA_PREFIX}{request['name']}",
            "collection": "sc_req_item",
            "target": "resolution",
            "duration": duration(hours=request["fulfilment_hours"]),
            "start_condition": SIMULATED + undated,
            "stop_condition": "active=false",
            "pause_condition": "approval=requested",
            "resume_condition": "approval!=requested",
            "when_to_resume": "on_condition",
        }
    )
    return definitions


def apply_slas(a: Applier, config: Config, schedule_id: str) -> list[str]:
    flow_id = a.optional_lookup("sys_hub_flow", f"name={quote(DEFAULT_SLA_FLOW)}")
    definitions = sla_definitions(config, schedule_id, flow_id)
    names = [definition["name"] for definition in definitions]
    too_long = [name for name in names if len(name) > SLA_NAME_LENGTH]
    if too_long:
        raise ValueError(f"ServiceNow keeps {SLA_NAME_LENGTH} characters of an SLA name: shorten {too_long}.")
    for definition in definitions:
        fields = {k: v for k, v in definition.items() if k != "name"}
        a.ensure("contract_sla", {"name": definition["name"]}, fields, label=definition["name"])
    not_ours = in_query("name", names).replace("IN", "NOT IN", 1)
    # Our SLAs that sla.yaml no longer describes: deleted if no ticket ever used them, otherwise
    # switched off, since closed tickets still point at them.
    for old in a.sn.query(
        "contract_sla", f"nameSTARTSWITH{SLA_PREFIX}^{not_ours}", fields=["sys_id", "name"]
    ):
        if a.sn.first("task_sla", f"sla={old['sys_id']}", fields=["sys_id"]) is None:
            a.remove("contract_sla", old["sys_id"], label=f"old SLA {old['name']}")
        else:
            a.ensure(
                "contract_sla", {"sys_id": old["sys_id"]}, {"active": False}, label=f"old SLA {old['name']}"
            )
    if config.sla.get("deactivate_others", True):
        # The instance's demo SLAs would attach to every ticket and confuse the report.
        others = a.sn.query(
            "contract_sla",
            f"collectionINincident,sc_req_item^active=true^{not_ours}",
            fields=["sys_id", "name"],
        )
        for other in others:
            if not other["name"].startswith(SLA_PREFIX):
                a.ensure(
                    "contract_sla",
                    {"sys_id": other["sys_id"]},
                    {"active": False},
                    label=f"demo SLA {other['name']}",
                )
    return names


def apply_scripts(a: Applier, config: Config) -> None:
    rules = config.rules
    for include in rules.get("script_includes", []):
        a.ensure(
            "sys_script_include",
            {"name": include["name"]},
            {
                "script": config.script(include["file"]),
                "description": include.get("description", ""),
                "access": "public",
                "client_callable": False,
                "active": True,
            },
            label=f"script include {include['name']}",
        )
    for rule in rules.get("business_rules", []):
        a.ensure(
            "sys_script",
            {"name": rule["name"], "collection": rule["table"]},
            {
                "when": rule["when"],
                "action_insert": bool(rule.get("insert", False)),
                "action_update": bool(rule.get("update", False)),
                "action_delete": False,
                "action_query": False,
                "order": rule.get("order", 100),
                "filter_condition": rule.get("filter", ""),
                "condition": "",
                "advanced": True,
                "active": True,
                "script": config.script(rule["file"]),
                "description": rule.get("description", ""),
            },
            label=f"business rule {rule['name']}",
        )


@dataclass
class Result:
    changes: list[Change]
    users: dict[str, str]
    groups: dict[str, str]
    items: dict[str, str]
    slas: list[str]
    articles: int

    @property
    def counts(self) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "delete": 0, "same": 0}
        for change in self.changes:
            counts[change.action] += 1
        return counts


STEPS = (
    "settings",
    "offices and departments",
    "staff",
    "teams",
    "working hours",
    "categories",
    "knowledge",
    "catalogue",
    "SLAs",
    "scripts",
)


def apply_all(
    sn: ServiceNow, config: Config, *, dry_run: bool, progress: Callable[[str], None] = lambda _: None
) -> Result:
    a = Applier(sn, dry_run=dry_run)
    progress("settings")
    apply_properties(a, config)
    progress("offices and departments")
    locations = apply_locations(a, config)
    departments = apply_departments(a, config)
    progress("staff")
    users = apply_staff(a, config, departments, locations)
    apply_department_heads(a, config, users)
    progress("teams")
    groups = apply_groups(a, config, users)
    progress("working hours")
    schedule_id = apply_schedule(a, config)
    progress("categories")
    apply_categories(a, config)
    progress("knowledge")
    articles = apply_knowledge(a, config, users)
    progress("catalogue")
    items = apply_catalog(a, config, groups)
    progress("SLAs")
    slas = apply_slas(a, config, schedule_id)
    progress("scripts")
    apply_scripts(a, config)
    return Result(a.changes, users, groups, items, slas, articles)


def working_days_after(start: date, days: int, holidays: set[date]) -> date:
    """The date ``days`` working days after ``start`` (used for sensible dates in tests and demos)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holidays:
            remaining -= 1
    return current
