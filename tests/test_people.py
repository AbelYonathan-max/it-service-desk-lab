"""Account names must match the PowerShell module's (powershell/tests/Naming.Tests.ps1)."""

import pytest

from servicedesk.people import account_name, load_organisation, load_people, to_ascii


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zoë", "zoe"),
        ("Siobhán", "siobhan"),
        ("O'Brien", "obrien"),
        ("García Fernández", "garciafernandez"),
        ("Smith-Jones", "smith-jones"),
        ("Søren", "soren"),
    ],
)
def test_to_ascii_matches_the_powershell_rules(text: str, expected: str) -> None:
    assert to_ascii(text) == expected


def test_firstname_dot_lastname() -> None:
    assert account_name("Amara", "Okafor", set()) == ("amara.okafor", "amara.okafor")


def test_a_clash_is_numbered_from_2() -> None:
    assert account_name("James", "Smith", {"james.smith"}) == ("james.smith2", "james.smith2")


def test_long_names_keep_the_sam_within_20_and_the_upn_in_full() -> None:
    sam, upn = account_name("Christopher", "Montgomery-Wellington", set())
    assert sam == "c.montgomery-welling"
    assert upn == "christopher.montgomery-wellington"
    sam2, _ = account_name("Christopher", "Montgomery-Wellington", {sam, upn})
    assert sam2 == "c.montgomery-wellin2"


def test_refuses_a_name_with_no_letters() -> None:
    with pytest.raises(ValueError, match="Cannot make"):
        account_name("!!", "Smith", set())


def test_everyone_in_the_hr_extract_gets_a_unique_name() -> None:
    people = load_people()
    assert len(people) > 50
    names = [p.user_name for p in people]
    assert len(set(names)) == len(names)
    assert all(len(name) <= 20 for name in names)
    smiths = [p.user_name for p in people if p.name == "James Smith"]
    assert smiths == ["james.smith", "james.smith2"]


def test_organisation_has_shares_with_owners_who_exist() -> None:
    org = load_organisation()
    ids = {p.employee_id for p in org.people}
    assert org.shares
    assert all(share.owner_id in ids for share in org.shares)
    assert set(org.offices) == {"Town Hall", "Civic Centre", "Riverside Depot"}
