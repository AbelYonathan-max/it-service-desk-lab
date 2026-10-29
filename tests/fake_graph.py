"""An in-memory stand-in for the parts of Microsoft Graph that the Intune and licensing code use.

It keeps state like a tenant, so tests can apply policies, change them "in the portal" and check
that the next run puts them back. Like Graph, it echoes settings with extra fields of its own.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from servicedesk.graph import GraphError
from servicedesk.intune import Policy

DM = "/beta/deviceManagement/"
COLLECTIONS = ("deviceCompliancePolicies", "deviceConfigurations", "configurationPolicies")
CHOICE_DEFINITION = "#microsoft.graph.deviceManagementConfigurationChoiceSettingDefinition"
SIMPLE_DEFINITION = "#microsoft.graph.deviceManagementConfigurationSimpleSettingDefinition"
COLLECTION_DEFINITION = "#microsoft.graph.deviceManagementConfigurationSimpleSettingCollectionDefinition"
VALUE_DEFINITION = "#microsoft.graph.deviceManagementConfiguration{}SettingValueDefinition"


def catalogue_for(policies: list[Policy]) -> dict[str, dict[str, Any]]:
    """A settings catalogue that knows every setting the policies use."""
    catalogue: dict[str, dict[str, Any]] = {}

    def add(entry: dict[str, Any]) -> None:
        setting_id = entry["id"]
        if "choice" in entry:
            options = [f"{setting_id}_{v}" for v in ("0", "1", "2", "3", "4", "5", "6", "7", "true", "false")]
            catalogue[setting_id] = {
                "@odata.type": CHOICE_DEFINITION,
                "id": setting_id,
                "options": [{"itemId": o} for o in options],
            }
        else:
            kind = "Integer" if "integer" in entry else "String"
            value: dict[str, Any] = {"@odata.type": VALUE_DEFINITION.format(kind)}
            if kind == "Integer":
                value.update(minimumValue=1, maximumValue=100000)
            catalogue[setting_id] = {
                "@odata.type": COLLECTION_DEFINITION if "strings" in entry else SIMPLE_DEFINITION,
                "id": setting_id,
                "valueDefinition": value,
            }
        for child in entry.get("children", []):
            add(child)

    for policy in policies:
        for entry in policy.settings:
            add(entry)
    return catalogue


def decorate(instance: dict[str, Any]) -> dict[str, Any]:
    """Settings as Graph returns them: with template references and fields the request never sent."""
    echoed = copy.deepcopy(instance)
    echoed["settingInstanceTemplateReference"] = None
    choice = echoed.get("choiceSettingValue")
    if choice:
        choice["settingValueTemplateReference"] = None
        choice["children"] = [decorate(c) for c in choice.get("children", [])]
    return echoed


class FakeGraph:
    def __init__(self, catalogue: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.catalogue = dict(catalogue or {})
        self.policies: dict[str, dict[str, dict[str, Any]]] = {c: {} for c in COLLECTIONS}
        self.skus: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.members: dict[str, list[dict[str, Any]]] = {}
        self.users: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self._next = 0

    # -- helpers -------------------------------------------------------------------------------------

    def _id(self) -> str:
        self._next += 1
        return f"00000000-0000-0000-0000-{self._next:012d}"

    @staticmethod
    def _missing(path: str) -> GraphError:
        return GraphError(f"GET {path} answered 404 ResourceNotFound", 404, "ResourceNotFound")

    def policy(self, name: str) -> dict[str, Any]:
        for collection in self.policies.values():
            for record in collection.values():
                if name in (record.get("displayName"), record.get("name")):
                    return record
        raise KeyError(name)

    # -- the Graph interface ---------------------------------------------------------------------------

    def get(self, path: str, params: Mapping[str, str] | None = None, **kwargs: Any) -> Any:
        allow_not_found = kwargs.get("allow_not_found", False)
        if path.startswith(DM + "configurationSettings/"):
            found = self.catalogue.get(path.rsplit("/", 1)[1])
            if found is None and not allow_not_found:
                raise self._missing(path)
            return copy.deepcopy(found)
        match = re.fullmatch(DM + r"(\w+)/([\w-]+)(?:/(\w+))?", path)
        if match and match.group(1) in COLLECTIONS:
            record = self.policies[match.group(1)].get(match.group(2))
            if record is None:
                raise self._missing(path)
            if match.group(3) == "deviceStatusOverview":
                return {
                    "successCount": 1,
                    "failedCount": 0,
                    "errorCount": 0,
                    "pendingCount": 0,
                    "notApplicableCount": 0,
                }
            return {k: copy.deepcopy(v) for k, v in record.items() if not k.startswith("_")}
        raise self._missing(path)

    def get_all(
        self, path: str, params: Mapping[str, str] | None = None, *, headers: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        params = params or {}
        match = re.fullmatch(DM + r"(\w+)(?:/([\w-]+)/(\w+))?", path)
        if match and match.group(1) in COLLECTIONS:
            records = self.policies[match.group(1)]
            if match.group(2) is None:
                fields = params.get("$select", "").split(",")
                return [{f: r.get(f) for f in fields} for r in records.values()]
            record = records[match.group(2)]
            part = match.group(3)
            if part == "settings":
                return [
                    {"id": str(i), "settingInstance": decorate(s["settingInstance"])}
                    for i, s in enumerate(record["_settings"])
                ]
            if part == "assignments":
                return copy.deepcopy(record["_assignments"])
            if part == "scheduledActionsForRule":
                return copy.deepcopy(record["_rules"])
        if path == DM + "managedDevices":
            return copy.deepcopy(self.devices)
        if path == "/v1.0/subscribedSkus":
            return copy.deepcopy(self.skus)
        if path == "/v1.0/groups":
            name = re.fullmatch(r"displayName eq '(.*)'", params["$filter"])
            assert name is not None
            wanted = name.group(1).replace("''", "'")
            return [copy.deepcopy(g) for g in self.groups if g["displayName"] == wanted]
        match = re.fullmatch(r"/v1\.0/groups/([\w-]+)/(members|membersWithLicenseErrors)", path)
        if match:
            return copy.deepcopy(self.members.get(match.group(1), [])) if match.group(2) == "members" else []
        if path == "/v1.0/users":
            assert headers and headers.get("ConsistencyLevel") == "eventual", (
                "advanced queries need the header"
            )
            users = [u for u in self.users if u.get("onPremisesSyncEnabled")]
            if "assignedLicenses/$count ne 0" in params.get("$filter", ""):
                users = [u for u in users if u.get("assignedLicenses")]
            return users
        raise self._missing(path)

    def post(self, path: str, body: Any) -> Any:
        self.writes.append(("POST", path, copy.deepcopy(body)))
        match = re.fullmatch(DM + r"(\w+)", path)
        if match and match.group(1) in COLLECTIONS:
            record = copy.deepcopy(body)
            record["id"] = self._id()
            if match.group(1) == "configurationPolicies":
                for key in ("platforms", "technologies", "settings"):
                    assert key in record, f"a settings catalog policy needs {key}"
                record["_settings"] = record.pop("settings")
            else:
                assert "@odata.type" in record, "Graph needs the policy's @odata.type"
            if match.group(1) == "deviceCompliancePolicies":
                assert record.get("scheduledActionsForRule"), "a compliance policy needs a block action"
                record["_rules"] = record.pop("scheduledActionsForRule")
            record["_assignments"] = []
            self.policies[match.group(1)][record["id"]] = record
            return {k: v for k, v in record.items() if not k.startswith("_")}
        match = re.fullmatch(DM + r"(\w+)/([\w-]+)/(assign|scheduleActionsForRules)", path)
        if match:
            record = self.policies[match.group(1)][match.group(2)]
            if match.group(3) == "assign":
                record["_assignments"] = [{"id": self._id(), **a} for a in body["assignments"]]
            else:
                record["_rules"] = body["deviceComplianceScheduledActionForRules"]
            return None
        match = re.fullmatch(r"/v1\.0/groups/([\w-]+)/assignLicense", path)
        if match:
            group = next(g for g in self.groups if g["id"] == match.group(1))
            group["assignedLicenses"] = group.get("assignedLicenses", []) + [
                {"skuId": item["skuId"], "disabledPlans": item["disabledPlans"]}
                for item in body["addLicenses"]
            ]
            return copy.deepcopy(group)
        raise self._missing(path)

    def patch(self, path: str, body: Any) -> Any:
        self.writes.append(("PATCH", path, copy.deepcopy(body)))
        match = re.fullmatch(DM + r"(\w+)/([\w-]+)", path)
        assert match, path
        record = self.policies[match.group(1)][match.group(2)]
        assert body.get("@odata.type") == record.get("@odata.type"), "PATCH needs the policy's @odata.type"
        record.update(copy.deepcopy(body))
        return None

    def put(self, path: str, body: Any) -> Any:
        self.writes.append(("PUT", path, copy.deepcopy(body)))
        match = re.fullmatch(DM + r"configurationPolicies/([\w-]+)", path)
        assert match, path
        record = self.policies["configurationPolicies"][match.group(1)]
        record.update({k: copy.deepcopy(v) for k, v in body.items() if k != "settings"})
        record["_settings"] = copy.deepcopy(body["settings"])
        return None
