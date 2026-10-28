from datetime import date
from pathlib import Path

import httpx
import respx

from servicedesk.config import load_config
from servicedesk.holidays import GOV_UK, merge, refresh


def test_gov_uk_wins_and_later_years_are_kept() -> None:
    existing = [
        {"date": date(2026, 12, 28), "title": "Boxing Day"},
        {"date": date(2028, 1, 3), "title": "New Year's Day"},
    ]
    published = [
        {"date": "2024-12-26", "title": "Boxing Day"},
        {"date": "2026-12-28", "title": "Boxing Day (substitute day)"},
        {"date": "2027-05-07", "title": "Extra bank holiday"},
    ]
    assert merge(existing, published) == [
        (date(2026, 12, 28), "Boxing Day (substitute day)"),
        (date(2027, 5, 7), "Extra bank holiday"),
        (date(2028, 1, 3), "New Year's Day"),
    ]


@respx.mock
def test_refresh_writes_a_file_the_config_can_read(tmp_path: Path) -> None:
    source = load_config().holidays
    target = tmp_path / "holidays.yaml"
    target.write_text("england-and-wales:\n  - {date: 2028-12-26, title: Boxing Day}\n", encoding="utf-8")
    events = [{"date": "2027-05-07", "title": "Bank holiday: a royal event, 2027"}]
    respx.get(GOV_UK).mock(return_value=httpx.Response(200, json={"england-and-wales": {"events": events}}))
    assert refresh(target) == "2 bank holidays from 2027-05-07 to 2028-12-26 (1 new)."
    text = target.read_text(encoding="utf-8")
    assert '{date: 2027-05-07, title: "Bank holiday: a royal event, 2027"}' in text
    assert "england-and-wales" in source  # the real file keeps the same shape
