"""The council's staff list (lab/config/people.csv) and organisation settings (lab/config/org.json).

The same two files build Active Directory on the domain controller, so account names here follow
exactly the rules of the PowerShell module's New-LabAccountName and ConvertTo-LabAscii:
firstname.lastname in plain ASCII, numbered on a clash (james.smith2), and first initial plus last
name when the result would pass the 20-character sAMAccountName limit.
"""

from __future__ import annotations

import csv
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_CONFIG = REPO_ROOT / "lab" / "config"

# Letters with no decomposed form (ConvertTo-LabAscii maps the same ones).
ASCII_MAP = {"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d", "ð": "d", "þ": "th"}


def to_ascii(text: str) -> str:
    """Zoë -> zoe, O'Brien -> obrien, García Fernández -> garciafernandez; hyphens kept."""
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()
    for letter, replacement in ASCII_MAP.items():
        plain = plain.replace(letter, replacement)
    kept = "".join(c for c in plain if c.isascii() and (c.isalnum() or c == "-"))
    return kept.strip("-")


def account_name(first: str, last: str, taken: set[str], max_length: int = 20) -> tuple[str, str]:
    """(sAMAccountName, UPN prefix) for a new person, avoiding names in ``taken``."""
    first_plain, last_plain = to_ascii(first), to_ascii(last)
    if not first_plain or not last_plain:
        raise ValueError(f"Cannot make an account name from {first!r} {last!r}.")
    upn_base = f"{first_plain}.{last_plain}"
    sam_base = upn_base if len(upn_base) <= max_length else f"{first_plain[0]}.{last_plain}"
    for n in range(1, 100):
        suffix = "" if n == 1 else str(n)
        sam = sam_base
        if len(sam) + len(suffix) > max_length:
            sam = sam[: max_length - len(suffix)]
        sam = sam.rstrip(".-") + suffix
        upn = upn_base + suffix
        if sam not in taken and upn not in taken:
            return sam, upn
    raise ValueError(f"No free account name for {first} {last}.")


@dataclass(frozen=True)
class Person:
    employee_id: str
    first_name: str
    last_name: str
    department: str
    department_code: str
    job_title: str
    manager_id: str
    office: str
    contract_type: str
    start_date: date
    end_date: date | None
    user_name: str
    upn_prefix: str

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class Share:
    name: str
    description: str
    restricted: bool
    owner_id: str


@dataclass
class Organisation:
    name: str
    dns_name: str
    departments: list[dict[str, str]]
    shares: list[Share]
    people: list[Person]
    service_now: dict[str, Any] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)  # role names to AD group names, from org.json

    def person(self, employee_id: str) -> Person:
        for person in self.people:
            if person.employee_id == employee_id:
                return person
        raise KeyError(employee_id)

    @property
    def offices(self) -> list[str]:
        return sorted({p.office for p in self.people})


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if value.strip() else None


def load_people(path: Path | None = None) -> list[Person]:
    """The staff list, with account names worked out in file order (as the AD build does)."""
    source = path or LAB_CONFIG / "people.csv"
    taken: set[str] = set()
    people: list[Person] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sam, upn = account_name(row["FirstName"], row["LastName"], taken)
            taken.update({sam, upn})
            start = _date(row["StartDate"])
            if start is None:
                raise ValueError(f"{row['EmployeeId']} has no start date.")
            people.append(
                Person(
                    employee_id=row["EmployeeId"].strip(),
                    first_name=row["FirstName"].strip(),
                    last_name=row["LastName"].strip(),
                    department=row["Department"].strip(),
                    department_code=row["DepartmentCode"].strip(),
                    job_title=row["JobTitle"].strip(),
                    manager_id=row["ManagerEmployeeId"].strip(),
                    office=row["Office"].strip(),
                    contract_type=row["ContractType"].strip(),
                    start_date=start,
                    end_date=_date(row["EndDate"]),
                    user_name=sam,
                    upn_prefix=upn,
                )
            )
    return people


def load_organisation(config_dir: Path | None = None) -> Organisation:
    directory = config_dir or LAB_CONFIG
    raw = json.loads((directory / "org.json").read_text(encoding="utf-8"))
    shares = [
        Share(s["name"], s["description"], bool(s["restricted"]), s["ownerEmployeeId"]) for s in raw["shares"]
    ]
    return Organisation(
        name=raw["organisation"],
        dns_name=raw["domain"]["dnsName"],
        departments=list(raw["departments"]),
        shares=shares,
        people=load_people(directory / "people.csv"),
        service_now=dict(raw.get("serviceNow", {})),
        groups=dict(raw.get("groups", {})),
    )
