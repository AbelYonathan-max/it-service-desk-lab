"""Chooses VM sizes for the lab that the subscription can actually run.

Free trials and student subscriptions have small vCPU quotas (4 per region on a free trial) and some
sizes are not offered to every subscription in every region. Before Terraform runs, the lab workflow
asks Azure what is available (``az vm list-skus``), how much quota is left (``az vm list-usage``) and
which lab VMs exist already (``az vm list -d``), and this module picks a size for each VM:

* a VM that exists keeps its size (its cores are in the usage figures already: Azure counts a
  deallocated VM against the quota too);
* a new VM gets the first size in its preference list that is offered here, supports Trusted Launch
  (Generation 2 with Secure Boot and vTPM), works with a SCSI disk controller, has at least 4 GB of
  memory, and fits in what is left of both the regional quota and its family's quota;
* if the Windows 11 PC does not fit, the lab is built without it rather than failing.

Usage (the lab workflow does this, region by region until one can take the domain controller):
    az vm list-skus --location uksouth --resource-type virtualMachines --all -o json > skus.json
    python -m servicedesk.lab_sizes --skus skus.json --usage usage.json --vms vms.json --location uksouth
``--all`` matters: without it the Azure CLI hides the sizes closed to the subscription, so the
reason for skipping them is lost. It prints ``key=value`` lines for ``$GITHUB_OUTPUT``, explains its
choices on standard error, and exits with 1 when the domain controller does not fit.

A stopped VM starts wherever Azure has room for its size, and sometimes there is none
(AllocationFailed). ``--replace VM --tried SIZES`` then picks the next size for that VM in the same
way, leaving out the sizes Azure had no room for and, because a Windows VM only resizes between sizes
that both have a local temporary disk or both lack one, the sizes of the other kind. It prints
``size=...`` (``infra/scripts/start-vms.sh`` moves the VM to it) and exits with 1 when none is left.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Two vCPUs each, cheapest first: burstable B sizes, then 4 GB general purpose, then 8 GB, then
# compute and memory optimised. Free trials cannot use every size in every region.
DC_CANDIDATES = [
    "Standard_B2s",
    "Standard_B2als_v2",
    "Standard_B2as_v2",
    "Standard_B2ls_v2",
    "Standard_B2s_v2",
    "Standard_B2ms",
    "Standard_D2als_v5",
    "Standard_D2ls_v5",
    "Standard_D2as_v5",
    "Standard_D2s_v5",
    "Standard_D2ads_v5",
    "Standard_D2ds_v5",
    "Standard_D2as_v4",
    "Standard_D2s_v4",
    "Standard_D2s_v3",
    "Standard_F2s_v2",
    "Standard_E2as_v5",
    "Standard_E2s_v5",
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
    scsi: bool = True  # newer sizes (v6) only take NVMe disks, which the lab's VMs do not use
    temp_disk: bool = False  # a local temporary disk (the D: drive on Windows)


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
            scsi="SCSI" in (_capability(raw, "DiskControllerTypes") or "SCSI"),
            temp_disk=float(_capability(raw, "MaxResourceVolumeMB") or 0) > 0,
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
    if not sku.scsi:
        return "NVMe disks only"
    if sku.memory_gb < MIN_MEMORY_GB:
        return f"only {sku.memory_gb:g} GB of memory"
    return None


@dataclass
class _Quota:
    """vCPUs promised so far against what is free, per quota name ("cores" is the regional total)."""

    free: dict[str, int]
    needed: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))

    def fits(self, sku: Sku) -> bool:
        return all(self.needed[q] + sku.vcpus <= self.free.get(q, UNLIMITED) for q in ("cores", sku.family))

    def reserve(self, sku: Sku) -> None:
        for quota in ("cores", sku.family):
            self.needed[quota] += sku.vcpus

    def release(self, sku: Sku) -> None:
        for quota in ("cores", sku.family):
            self.needed[quota] -= sku.vcpus


def _pick(
    vm: str, candidates: list[str], skus: dict[str, Sku], quota: _Quota, notes: list[str]
) -> str | None:
    """The first of ``candidates`` that the lab can use and that fits in the quota left, now reserved."""
    for name in candidates:
        sku = skus.get(name)
        reason = unusable_reason(sku)
        if sku is not None and reason is None and not quota.fits(sku):
            reason = f"not enough vCPU quota left ({sku.family}, regional)"
        if sku is None or reason:
            notes.append(f"{vm}: {name} skipped, {reason}")
            continue
        quota.reserve(sku)
        notes.append(f"{vm}: {name} ({sku.vcpus} vCPUs, {sku.memory_gb:g} GB)")
        return name
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
    quota = _Quota(free)

    def keep_existing(vm: str) -> str | None:
        current = existing.get(vm)
        if not current:
            return None
        size = str(current.get("size"))
        choice.notes.append(f"{vm} exists: keeping {size}")
        return size

    choice.dc_size = keep_existing(DC_VM) or _pick(
        DC_VM, dc_candidates or DC_CANDIDATES, skus, quota, choice.notes
    )
    if choice.dc_size is None:
        choice.notes.append("No size here fits the domain controller.")
        return choice

    if want_ws:
        choice.ws_size = keep_existing(WS_VM) or _pick(
            WS_VM, ws_candidates or WS_CANDIDATES, skus, quota, choice.notes
        )
        choice.enable_ws01 = choice.ws_size is not None
        if not choice.enable_ws01:
            choice.notes.append(
                "The Windows 11 PC does not fit in the quota left, so the lab is built without it."
            )
    return choice


def choose_replacement(
    skus: dict[str, Sku],
    free: dict[str, int],
    existing: dict[str, dict[str, Any]],
    vm: str,
    tried: set[str],
    candidates: list[str] | None = None,
) -> tuple[str | None, list[str]]:
    """Picks another size for ``vm``, a deallocated VM that Azure has no room to start in its own size.

    ``tried`` holds the sizes Azure has had no room for (the VM's own size counts as one). The other
    VMs keep their sizes. Returns the size, or None when nothing is left, and notes on the choice.
    """
    current = existing.get(vm)
    if not current:
        return None, [f"{vm} does not exist"]
    notes: list[str] = []
    quota = _Quota(free)
    old = skus.get(str(current.get("size")))
    if old is not None:
        # The usage figures count the VM in its present size until it moves.
        quota.release(old)
    no_room = tried | {str(current.get("size"))}
    usable = []
    for name in candidates or (DC_CANDIDATES if vm == DC_VM else WS_CANDIDATES):
        sku = skus.get(name)
        if name in no_room:
            notes.append(f"{vm}: {name} skipped, Azure has no room for it just now")
        elif sku is not None and old is not None and sku.temp_disk != old.temp_disk:
            notes.append(f"{vm}: {name} skipped, a temporary disk where {old.name} has none, or the reverse")
        else:
            usable.append(name)
    return _pick(vm, usable, skus, quota, notes), notes


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8") or "[]")


def _names(text: str) -> list[str] | None:
    """The names in a comma-separated list, or None when there are none."""
    return [name.strip() for name in text.split(",") if name.strip()] or None


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
    parser.add_argument(
        "--replace", metavar="VM", help="pick another size for this VM, which Azure has no room for"
    )
    parser.add_argument(
        "--tried", default="", help="with --replace: comma-separated sizes Azure had no room for"
    )
    args = parser.parse_args(argv)

    skus = parse_skus(_load(args.skus), args.location)
    free = parse_free_quota(_load(args.usage))
    existing = {
        vm["name"]: {
            "size": (vm.get("hardwareProfile") or {}).get("vmSize"),
            "power_state": vm.get("powerState", ""),
        }
        for vm in _load(args.vms)
    }
    dc_candidates = _names(args.dc_candidates)
    ws_candidates = _names(args.ws_candidates)

    if args.replace:
        size, notes = choose_replacement(
            skus,
            free,
            existing,
            args.replace,
            set(_names(args.tried) or []),
            dc_candidates if args.replace == DC_VM else ws_candidates,
        )
        for note in notes:
            print(note, file=sys.stderr)
        if size:
            print(f"size={size}")
        return 0 if size else 1

    choice = choose(
        skus, free, existing, want_ws=not args.no_ws, dc_candidates=dc_candidates, ws_candidates=ws_candidates
    )
    for note in choice.notes:
        print(note, file=sys.stderr)
    for key, value in choice.outputs().items():
        print(f"{key}={value}")
    return 0 if choice.dc_size else 1


if __name__ == "__main__":
    raise SystemExit(main())
