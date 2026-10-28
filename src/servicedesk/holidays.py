"""Keeps servicenow/config/holidays.yaml in step with GOV.UK's bank holiday list.

Working-hours SLAs skip bank holidays, so a missing one (an extra day for a royal event, say) would
make tickets look late. GOV.UK publishes about two years ahead; years the file already has beyond
that are kept until GOV.UK lists them.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

from servicedesk.config import SERVICENOW_CONFIG

GOV_UK = "https://www.gov.uk/bank-holidays.json"
REGION = "england-and-wales"
SINCE = date(2025, 1, 1)
HEADER = """\
# England and Wales bank holidays, from https://www.gov.uk/bank-holidays.json; years GOV.UK does
# not list yet follow the usual rules until it does.
# The service desk is closed on these days: working-hours SLAs do not count them.
# `sdlab holidays` refreshes this file.
"""


def merge(existing: list[dict[str, Any]], published: list[dict[str, Any]]) -> list[tuple[date, str]]:
    """GOV.UK's dates where it has them, and the file's own dates for later years."""

    def day(value: Any) -> date:
        return value if isinstance(value, date) else date.fromisoformat(str(value))

    from_gov_uk = [(day(e["date"]), str(e["title"])) for e in published]
    last = max(d for d, _ in from_gov_uk)
    later = [(day(e["date"]), str(e["title"])) for e in existing if day(e["date"]) > last]
    return sorted({(d, t) for d, t in from_gov_uk + later if d >= SINCE})


def _title(text: str) -> str:
    """Plain where YAML allows it, else double-quoted (JSON's quoting is valid YAML)."""
    special = any(c in text for c in ':,{}[]#"') or text[:1] in set("!&*%@`|>'?- ")
    return json.dumps(text) if special or not text else text


def refresh(path: Path | None = None, client: httpx.Client | None = None) -> str:
    target = path or SERVICENOW_CONFIG / "holidays.yaml"
    with client or httpx.Client(timeout=30) as http:
        response = http.get(GOV_UK)
        response.raise_for_status()
        published = response.json()[REGION]["events"]
    existing = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}).get(REGION, [])
    days = merge(existing, published)
    lines = [f"  - {{date: {d.isoformat()}, title: {_title(t)}}}" for d, t in days]
    target.write_text(HEADER + f"{REGION}:\n" + "\n".join(lines) + "\n", encoding="utf-8")
    added = len({d for d, _ in days} - {date.fromisoformat(str(e["date"])) for e in existing})
    return f"{len(days)} bank holidays from {days[0][0]} to {days[-1][0]} ({added} new)."
