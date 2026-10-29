import copy
from typing import Any, cast

import pytest

from servicedesk.apply import Change, apply_all, catalog_property, duration, same, sla_definitions
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


def test_html_is_compared_as_the_instance_stores_it() -> None:
    stored = "<p>It&#39;s &#34;done&#34;</p><ol><li>One</li><li>Two</li></ol>"
    sent = '<p>It\'s "done"</p>\n<ol>\n<li>One</li>\n<li>Two</li>\n</ol>'
    assert same(stored, sent, html=True)
    assert not same(stored, sent)
    assert not same(stored, sent.replace("Two", "Three"), html=True)


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


def test_articles_are_published_by_a_one_off_job(config: Any) -> None:
    fake = base_instance()
    plan = run(fake, config, dry_run=True)
    publishing = [c for c in plan.changes if c.table == "kb_knowledge" and c.fields == ["workflow_state"]]
    assert len(publishing) == plan.articles

    run(fake, config)
    assert {a["workflow_state"] for a in fake.tables["kb_knowledge"]} == {"published"}
    assert [(action, table) for action, table, _ in fake.writes if table == "sysauto_script"] == [
        ("create", "sysauto_script"),
        ("delete", "sysauto_script"),
    ]
    assert fake.tables["sysauto_script"] == []

    # The instance's sanitiser rewrites the HTML; that is not a change to put back.
    article = fake.tables["kb_knowledge"][0]
    sanitised = article["text"].replace("'", "&#39;").replace(">\n<", "><")
    assert sanitised != article["text"]
    article["text"] = sanitised
    again = run(fake, config)
    assert again.counts["update"] == 0, [str(c) for c in again.changes if c.action != "same"]


def test_apply_puts_back_a_setting_changed_by_hand(config: Any) -> None:
    fake = base_instance()
    run(fake, config)
    group = fake.first("sys_user_group", "name=Service Desk")
    assert group is not None
    fake.update("sys_user_group", group["sys_id"], {"description": "edited in the browser"})
    result = run(fake, config)
    updates = [c for c in result.changes if c.action == "update"]
    assert [(c.table, c.fields) for c in updates] == [("sys_user_group", ["description"])]
    [difference] = updates[0].differences()
    assert difference.startswith("description: 21 -> ") and "'edited in the browser' -> " in difference


def test_a_long_difference_shows_where_the_values_part() -> None:
    change = Change(
        "update", "kb_knowledge", "article", ["text"], {"text": "a" * 100 + "b"}, {"text": "a" * 100 + "c"}
    )
    assert change.differences() == [
        "text: 101 -> 101 characters, first difference at 100: 'aaaaaaaaaaaaaaaaaaaab' -> 'aaaaaaaaaaaaaaaaaaaac'"
    ]


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


def test_slas_no_longer_configured_are_deleted_unless_tickets_used_them(config: Any) -> None:
    fake = base_instance()
    # ServiceNow cut this one's name off at 40 characters, so apply never found it again.
    unused = fake.add("contract_sla", name="Kestrel P3 Moderate resolution (30 worki", collection="incident")
    used = fake.add(
        "contract_sla", name="Kestrel P2 High response (30 minutes)", collection="incident", active=True
    )
    fake.add("task_sla", sla=used["sys_id"], task="INC0010001")
    result = run(fake, config)
    assert [(c.action, c.label) for c in result.changes if c.label.startswith("old SLA")] == [
        ("delete", "old SLA Kestrel P3 Moderate resolution (30 worki"),
        ("update", "old SLA Kestrel P2 High response (30 minutes)"),
    ]
    assert fake.get("contract_sla", unused["sys_id"]) is None
    assert fake.get("contract_sla", used["sys_id"], fields=["active"]) == {"active": "false"}
    again = run(fake, config)
    assert again.counts["create"] == again.counts["update"] == again.counts["delete"] == 0


def test_an_sla_name_servicenow_would_cut_off_is_refused(config: Any) -> None:
    longer = copy.deepcopy(config)
    longer.sla["request"]["name"] = "Request fulfilment (3 working days)"
    with pytest.raises(ValueError, match="40 characters"):
        run(base_instance(), longer, dry_run=True)


def test_sla_definitions_follow_the_targets(config: Any) -> None:
    definitions = {d["name"]: d for d in sla_definitions(config, "SCHEDULE", "FLOW")}
    assert set(definitions) == {
        "Kestrel P1 response (15 minutes)",
        "Kestrel P1 resolution (4 hours)",
        "Kestrel P2 response (30 minutes)",
        "Kestrel P2 resolution (8 working hours)",
        "Kestrel P3 response (2 working hours)",
        "Kestrel P3 resolution (30 working hours)",
        "Kestrel P4 response (4 working hours)",
        "Kestrel P4 resolution (50 working hours)",
        "Kestrel Fulfilment (3 working days)",
    }
    p1 = definitions["Kestrel P1 resolution (4 hours)"]
    assert p1["duration"] == "1970-01-01 04:00:00"
    assert p1["schedule"] == ""
    assert p1["schedule_source"] == "no_schedule"
    p3 = definitions["Kestrel P3 resolution (30 working hours)"]
    assert p3["schedule"] == "SCHEDULE"
    assert p3["pause_condition"] == "state=3"
    assert "correlation_idNOT LIKESIM-" in p3["start_condition"]
    request = definitions["Kestrel Fulfilment (3 working days)"]
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
