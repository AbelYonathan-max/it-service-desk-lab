"""Microsoft 365 licensing and the tenant's health, for the "Entra and Intune" workflow.

Licences use group-based licensing: Microsoft 365 Business Premium is given to the AD group
GG-LIC-M365-BusinessPremium once Entra Cloud Sync has copied it to Entra ID. After that, nobody
assigns a licence by hand: a starter added to the group in AD gets one at the next sync, and a
leaver taken out of it gives theirs back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from servicedesk.graph import Graph, GraphError, odata_quote
from servicedesk.intune import Policy

BUSINESS_PREMIUM = "SPB"  # skuPartNumber of Microsoft 365 Business Premium, trial included
EVENTUAL = {"ConsistencyLevel": "eventual"}  # Graph's "advanced query" header, needed to filter on sync


@dataclass(frozen=True)
class Licensing:
    action: str  # assign, same or waiting
    message: str


def find_sku(graph: Graph, part_number: str = BUSINESS_PREMIUM) -> dict[str, Any] | None:
    return next(
        (s for s in graph.get_all("/v1.0/subscribedSkus") if s.get("skuPartNumber") == part_number), None
    )


def find_group(graph: Graph, name: str) -> dict[str, Any] | None:
    groups = graph.get_all(
        "/v1.0/groups",
        {
            "$filter": f"displayName eq {odata_quote(name)}",
            "$select": "id,displayName,onPremisesSyncEnabled,assignedLicenses",
        },
    )
    return groups[0] if groups else None


def license_group(graph: Graph, group_name: str, *, apply: bool) -> Licensing:
    """Gives the licence group Microsoft 365 Business Premium, once both exist in the tenant."""
    sku = find_sku(graph)
    if sku is None:
        return Licensing(
            "waiting",
            "The tenant has no Microsoft 365 Business Premium licences yet: start the trial (docs/setup.md, step 3).",
        )
    group = find_group(graph, group_name)
    if group is None:
        return Licensing(
            "waiting",
            f"{group_name} is not in Entra ID yet. Entra Cloud Sync copies it from AD once it is connected "
            "with the Licensing OU in scope (docs/setup.md, step 4).",
        )
    members = graph.get_all(f"/v1.0/groups/{group['id']}/members", {"$select": "id"})
    units = int((sku.get("prepaidUnits") or {}).get("enabled", 0))
    used = int(sku.get("consumedUnits", 0))
    if any(item.get("skuId") == sku["skuId"] for item in group.get("assignedLicenses") or []):
        return Licensing(
            "same",
            f"{group_name} ({len(members)} members) gives Business Premium; {used} of {units} licences in use.",
        )
    if apply:
        graph.post(
            f"/v1.0/groups/{group['id']}/assignLicense",
            {"addLicenses": [{"skuId": sku["skuId"], "disabledPlans": []}], "removeLicenses": []},
        )
    short = units - used < len(members)
    return Licensing(
        "assign",
        f"{group_name} ({len(members)} members) {'now gives' if apply else 'would give'} Business Premium"
        + (f"; only {units - used} of {units} licences are free." if short else "."),
    )


def _count(graph: Graph, path: str, params: dict[str, str]) -> int:
    return len(graph.get_all(path, {**params, "$count": "true"}, headers=EVENTUAL))


def tenant_status(graph: Graph, group_name: str, policies: list[Policy]) -> list[str]:
    """A short Markdown report: staff synced from AD, licences, PCs and how the policies are doing.

    No names or sign-in addresses: the repository and its workflow logs are public.
    """
    lines = ["### Entra ID and Intune", ""]
    synced = _count(graph, "/v1.0/users", {"$filter": "onPremisesSyncEnabled eq true", "$select": "id"})
    licensed = _count(
        graph,
        "/v1.0/users",
        {"$filter": "onPremisesSyncEnabled eq true and assignedLicenses/$count ne 0", "$select": "id"},
    )
    lines.append(f"- Staff accounts synced from AD: {synced}, of whom {licensed} hold a licence.")
    group = find_group(graph, group_name)
    if group is None:
        lines.append(f"- {group_name} has not synced from AD yet.")
    else:
        try:
            errors = graph.get_all(f"/v1.0/groups/{group['id']}/membersWithLicenseErrors", {"$select": "id"})
            lines.append(f"- {group_name}: licence errors for {len(errors)} member(s).")
        except GraphError as error:  # a report line is not worth failing the run for
            lines.append(f"- {group_name}: licence errors not readable ({error.code or error.status}).")
    sku = find_sku(graph)
    if sku:
        units = int((sku.get("prepaidUnits") or {}).get("enabled", 0))
        lines.append(f"- Business Premium: {sku.get('consumedUnits', 0)} of {units} licences in use.")

    devices = graph.get_all(
        "/beta/deviceManagement/managedDevices",
        {"$select": "deviceName,operatingSystem,osVersion,complianceState,isEncrypted,lastSyncDateTime"},
    )
    lines += ["", "| PC | Windows | Compliance | Encrypted | Last check-in (UTC) |", "|---|---|---|---|---|"]
    for device in sorted(devices, key=lambda d: str(d.get("deviceName"))):
        lines.append(
            f"| {device.get('deviceName')} | {device.get('osVersion')} | {device.get('complianceState')} "
            f"| {'yes' if device.get('isEncrypted') else 'no'} | {str(device.get('lastSyncDateTime', ''))[:16]} |"
        )
    if not devices:
        lines.append("| none enrolled yet | | | | |")

    lines += [
        "",
        "| Policy | Succeeded | Failed or error | Pending | Not applicable |",
        "|---|---|---|---|---|",
    ]
    for policy in policies:
        name_field = "name" if policy.kind == "settings" else "displayName"
        found = [
            p
            for p in graph.get_all(policy.collection, {"$select": f"id,{name_field}"})
            if p.get(name_field) == policy.name
        ]
        if not found:
            lines.append(f"| {policy.name} | not created yet | | | |")
        elif policy.kind == "settings":
            # Settings catalog status lives in Intune's reports, not in a simple overview.
            lines.append(f"| {policy.name} | see Intune admin centre > Devices > Configuration | | | |")
        else:
            overview = graph.get(f"{policy.collection}/{found[0]['id']}/deviceStatusOverview")
            failed = int(overview.get("failedCount", 0)) + int(overview.get("errorCount", 0))
            lines.append(
                f"| {policy.name} | {overview.get('successCount', 0)} | {failed} "
                f"| {overview.get('pendingCount', 0)} | {overview.get('notApplicableCount', 0)} |"
            )
    return lines
