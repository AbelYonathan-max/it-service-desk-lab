from typing import Any, cast

import pytest

from servicedesk.apply import apply_all, catalog_property, duration, same, sla_definitions
from servicedesk.config import load_config
from servicedesk.snow import ServiceNow, ServiceNowError
from tests.fake_servicenow import FakeServiceNow, base_instance, matches


@pytest.fixture(scope="module")
def config() -> Any:
    return load_config()


def run(fake: FakeServiceNow, config: Any, dry_run: bool = False) -> Any:
    return apply_all(cast(ServiceNow, fake), config, dry_run=dry_run)


def test_durations_count_days_from_the_epoch() -> None:
    assert duration(minutes=15) == "1970-01-01 00:15:00"
    assert duration(hours=4) == "1970-01-01 04:00:00"
    assert duration(hours=30) == "1970-01-02 06:00:00"
    assert duration(hours=50) == "1970-01-03 02:00:00"


def test_same_ignores_line_endings_and_trailing_spaces() -> None:
    assert same("a  \r\nb\n", "a\nb")
    assert not same("a", "b")
    assert same(None, "")


def test_fake_reads_encoded_queries() -> None:
    record = {"a": "1", "b": "", "c": "SIM-1"}
    assert matches(record, "a=1^bISEMPTY")
    assert matches(record, "a=2^ORa=1")
    assert not matches(record, "cNOT LIKESIM-^ORcISEMPTY")
    assert matches({"c": ""}, "cNOT LIKESIM-^ORcISEMPTY")


def test_plan_on_a_new_instance_changes_nothing(config: Any) -> None:
    fake = base_instance()
    result = run(fake, config, dry_run=True)
    assert fake.writes == []
    assert result.counts["create"] > 200


def test_apply_builds_everything_then_finds_nothing_to_do(config: Any) -> None:
    fake = base_instance()
    first = run(fake, config)
    assert first.counts["create"] > 200
    assert len(first.users) == len(config.org.people)
    assert set(first.items) == {
        "New starter",
        "Leaver",
        "Change of role or department",
        "Shared drive access",
    }
    assert first.articles > 0

    second = run(fake, config)
    assert second.counts["create"] == 0
    assert second.counts["update"] == 0, [str(c) for c in second.changes if c.action != "same"]


def test_apply_puts_back_a_setting_changed_by_hand(config: Any) -> None:
    fake = base_instance()
    run(fake, config)
    group = fake.first("sys_user_group", "name=Service Desk")
    assert group is not None
    fake.update("sys_user_group", group["sys_id"], {"description": "edited in the browser"})
    result = run(fake, config)
    updates = [c for c in result.changes if c.action == "update"]
    assert [(c.table, c.fields) for c in updates] == [("sys_user_group", ["description"])]


def test_staff_records_belong_to_the_ad_sync_once_created(config: Any) -> None:
    fake = base_instance()
    run(fake, config)
    person = fake.first("sys_user", "employee_number=E10031")
    assert person is not None
    fake.update("sys_user", person["sys_id"], {"title": "Senior Service Desk Analyst", "manager": ""})
    result = run(fake, config)
    updates = [c for c in result.changes if c.action == "update"]
    # The title (now owned by AD) stays; the empty line manager is filled in again.
    assert [(c.table, c.label, c.fields) for c in updates] == [
        ("sys_user", "line manager of E10031", ["manager"])
    ]


def test_a_demo_user_with_a_staff_user_name_is_moved_aside(config: Any) -> None:
    fake = base_instance()
    demo = fake.add("sys_user", user_name="james.smith", name="James Smith", active=True)
    plan = run(fake, config, dry_run=True)
    assert fake.writes == []
    assert ("sys_user", "demo user james.smith renamed for E10065 James Smith", ["active", "user_name"]) in [
        (c.table, c.label, c.fields) for c in plan.changes if c.action == "update"
    ]

    run(fake, config)
    moved = {**demo, "user_name": "james.smith.demo", "active": "false"}
    assert fake.get("sys_user", demo["sys_id"]) == moved
    ours = fake.first("sys_user", "employee_number=E10065")
    assert ours is not None and ours["user_name"] == "james.smith"
    again = run(fake, config)
    assert again.counts["create"] == again.counts["update"] == 0


def test_a_refused_record_is_named_in_the_error(config: Any) -> None:
    fake = base_instance()
    fake.add("sys_user", user_name="hamza.ali", employee_number="E10003")  # one of the council's, so it stays
    with pytest.raises(ServiceNowError, match=r"^staff E10002 Hamza Ali: POST .* \(403\)"):
        run(fake, config)


def test_demo_slas_are_switched_off(config: Any) -> None:
    fake = base_instance()
    run(fake, config)
    demo = fake.first("contract_sla", "name=Priority 1 resolution (1 hour)")
    assert demo is not None and demo["active"] == "false"
    ours = fake.query("contract_sla", "active=true")
    assert len(ours) == 9


def test_sla_definitions_follow_the_targets(config: Any) -> None:
    definitions = {d["name"]: d for d in sla_definitions(config, "SCHEDULE", "FLOW")}
    p1 = definitions["Kestrel P1 Critical resolution (4 hours)"]
    assert p1["duration"] == "1970-01-01 04:00:00"
    assert p1["schedule"] == ""
    assert p1["schedule_source"] == "no_schedule"
    p3 = definitions["Kestrel P3 Moderate resolution (30 working hours)"]
    assert p3["schedule"] == "SCHEDULE"
    assert p3["pause_condition"] == "state=3"
    assert "correlation_idNOT LIKESIM-" in p3["start_condition"]
    request = definitions["Kestrel Request fulfilment (3 working days)"]
    assert request["collection"] == "sc_req_item"
    assert request["pause_condition"] == "approval=requested"


def test_catalogue_property_names_every_share_owner(config: Any) -> None:
    value = catalog_property(config)
    assert value["automation_group"] == config.org.service_now["automationGroup"]
    assert value["fallback_group"] == config.org.service_now["fallbackGroup"]
    assert set(value["items"]) == set(config.org.service_now["catalogItems"])
    assert value["share_owners"]["Finance"] == "E10020"
    assert value["items"]["Leaver"]["approval"] == "none"


def test_catalogue_variables_match_what_the_fulfilment_agent_reads(config: Any) -> None:
    fulfilment = (config.root / "powershell/ServiceDeskLab/Private/Fulfilment.ps1").read_text()
    names = {v["name"] for item in config.catalog["items"] for v in item["variables"]}
    for used in [
        "first_name",
        "last_name",
        "department",
        "job_title",
        "employee_id",
        "contract_type",
        "start_date",
        "end_date",
        "office",
        "line_manager",
        "last_working_day",
        "leaver",
        "effective_date",
        "employee",
        "new_department",
        "new_job_title",
        "new_line_manager",
        "user",
        "share",
        "access_level",
    ]:
        assert f"$v['{used}']" in fulfilment or f"Variables['{used}']" in fulfilment, used
        assert used in names, used
