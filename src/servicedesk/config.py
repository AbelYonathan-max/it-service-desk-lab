"""Loads servicenow/config (YAML) together with the organisation in lab/config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from servicedesk.people import REPO_ROOT, Organisation, Person, load_organisation

SERVICENOW_CONFIG = REPO_ROOT / "servicenow" / "config"
SCRIPTS = REPO_ROOT / "servicenow" / "scripts"


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold a mapping.")
    return data


@dataclass
class Config:
    org: Organisation
    organisation: dict[str, Any]
    sla: dict[str, Any]
    catalog: dict[str, Any]
    knowledge: dict[str, Any]
    rules: dict[str, Any]
    holidays: dict[str, list[dict[str, Any]]]
    root: Path = REPO_ROOT
    email_domain: str = ""

    @property
    def prefix(self) -> str:
        return str(self.organisation.get("property_prefix", "kestrel.servicedesk"))

    @property
    def schedule(self) -> dict[str, Any]:
        return dict(self.organisation["schedule"])

    def holiday_dates(self) -> list[tuple[date, str]]:
        region = self.schedule.get("holidays", "england-and-wales")
        entries = self.holidays.get(region, [])
        result = []
        for entry in entries:
            day = entry["date"]
            result.append(
                (day if isinstance(day, date) else date.fromisoformat(str(day)), str(entry["title"]))
            )
        return sorted(result)

    def person(self, employee_id: str) -> Person:
        return self.org.person(employee_id)

    def department_names(self) -> list[str]:
        return [d["name"] for d in self.org.departments]

    def department_heads(self) -> dict[str, str]:
        """Department name -> employee ID of its head: the member whose manager is outside it."""
        people = {p.employee_id: p for p in self.org.people}
        heads: dict[str, str] = {}
        for person in self.org.people:
            manager = people.get(person.manager_id)
            if (
                manager is None or manager.department != person.department
            ) and person.department not in heads:
                heads[person.department] = person.employee_id
        return heads

    def script(self, name: str) -> str:
        return (SCRIPTS / name).read_text(encoding="utf-8")


def load_config(config_dir: Path | None = None, lab_config: Path | None = None) -> Config:
    directory = config_dir or SERVICENOW_CONFIG
    org = load_organisation(lab_config)
    return Config(
        org=org,
        organisation=_yaml(directory / "organisation.yaml"),
        sla=_yaml(directory / "sla.yaml"),
        catalog=_yaml(directory / "catalog.yaml"),
        knowledge=_yaml(directory / "knowledge.yaml"),
        rules=_yaml(directory / "rules.yaml"),
        holidays=_yaml(directory / "holidays.yaml"),
        email_domain=os.environ.get("SDLAB_EMAIL_DOMAIN", "").strip() or org.dns_name,
    )
