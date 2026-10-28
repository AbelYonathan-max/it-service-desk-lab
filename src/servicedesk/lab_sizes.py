"""Chooses VM sizes for the lab that the subscription can actually run.

Free trials and student subscriptions have small vCPU quotas (4 per region on a free trial) and some
sizes are not offered to every subscription in every region. Before Terraform runs, the lab workflow
asks Azure what is available (``az vm list-skus``), how much quota is left (``az vm list-usage``) and
which lab VMs exist already (``az vm list -d``), and this module picks a size for each VM:

* a VM that exists keeps its size;
* a new VM gets the first size in its preference list that is offered here, supports Trusted Launch
  (Generation 2 with Secure Boot and vTPM), has at least 4 GB of memory, and fits in what is left of
  both the regional quota and its family's quota;
* if the Windows 11 PC does not fit, the lab is built without it rather than failing.

Usage (the lab workflow does this):
    python -m servicedesk.lab_sizes --skus skus.json --usage usage.json --vms vms.json --location uksouth
It prints ``key=value`` lines for ``$GITHUB_OUTPUT`` and explains its choices on standard error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DC_CANDIDATES = [
    "Standard_B2s",
    "Standard_B2als_v2",
    "Standard_B2as_v2",
    "Standard_B2ls_v2",
    "Standard_B2s_v2",
    "Standard_D2as_v5",
    "Standard_D2s_v5",
]
WS_CANDIDATES = DC_CANDIDATES
DC_VM = "vm-dc01"
WS_VM = "vm-ws01"
MIN_MEMORY_GB = 4.0
UNLIMITED = 1_000_000  # a quota Azure did not report


@dataclass(frozen=True)
class Sku:
    name: str
    family: str
    vcpus: int
    memory_gb: float
    gen2: bool
    trusted_launch: bool
    restricted: bool


@dataclass
class Choice:
    dc_size: str | None = None
    ws_size: str | None = None
    enable_ws01: bool = False
    notes: list[str] = field(default_factory=list)

    def outputs(self) -> dict[str, str]:
        return {
            "dc_size": self.dc_size or "",
            "ws_size": self.ws_size or "",
            "enable_ws01": "true" if self.enable_ws01 else "false",
        }


def _capability(raw: dict[str, Any], name: str) -> str | None:
    for item in raw.get("capabilities") or []:
        if item.get("name") == name:
            return str(item.get("value"))
    return None


def parse_skus(raw_skus: list[dict[str, Any]], location: str) -> dict[str, Sku]:
    """Turns ``az vm list-skus`` output into Sku records, keyed by size name."""
    skus: dict[str, Sku] = {}
    for raw in raw_skus:
        if raw.get("resourceType", "virtualMachines") != "virtualMachines":
            continue
        name = raw.get("name")
        if not name:
            continue
        restricted = any(
            r.get("type") == "Location" and location.lower() in [v.lower() for v in r.get("values") or []]
            for r in raw.get("restrictions") or []
        )
        skus[name] = Sku(
            name=name,
            family=raw.get("family") or "",
            vcpus=int(float(_capability(raw, "vCPUs") or 0)),
            memory_gb=float(_capability(raw, "MemoryGB") or 0),
            gen2="V2" in (_capability(raw, "HyperVGenerations") or ""),
            trusted_launch=(_capability(raw, "TrustedLaunchDisabled") or "False").lower() != "true",
            restricted=restricted,
        )
    return skus


def parse_free_quota(raw_usage: list[dict[str, Any]]) -> dict[str, int]:
    """Turns ``az vm list-usage`` output into free vCPUs per quota name ("cores" is the regional total)."""
    free: dict[str, int] = {}
    for item in raw_usage:
        name = (item.get("name") or {}).get("value")
        if name:
            free[name] = int(item.get("limit", 0)) - int(item.get("currentValue", 0))
    return free


def unusable_reason(sku: Sku | None) -> str | None:
    if sku is None:
        return "not offered in this region"
    if sku.restricted:
        return "not available to this subscription here"
    if not sku.gen2 or not sku.trusted_launch:
        return "no Trusted Launch (Secure Boot and vTPM)"
    if sku.memory_gb < MIN_MEMORY_GB:
        return f"only {sku.memory_gb:g} GB of memory"
    return None


def choose(
    skus: dict[str, Sku],
    free: dict[str, int],
    existing: dict[str, dict[str, Any]],
    want_ws: bool = True,
    dc_candidates: list[str] | None = None,
    ws_candidates: list[str] | None = None,
) -> Choice:
    """Picks sizes for the DC and the PC. ``existing`` maps VM name to its size and power state."""
    choice = Choice()
    # vCPUs still to find, per quota name: a deallocated VM needs its cores back when it starts.
    needed: dict[str, int] = defaultdict(int)

    def fits(sku: Sku) -> bool:
        return all(needed[quota] + sku.vcpus <= free.get(quota, UNLIMITED) for quota in ("cores", sku.family))

    def reserve(sku: Sku) -> None:
        needed["cores"] += sku.vcpus
        needed[sku.family] += sku.vcpus

    def keep_existing(vm: str) -> str | None:
        current = existing.get(vm)
        if not current:
            return None
        size = str(current.get("size"))
        if "deallocated" in str(current.get("power_state", "")).lower() and size in skus:
            reserve(skus[size])
        choice.notes.append(f"{vm} exists: keeping {size}")
        return size

    def pick(vm: str, candidates: list[str]) -> str | None:
        for name in candidates:
            sku = skus.get(name)
            reason = unusable_reason(sku)
            if sku is not None and reason is None and not fits(sku):
                reason = f"not enough vCPU quota left ({sku.family}, regional)"
            if sku is None or reason:
                choice.notes.append(f"{vm}: {name} skipped, {reason}")
                continue
            reserve(sku)
            choice.notes.append(f"{vm}: {name} ({sku.vcpus} vCPUs, {sku.memory_gb:g} GB)")
            return name
        return None

    choice.dc_size = keep_existing(DC_VM) or pick(DC_VM, dc_candidates or DC_CANDIDATES)
    if choice.dc_size is None:
        choice.notes.append(
            "No size fits for the domain controller. The lab needs 2 vCPUs of quota for it (4 with the PC): "
            "upgrade a free trial to pay-as-you-go, or ask for more quota in the portal (Quotas > Compute)."
        )
        return choice

    if want_ws:
        choice.ws_size = keep_existing(WS_VM) or pick(WS_VM, ws_candidates or WS_CANDIDATES)
        choice.enable_ws01 = choice.ws_size is not None
        if not choice.enable_ws01:
            choice.notes.append(
                "The Windows 11 PC does not fit in the quota left, so the lab is built without it."
            )
    return choice


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8") or "[]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--skus", required=True, help="output of az vm list-skus --resource-type virtualMachines"
    )
    parser.add_argument("--usage", required=True, help="output of az vm list-usage")
    parser.add_argument("--vms", required=True, help="output of az vm list --show-details for the lab group")
    parser.add_argument("--location", required=True)
    parser.add_argument("--no-ws", action="store_true", help="build the domain controller only")
    parser.add_argument("--dc-candidates", default="", help="comma-separated sizes to try for the DC")
    parser.add_argument("--ws-candidates", default="", help="comma-separated sizes to try for the PC")
    args = parser.parse_args(argv)

    existing = {
        vm["name"]: {
            "size": (vm.get("hardwareProfile") or {}).get("vmSize"),
            "power_state": vm.get("powerState", ""),
        }
        for vm in _load(args.vms)
    }
    choice = choose(
        parse_skus(_load(args.skus), args.location),
        parse_free_quota(_load(args.usage)),
        existing,
        want_ws=not args.no_ws,
        dc_candidates=[s.strip() for s in args.dc_candidates.split(",") if s.strip()] or None,
        ws_candidates=[s.strip() for s in args.ws_candidates.split(",") if s.strip()] or None,
    )
    for note in choice.notes:
        print(note, file=sys.stderr)
    for key, value in choice.outputs().items():
        print(f"{key}={value}")
    return 0 if choice.dc_size else 1


if __name__ == "__main__":
    raise SystemExit(main())
