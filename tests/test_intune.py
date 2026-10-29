import re
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from servicedesk.entra import BUSINESS_PREMIUM, license_group, tenant_status
from servicedesk.graph import Graph, GraphError, odata_quote, token_from_azure_cli
from servicedesk.intune import Intune, IntuneError, Policy, load_policies, settings_key
from servicedesk.people import REPO_ROOT
from tests.fake_graph import (
    CHOICE_DEFINITION,
    COLLECTION_DEFINITION,
    SIMPLE_DEFINITION,
    VALUE_DEFINITION,
    FakeGraph,
    catalogue_for,
)

LICENCE_GROUP = "GG-LIC-M365-BusinessPremium"


@pytest.fixture(scope="module")
def policies() -> list[Policy]:
    return load_policies()


def tenant(policies: list[Policy]) -> FakeGraph:
    return FakeGraph(catalogue_for(policies))


def run(fake: FakeGraph, policies: list[Policy], *, apply: bool) -> list[str]:
    result = Intune(cast(Graph, fake)).run(policies, apply=apply)
    return [str(c) for c in result.changes]


def test_the_policies_load(policies: list[Policy]) -> None:
    assert sorted(p.kind for p in policies) == [
        "compliance",
        "settings",
        "settings",
        "settings",
        "settings",
        "update-ring",
    ]
    assert all(p.name.startswith("KBC - ") and p.description for p in policies)
    assert all(p.assign == "all devices" for p in policies)


def test_a_settings_policy_becomes_graph_json(policies: list[Policy]) -> None:
    bitlocker = next(p for p in policies if p.name == "KBC - BitLocker").body()
    assert (bitlocker["platforms"], bitlocker["technologies"]) == ("windows10", "mdm")
    first = bitlocker["settings"][0]["settingInstance"]
    assert first["choiceSettingValue"]["value"] == "device_vendor_msft_bitlocker_requiredeviceencryption_1"
    silent = bitlocker["settings"][1]["settingInstance"]["choiceSettingValue"]
    assert silent["children"][0]["choiceSettingValue"]["value"].endswith("allowstandarduserencryption_1")
    recovery = bitlocker["settings"][3]["settingInstance"]["choiceSettingValue"]["children"]
    assert recovery[0]["choiceSettingValue"]["value"].endswith("_osallowdra_name_0")
    laps = next(p for p in policies if p.name == "KBC - Windows LAPS").body()
    length = next(
        s["settingInstance"]
        for s in laps["settings"]
        if s["settingInstance"]["settingDefinitionId"].endswith("passwordlength")
    )
    assert length["simpleSettingValue"] == {
        "@odata.type": "#microsoft.graph.deviceManagementConfigurationIntegerSettingValue",
        "value": 20,
    }


def test_a_compliance_policy_carries_its_grace_period(policies: list[Policy]) -> None:
    compliance = next(p for p in policies if p.kind == "compliance").body()
    assert compliance["@odata.type"] == "#microsoft.graph.windows10CompliancePolicy"
    (rule,) = compliance["scheduledActionsForRule"]
    assert rule["scheduledActionConfigurations"][0] == {
        "actionType": "block",
        "gracePeriodHours": 24,
        "notificationTemplateId": "00000000-0000-0000-0000-000000000000",
        "notificationMessageCCList": [],
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("kind: app\nname: x\nassign: all devices\n", "kind must be"),
        ("kind: settings\nname: x\nassign: everyone\nsettings: []\n", "assign must be"),
        ("kind: settings\nname: x\nassign: all devices\nsettings: []\n", "must be a list"),
        ("kind: settings\nname: x\nassign: all devices\nsettings:\n  - id: a\n", "one of choice"),
        (
            "kind: settings\nname: x\nassign: all devices\nsettings:\n  - id: A_b\n    choice: 1\n",
            "lower case",
        ),
        (
            "kind: settings\nname: x\nassign: all devices\nsettings:\n  - id: a\n    integer: '5'\n",
            "whole number",
        ),
        (
            "kind: settings\nname: x\nassign: all devices\nsettings:\n  - id: a\n    integer: 5\n"
            "    children:\n      - id: b\n        choice: 1\n",
            "only a choice",
        ),
        (
            "kind: settings\nname: x\nassign: all devices\nsettings:\n  - id: a\n    strings: text\n",
            "list of text",
        ),
        ("kind: compliance\nname: x\nassign: all devices\nproperties: {}\n", "@odata.type"),
    ],
)
def test_mistakes_in_policy_files_are_caught(tmp_path: Path, content: str, message: str) -> None:
    (tmp_path / "policy.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_policies(tmp_path)


def test_unknown_settings_stop_the_run_before_any_change(policies: list[Policy]) -> None:
    fake = tenant(policies)
    del fake.catalogue["device_vendor_msft_laps_policies_passwordlength"]
    complexity = fake.catalogue["device_vendor_msft_laps_policies_passwordcomplexity"]
    complexity["options"] = [o for o in complexity["options"] if not o["itemId"].endswith("_4")]
    with pytest.raises(IntuneError) as error:
        run(fake, policies, apply=True)
    text = str(error.value)
    assert (
        "laps.yaml: device_vendor_msft_laps_policies_passwordlength is not in this tenant's settings catalog"
        in text
    )
    assert "has no option device_vendor_msft_laps_policies_passwordcomplexity_4" in text
    assert fake.writes == []


RECOVERY = "device_vendor_msft_bitlocker_systemdrivesrecoveryoptions"
INACTIVITY = (
    "device_vendor_msft_policy_config_localpoliciessecurityoptions_interactivelogon_machineinactivitylimit"
)
CONSUMER = "device_vendor_msft_policy_config_experience_allowwindowsconsumerfeatures"
SPOTLIGHT = "user_vendor_msft_policy_config_experience_allowwindowsspotlight"
NOTICE = (
    "device_vendor_msft_policy_config_localpoliciessecurityoptions_"
    "interactivelogon_messagetextforusersattemptingtologon"
)


def test_catalogue_mismatches_name_the_setting_to_use(tmp_path: Path) -> None:
    # What a real tenant answered to the first version of the BitLocker and device security policies.
    (tmp_path / "policy.yaml").write_text(
        f"""kind: settings
name: x
assign: all devices
settings:
  - id: {RECOVERY}
    choice: 1
    children:
      - id: {RECOVERY}_osallowdra
        choice: 0
      - id: {RECOVERY}_ostypo
        choice: 1
  - id: {INACTIVITY}
    integer: 900
  - id: {NOTICE}
    string: Authorised use only.
  - id: {CONSUMER}
    choice: 0
""",
        encoding="utf-8",
    )
    recovery = {
        "@odata.type": CHOICE_DEFINITION,
        "options": [
            {"itemId": f"{RECOVERY}_0"},
            {"itemId": f"{RECOVERY}_1", "dependedOnBy": [{"dependedOnBy": f"{RECOVERY}_osallowdra_name"}]},
        ],
    }
    fake = FakeGraph(
        {
            RECOVERY: recovery,
            f"{RECOVERY}_osallowdra_name": {
                "@odata.type": CHOICE_DEFINITION,
                "options": [{"itemId": f"{RECOVERY}_osallowdra_name_0"}],
            },
            INACTIVITY: {"@odata.type": CHOICE_DEFINITION, "options": [{"itemId": f"{INACTIVITY}_1"}]},
            f"{INACTIVITY}_v2": {
                "@odata.type": SIMPLE_DEFINITION,
                "valueDefinition": {"@odata.type": VALUE_DEFINITION.format("Integer")},
            },
            NOTICE: {
                "@odata.type": COLLECTION_DEFINITION,
                "valueDefinition": {"@odata.type": VALUE_DEFINITION.format("String")},
            },
            CONSUMER: {
                "@odata.type": CHOICE_DEFINITION,
                "options": [{"itemId": f"{CONSUMER}_0", "dependentOn": [{"dependentOn": f"{SPOTLIGHT}_1"}]}],
            },
        }
    )
    with pytest.raises(IntuneError) as error:
        run(fake, load_policies(tmp_path), apply=False)
    text = str(error.value)
    assert (
        f"{RECOVERY}_osallowdra is not in this tenant's settings catalog. It has {RECOVERY}_osallowdra_name."
        in text
    )
    siblings = f"The option above it has these settings: {RECOVERY}_osallowdra_name."
    assert f"_ostypo is not in this tenant's settings catalog. {siblings}" in text
    assert f"{INACTIVITY} does not take a whole number. {INACTIVITY}_v2 does." in text
    assert f"{NOTICE} takes a list of text (strings)." in text
    assert f"{CONSUMER} only goes under {SPOTLIGHT}_1." in text


def test_apply_creates_everything_once(policies: list[Policy]) -> None:
    fake = tenant(policies)
    planned = run(fake, policies, apply=False)
    assert len(planned) == 6 and all(line.startswith("create ") for line in planned)
    assert fake.writes == []

    run(fake, policies, apply=True)
    creates = [path for method, path, _ in fake.writes if method == "POST" and not path.endswith("/assign")]
    assigns = [path for method, path, _ in fake.writes if path.endswith("/assign")]
    assert len(creates) == 6 and len(assigns) == 6

    writes = len(fake.writes)
    assert all(line.startswith("same ") for line in run(fake, policies, apply=True))
    assert len(fake.writes) == writes


def test_changes_made_in_the_portal_are_put_back(policies: list[Policy]) -> None:
    fake = tenant(policies)
    run(fake, policies, apply=True)
    fake.policy("KBC - Windows compliance")["osMinimumVersion"] = "10.0.22631"
    fake.policy("KBC - Windows compliance")["_rules"][0]["scheduledActionConfigurations"][0][
        "gracePeriodHours"
    ] = 72
    fake.policy("KBC - Windows Update ring")["_assignments"] = []
    ring = fake.policy("KBC - Windows Update ring")
    ring["installationSchedule"]["activeHoursStart"] = "07:00:00.0000000"
    laps = fake.policy("KBC - Windows LAPS")
    for setting in laps["_settings"]:
        instance = setting["settingInstance"]
        if instance["settingDefinitionId"].endswith("passwordlength"):
            instance["simpleSettingValue"]["value"] = 8

    planned = run(fake, policies, apply=False)
    assert 'update  KBC - Windows compliance: osMinimumVersion: "10.0.22631" -> "10.0.26100"' in planned
    assert "update  KBC - Windows compliance: non-compliant after 24 hours" in planned
    assert "assign  KBC - Windows Update ring: to all devices" in planned
    assert any("installationSchedule.activeHoursStart" in line for line in planned)
    assert (
        "update  KBC - Windows LAPS: settings laps_policies_passwordlength (differ from the repository)"
        in planned
    )

    run(fake, policies, apply=True)
    assert all(line.startswith("same ") for line in run(fake, policies, apply=False))
    patch = next(
        body for method, path, body in fake.writes if method == "PATCH" and "deviceCompliancePolicies" in path
    )
    assert patch == {
        "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
        "osMinimumVersion": "10.0.26100",
    }


def test_graph_echoes_do_not_count_as_changes(policies: list[Policy]) -> None:
    laps = next(p for p in policies if p.name == "KBC - Windows LAPS").body()["settings"]
    echoed = [
        {"id": "0", "settingInstance": {**s["settingInstance"], "settingInstanceTemplateReference": None}}
        for s in laps
    ]
    assert settings_key(echoed) == settings_key(list(reversed(laps)))


def licensed_tenant(policies: list[Policy], *, members: int = 18, free: int = 25) -> FakeGraph:
    fake = tenant(policies)
    fake.skus = [
        {
            "skuId": "sku-1",
            "skuPartNumber": BUSINESS_PREMIUM,
            "prepaidUnits": {"enabled": 25},
            "consumedUnits": 25 - free,
        }
    ]
    fake.groups = [
        {"id": "group-1", "displayName": LICENCE_GROUP, "onPremisesSyncEnabled": True, "assignedLicenses": []}
    ]
    fake.members = {"group-1": [{"id": f"user-{n}"} for n in range(members)]}
    return fake


def test_licences_wait_for_the_trial_and_the_synced_group(policies: list[Policy]) -> None:
    fake = tenant(policies)
    assert license_group(cast(Graph, fake), LICENCE_GROUP, apply=True).action == "waiting"
    fake.skus = licensed_tenant(policies).skus
    waiting = license_group(cast(Graph, fake), LICENCE_GROUP, apply=True)
    assert waiting.action == "waiting" and "Cloud Sync" in waiting.message
    assert fake.writes == []


def test_the_licence_group_gets_business_premium_once(policies: list[Policy]) -> None:
    fake = licensed_tenant(policies)
    planned = license_group(cast(Graph, fake), LICENCE_GROUP, apply=False)
    assert planned.action == "assign" and "would give" in planned.message and fake.writes == []
    done = license_group(cast(Graph, fake), LICENCE_GROUP, apply=True)
    assert done.message == f"{LICENCE_GROUP} (18 members) now gives Business Premium."
    assert fake.writes[0][2] == {
        "addLicenses": [{"skuId": "sku-1", "disabledPlans": []}],
        "removeLicenses": [],
    }
    assert license_group(cast(Graph, fake), LICENCE_GROUP, apply=True).action == "same"
    assert len(fake.writes) == 1


def test_too_few_licences_are_flagged(policies: list[Policy]) -> None:
    fake = licensed_tenant(policies, members=18, free=10)
    assert (
        "only 10 of 25 licences are free"
        in license_group(cast(Graph, fake), LICENCE_GROUP, apply=False).message
    )


def test_status_counts_without_names(policies: list[Policy]) -> None:
    fake = licensed_tenant(policies)
    run(fake, policies, apply=True)
    fake.users = [
        {
            "id": "u1",
            "userPrincipalName": "ava.thompson@example.org",
            "onPremisesSyncEnabled": True,
            "assignedLicenses": [{"skuId": "sku-1"}],
        },
        {
            "id": "u2",
            "userPrincipalName": "kwame.mensah@example.org",
            "onPremisesSyncEnabled": True,
            "assignedLicenses": [],
        },
        {
            "id": "u3",
            "userPrincipalName": "lab.admin@example.org",
            "onPremisesSyncEnabled": None,
            "assignedLicenses": [],
        },
    ]
    fake.devices = [
        {
            "deviceName": "WS01",
            "osVersion": "10.0.26200.6899",
            "complianceState": "compliant",
            "isEncrypted": True,
            "lastSyncDateTime": "2026-10-28T21:00:00Z",
        }
    ]
    text = "\n".join(tenant_status(cast(Graph, fake), LICENCE_GROUP, policies))
    assert "Staff accounts synced from AD: 2, of whom 1 hold a licence." in text
    assert "| WS01 | 10.0.26200.6899 | compliant | yes | 2026-10-28T21:00 |" in text
    assert "| KBC - Windows compliance | 1 | 0 | 0 | 0 |" in text
    assert "@" not in text


# -- the Graph client itself -------------------------------------------------------------------------------


@pytest.fixture
def graph() -> Graph:
    return Graph("token", sleep=lambda _: None)


@respx.mock
def test_graph_follows_next_links(graph: Graph) -> None:
    route = respx.get("https://graph.microsoft.com/v1.0/users")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=x",
            },
        ),
        httpx.Response(200, json={"value": [{"id": "2"}]}),
    ]
    assert [u["id"] for u in graph.get_all("/v1.0/users")] == ["1", "2"]
    assert route.calls[1].request.url.params["$skiptoken"] == "x"


@respx.mock
def test_graph_waits_when_throttled(graph: Graph) -> None:
    waits: list[float] = []
    graph._sleep = waits.append
    route = respx.get("https://graph.microsoft.com/v1.0/subscribedSkus")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}, json={}),
        httpx.Response(200, json={"value": []}),
    ]
    assert graph.get_all("/v1.0/subscribedSkus") == []
    assert waits == [7.0]
    assert route.calls[0].request.headers["Authorization"] == "Bearer token"


@respx.mock
def test_graph_errors_carry_the_code(graph: Graph) -> None:
    respx.post("https://graph.microsoft.com/beta/deviceManagement/configurationPolicies").mock(
        return_value=httpx.Response(
            400, json={"error": {"code": "BadRequest", "message": "Request not applicable to target tenant."}}
        )
    )
    with pytest.raises(GraphError) as error:
        graph.post("/beta/deviceManagement/configurationPolicies", {})
    assert (error.value.status, error.value.code) == (400, "BadRequest")


def test_an_inactive_intune_tenant_gets_a_clear_message(policies: list[Policy]) -> None:
    class NoIntune(FakeGraph):
        def get_all(self, path: str, params: Any = None, *, headers: Any = None) -> list[dict[str, Any]]:
            raise GraphError(
                f"GET {path} answered 400 BadRequest: Request not applicable to target tenant.",
                400,
                "BadRequest",
            )

    with pytest.raises(IntuneError, match=r"start the Microsoft 365 trial"):
        run(NoIntune(catalogue_for(policies)), policies, apply=False)


def test_token_comes_from_the_environment_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_TOKEN", "from-env")
    assert token_from_azure_cli() == "from-env"
    assert odata_quote("O'Brien") == "'O''Brien'"


def test_intune_and_group_policy_agree(policies: list[Policy]) -> None:
    """Cloud-native and domain-joined PCs get the same sign-in notice and screen lock."""
    gpo = (REPO_ROOT / "lab" / "dc" / "04-Build-GroupPolicy.ps1").read_text(encoding="utf-8")
    notice = re.search(r"'LegalNoticeText' '([^']*)'", gpo)
    timeout = re.search(r"'InactivityTimeoutSecs' (\d+)", gpo)
    assert notice and timeout

    def setting(name: str) -> dict[str, Any]:
        return next(s for p in policies for s in p.settings if f"_{name}" in s["id"])

    assert setting("messagetextforusersattemptingtologon")["strings"] == [notice.group(1)]
    assert setting("machineinactivitylimit")["integer"] == int(timeout.group(1))
