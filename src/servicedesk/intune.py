"""Intune policies as code: lab/intune/*.yaml, applied through Microsoft Graph.

Each policy goes in the Graph collection the Intune admin centre itself uses:

- ``compliance``: deviceManagement/deviceCompliancePolicies
- ``update-ring``: deviceManagement/deviceConfigurations (Windows Update for Business)
- ``settings``: deviceManagement/configurationPolicies (the settings catalog)

``plan`` compares each policy with the tenant and lists what ``apply`` would change; ``apply`` creates
what is missing, puts back anything changed by hand in the portal, and sets the assignments.
Policies are matched by name. Every settings catalog setting is first checked against the tenant's
own catalogue, so a wrong setting ID or value fails with a clear message before anything changes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from servicedesk.graph import Graph, GraphError
from servicedesk.people import REPO_ROOT

INTUNE_DIR = REPO_ROOT / "lab" / "intune"
DEVICE_MANAGEMENT = "/beta/deviceManagement"
COLLECTIONS = {
    "compliance": "deviceCompliancePolicies",
    "update-ring": "deviceConfigurations",
    "settings": "configurationPolicies",
}
TARGETS = {
    "all devices": "#microsoft.graph.allDevicesAssignmentTarget",
    "all users": "#microsoft.graph.allLicensedUsersAssignmentTarget",
}
NO_TEMPLATE = "00000000-0000-0000-0000-000000000000"
SETTING = "#microsoft.graph.deviceManagementConfiguration"


@dataclass(frozen=True)
class Policy:
    kind: str
    name: str
    description: str
    assign: str
    source: str
    properties: dict[str, Any] = field(default_factory=dict)  # compliance and update-ring
    settings: list[dict[str, Any]] = field(default_factory=list)  # settings, in the YAML's short form
    grace_hours: int = 0  # compliance: hours before a failing PC is marked non-compliant

    @property
    def collection(self) -> str:
        return f"{DEVICE_MANAGEMENT}/{COLLECTIONS[self.kind]}"

    @property
    def target(self) -> str:
        return TARGETS[self.assign]

    def body(self) -> dict[str, Any]:
        """The Graph body that creates the policy."""
        if self.kind == "settings":
            return {
                "name": self.name,
                "description": self.description,
                "platforms": "windows10",
                "technologies": "mdm",
                "roleScopeTagIds": ["0"],
                "settings": [
                    {"@odata.type": f"{SETTING}Setting", "settingInstance": setting_instance(s)}
                    for s in self.settings
                ],
            }
        body = {"displayName": self.name, "description": self.description, **self.properties}
        if self.kind == "compliance":
            body["scheduledActionsForRule"] = [block_after(self.grace_hours)]
        return body


def block_after(hours: int) -> dict[str, Any]:
    """The compliance action every policy needs: mark the device non-compliant after ``hours``."""
    return {
        "ruleName": "PasswordRequired",  # the name Intune gives the default rule, whatever it checks
        "scheduledActionConfigurations": [
            {
                "actionType": "block",
                "gracePeriodHours": hours,
                "notificationTemplateId": NO_TEMPLATE,
                "notificationMessageCCList": [],
            }
        ],
    }


def choice_value(entry: dict[str, Any]) -> str:
    """``{id}_{choice}``: the option's ID in the catalogue, as the portal stores it."""
    choice = entry["choice"]
    suffix = str(choice).lower() if isinstance(choice, bool) else str(choice)
    return f"{entry['id']}_{suffix}"


def setting_instance(entry: dict[str, Any]) -> dict[str, Any]:
    """A setting in the short form (id plus choice, integer, string or strings) as a Graph setting
    instance. ``strings`` is for the settings the catalogue keeps as a list of text values."""
    setting_id = entry["id"]
    if "choice" in entry:
        return {
            "@odata.type": f"{SETTING}ChoiceSettingInstance",
            "settingDefinitionId": setting_id,
            "choiceSettingValue": {
                "@odata.type": f"{SETTING}ChoiceSettingValue",
                "value": choice_value(entry),
                "children": [setting_instance(child) for child in entry.get("children", [])],
            },
        }
    if "strings" in entry:
        return {
            "@odata.type": f"{SETTING}SimpleSettingCollectionInstance",
            "settingDefinitionId": setting_id,
            "simpleSettingCollectionValue": [
                {"@odata.type": f"{SETTING}StringSettingValue", "value": value} for value in entry["strings"]
            ],
        }
    kind, value = ("Integer", entry["integer"]) if "integer" in entry else ("String", entry["string"])
    return {
        "@odata.type": f"{SETTING}SimpleSettingInstance",
        "settingDefinitionId": setting_id,
        "simpleSettingValue": {"@odata.type": f"{SETTING}{kind}SettingValue", "value": value},
    }


def _check_short_form(entries: Any, where: str) -> None:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{where}: settings must be a list.")
    for entry in entries:
        kinds = [k for k in ("choice", "integer", "string", "strings") if k in entry]
        if not isinstance(entry.get("id"), str) or len(kinds) != 1:
            raise ValueError(
                f"{where}: each setting needs an id and one of choice, integer, string or strings: {entry}"
            )
        if entry["id"] != entry["id"].lower():
            raise ValueError(f"{where}: setting IDs are lower case: {entry['id']}")
        if "integer" in entry and not isinstance(entry["integer"], int):
            raise ValueError(f"{where}: {entry['id']} needs a whole number.")
        strings = entry.get("strings", [""])
        if not isinstance(strings, list) or not strings or not all(isinstance(v, str) for v in strings):
            raise ValueError(f"{where}: {entry['id']} needs a list of text for strings.")
        if "children" in entry:
            if "choice" not in entry:
                raise ValueError(f"{where}: only a choice setting can have children: {entry['id']}")
            _check_short_form(entry["children"], where)


def load_policies(directory: Path | None = None) -> list[Policy]:
    folder = directory or INTUNE_DIR
    policies: list[Policy] = []
    for path in sorted(folder.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        where = path.name
        kind = raw.get("kind")
        if kind not in COLLECTIONS:
            raise ValueError(f"{where}: kind must be one of {', '.join(COLLECTIONS)}.")
        if raw.get("assign") not in TARGETS:
            raise ValueError(f"{where}: assign must be one of {', '.join(TARGETS)}.")
        if kind == "settings":
            _check_short_form(raw.get("settings"), where)
        elif "@odata.type" not in raw.get("properties", {}):
            raise ValueError(f"{where}: properties need an @odata.type.")
        policies.append(
            Policy(
                kind=kind,
                name=str(raw["name"]),
                description=" ".join(str(raw.get("description", "")).split()),
                assign=raw["assign"],
                source=where,
                properties=dict(raw.get("properties", {})),
                settings=list(raw.get("settings", [])),
                grace_hours=int(raw.get("grace_hours", 0)),
            )
        )
    names = [p.name for p in policies]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Two policies share a name: {', '.join(duplicates)}.")
    return policies


# -- checking settings against the tenant's catalogue -------------------------------------------------


def check_settings(graph: Graph, policies: list[Policy]) -> list[str]:
    """Problems with settings catalog entries: unknown IDs, values the setting does not offer."""
    definitions: dict[str, Any] = {}
    problems: list[str] = []

    def definition(setting_id: str) -> Any:
        if setting_id not in definitions:
            try:
                definitions[setting_id] = graph.get(
                    f"{DEVICE_MANAGEMENT}/configurationSettings/{setting_id}", allow_not_found=True
                )
            except GraphError as error:
                if error.status != 400:  # an ID the catalogue cannot even parse is simply unknown
                    raise
                definitions[setting_id] = None
        return definitions[setting_id]

    def value_type(found: Any) -> str:
        return str((found.get("valueDefinition") or {}).get("@odata.type", ""))

    def renamed(setting_id: str, fits: Callable[[Any], bool]) -> str | None:
        """The catalogue's usual variants of an ID: children of ADMX-backed settings end in _name, and
        a setting Microsoft had to redefine comes back with _v2."""
        for candidate in (f"{setting_id}_name", f"{setting_id}_v2"):
            found = definition(candidate)
            if found and fits(found):
                return candidate
        return None

    def check(
        entry: dict[str, Any], where: str, siblings: list[str] | None = None, parent: str | None = None
    ) -> None:
        """``parent`` is the option the entry sits under, and ``siblings`` the settings that option has."""
        setting_id = entry["id"]
        found = definition(setting_id)
        if not found:
            problem = f"{where}: {setting_id} is not in this tenant's settings catalog."
            hint = renamed(setting_id, lambda _: True)
            if hint:
                problem += f" It has {hint}."
            elif siblings:
                problem += f" The option above it has these settings: {', '.join(siblings)}."
            problems.append(problem)
            return
        odata = str(found.get("@odata.type", ""))
        options = found.get("options") or []
        if "choice" in entry:
            option_ids = [str(o.get("itemId")) for o in options]
            if "Choice" not in odata:
                problems.append(f"{where}: {setting_id} is not a choice setting.")
            elif choice_value(entry) not in option_ids:
                problems.append(
                    f"{where}: {setting_id} has no option {choice_value(entry)} "
                    f"(it offers {', '.join(o.removeprefix(setting_id) for o in option_ids)})."
                )
        elif "integer" in entry:
            limits = found.get("valueDefinition") or {}
            low, high = limits.get("minimumValue"), limits.get("maximumValue")
            if "Integer" not in value_type(found):
                hint = renamed(setting_id, lambda other: "Integer" in value_type(other))
                problems.append(
                    f"{where}: {setting_id} does not take a whole number."
                    + (f" {hint} does." if hint else "")
                )
            elif (low is not None and entry["integer"] < low) or (
                high is not None and entry["integer"] > high
            ):
                problems.append(f"{where}: {setting_id} must be between {low} and {high}.")
        elif "String" not in value_type(found):
            problems.append(f"{where}: {setting_id} does not take text.")
        elif ("strings" in entry) != ("Collection" in odata):
            wanted = "a list of text (strings)" if "Collection" in odata else "a single text (string)"
            problems.append(f"{where}: {setting_id} takes {wanted}.")
        # Some settings only exist under another setting's option, which Graph checks on every write.
        chosen: dict[str, Any] = {}
        if "choice" in entry:
            chosen = next((o for o in options if o.get("itemId") == choice_value(entry)), {})
        parents = [str(d.get("dependentOn")) for d in (chosen or found).get("dependentOn") or []]
        if parents and parent not in parents:
            problems.append(f"{where}: {setting_id} only goes under {' or '.join(parents)}.")
        children = [str(d.get("dependedOnBy")) for d in chosen.get("dependedOnBy") or []]
        for child in entry.get("children", []):
            check(child, where, children, choice_value(entry))

    for policy in policies:
        for entry in policy.settings:
            check(entry, policy.source)
    return problems


# -- comparing with what the tenant has ----------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Values as Graph might echo them: times lose a zero fraction ("08:00:00.0000000" -> "08:00:00")."""
    if isinstance(value, str):
        return re.sub(r"(\d\d:\d\d:\d\d)\.0+$", r"\1", value)
    return value


def differences(desired: dict[str, Any], current: dict[str, Any], prefix: str = "") -> list[str]:
    """Properties of ``desired`` that ``current`` does not match (``@odata.type`` is not compared)."""
    found = []
    for key, value in desired.items():
        if key == "@odata.type":
            continue
        have = current.get(key)
        if isinstance(value, dict) and isinstance(have, dict):
            found.extend(differences(value, have, f"{prefix}{key}."))
        elif _plain(value) != _plain(have):
            found.append(f"{prefix}{key}: {json.dumps(have)} -> {json.dumps(value)}")
    return found


def _instance_key(instance: dict[str, Any]) -> tuple[str, str, tuple[Any, ...]]:
    setting_id = str(instance.get("settingDefinitionId", ""))
    choice = instance.get("choiceSettingValue")
    if choice:
        children = tuple(sorted(_instance_key(c) for c in choice.get("children") or []))
        return setting_id, json.dumps(choice.get("value")), children
    simple = instance.get("simpleSettingValue")
    if simple:
        return setting_id, json.dumps(simple.get("value")), ()
    collection = instance.get("simpleSettingCollectionValue")
    if collection is not None:
        return setting_id, json.dumps([v.get("value") for v in collection]), ()
    return setting_id, json.dumps(instance, sort_keys=True), ()


def settings_key(settings: list[dict[str, Any]]) -> list[tuple[str, str, tuple[Any, ...]]]:
    """Settings in a form that compares equal however Graph orders them or decorates them."""
    return sorted(_instance_key(s["settingInstance"]) for s in settings)


# -- plan and apply -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    policy: str
    action: str  # create, update, assign or same
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.action:<7} {self.policy}" + (f": {self.detail}" if self.detail else "")


@dataclass
class Result:
    changes: list[Change] = field(default_factory=list)

    @property
    def changed(self) -> list[Change]:
        return [c for c in self.changes if c.action != "same"]


class IntuneError(RuntimeError):
    """The tenant cannot take the policies yet, or a policy is wrong; the message says what to do."""


def explain(error: GraphError) -> IntuneError:
    text = str(error)
    if error.status in {401, 403}:
        hint = (
            "The deploy identity is not allowed to manage Intune. Run the set-up command again "
            "(docs/setup.md, step 2): it grants the Microsoft Graph permissions."
        )
    elif "not applicable to target tenant" in text.lower():
        hint = (
            "Intune is not active in this tenant yet: start the Microsoft 365 trial (docs/setup.md, step 3)."
        )
    else:
        hint = "Graph refused the request."
    return IntuneError(f"{hint} ({text})")


class Intune:
    def __init__(self, graph: Graph, progress: Callable[[str], None] = lambda _: None) -> None:
        self.graph = graph
        self.progress = progress

    def existing(self, policy: Policy) -> dict[str, Any] | None:
        name_field = "name" if policy.kind == "settings" else "displayName"
        matches = [
            item
            for item in self.graph.get_all(policy.collection, {"$select": f"id,{name_field}"})
            if item.get(name_field) == policy.name
        ]
        if len(matches) > 1:
            raise IntuneError(
                f"The tenant has {len(matches)} policies called {policy.name}: delete the extras."
            )
        return matches[0] if matches else None

    def plan_policy(self, policy: Policy, apply: bool) -> list[Change]:
        found = self.existing(policy)
        if found is None:
            if apply:
                created = self.graph.post(policy.collection, policy.body())
                self._assign(policy, str(created["id"]))
            return [Change(policy.name, "create", f"{policy.kind}, assigned to {policy.assign}")]

        policy_id = str(found["id"])
        url = f"{policy.collection}/{policy_id}"
        changes: list[Change] = []
        if policy.kind == "settings":
            current = self.graph.get(url)
            settings = self.graph.get_all(f"{url}/settings")
            desired = policy.body()
            what = []
            if current.get("description") != policy.description:
                what.append("description")
            have, want = set(settings_key(settings)), set(settings_key(desired["settings"]))
            if have != want:
                names = sorted({key[0].removeprefix("device_vendor_msft_") for key in have ^ want})
                what.append("settings " + ", ".join(names))
            if what:
                if apply:
                    self.graph.put(url, desired)
                changes.append(
                    Change(policy.name, "update", "; ".join(what) + " (differ from the repository)")
                )
        else:
            current = self.graph.get(url)
            desired = {"description": policy.description, **policy.properties}
            found_differences = differences(desired, current)
            if found_differences:
                if apply:
                    changed = {k: v for k, v in desired.items() if differences({k: v}, current)}
                    self.graph.patch(url, {"@odata.type": policy.properties["@odata.type"], **changed})
                changes.append(Change(policy.name, "update", "; ".join(found_differences)))
            if policy.kind == "compliance":
                changes.extend(self._grace_period(policy, url, apply))

        assigned = {
            str(a.get("target", {}).get("@odata.type")) for a in self.graph.get_all(f"{url}/assignments")
        }
        if assigned != {policy.target}:
            if apply:
                self._assign(policy, policy_id)
            changes.append(Change(policy.name, "assign", f"to {policy.assign}"))
        return changes or [Change(policy.name, "same")]

    def _grace_period(self, policy: Policy, url: str, apply: bool) -> list[Change]:
        # Graph has no route to the rules on their own: they only come expanded with the policy.
        expanded = self.graph.get(
            url, {"$expand": "scheduledActionsForRule($expand=scheduledActionConfigurations)"}
        )
        rules = expanded.get("scheduledActionsForRule") or []
        blocks = [
            a.get("gracePeriodHours")
            for rule in rules
            for a in rule.get("scheduledActionConfigurations") or []
            if a.get("actionType") == "block"
        ]
        if blocks == [policy.grace_hours]:
            return []
        if apply:
            self.graph.post(
                f"{url}/scheduleActionsForRules",
                {"deviceComplianceScheduledActionForRules": [block_after(policy.grace_hours)]},
            )
        return [Change(policy.name, "update", f"non-compliant after {policy.grace_hours} hours")]

    def _assign(self, policy: Policy, policy_id: str) -> None:
        self.graph.post(
            f"{policy.collection}/{policy_id}/assign",
            {"assignments": [{"target": {"@odata.type": policy.target}}]},
        )

    def run(self, policies: list[Policy], *, apply: bool) -> Result:
        result = Result()
        try:
            problems = check_settings(self.graph, [p for p in policies if p.kind == "settings"])
            if problems:
                raise IntuneError(
                    "Some settings do not match the tenant's catalogue:\n  " + "\n  ".join(problems)
                )
            for policy in policies:
                self.progress(f"{policy.name} ({policy.source})")
                result.changes.extend(self.plan_policy(policy, apply))
        except GraphError as error:
            raise explain(error) from error
        return result
