import json

import pytest

from servicedesk import lab_sizes
from servicedesk.lab_sizes import choose, choose_replacement, parse_free_quota, parse_skus


def sku(
    name, family, vcpus=2, memory=4, gens="V1,V2", tl_disabled=None, restricted_in=None, disks=None, temp_mb=0
):
    capabilities = [
        {"name": "vCPUs", "value": str(vcpus)},
        {"name": "MemoryGB", "value": str(memory)},
        {"name": "HyperVGenerations", "value": gens},
        {"name": "MaxResourceVolumeMB", "value": str(temp_mb)},
    ]
    if disks:
        capabilities.append({"name": "DiskControllerTypes", "value": disks})
    if tl_disabled:
        capabilities.append({"name": "TrustedLaunchDisabled", "value": "True"})
    restrictions = []
    if restricted_in:
        restrictions.append(
            {"type": "Location", "reasonCode": "NotAvailableForSubscription", "values": [restricted_in]}
        )
    return {
        "resourceType": "virtualMachines",
        "name": name,
        "family": family,
        "capabilities": capabilities,
        "restrictions": restrictions,
    }


def usage(cores_free=4, **families):
    rows = [{"name": {"value": "cores"}, "limit": cores_free, "currentValue": 0}]
    rows += [
        {"name": {"value": family}, "limit": limit, "currentValue": 0} for family, limit in families.items()
    ]
    return rows


CATALOGUE = [
    sku("Standard_B1ms", "standardBSFamily", vcpus=1, memory=2),
    sku("Standard_B2s", "standardBSFamily"),
    sku("Standard_B2als_v2", "standardBasv2Family"),
    sku("Standard_D2as_v5", "standardDASv5Family", memory=8),
    sku("Standard_A2_v2", "standardAv2Family", gens="V1"),
]


def test_free_trial_gets_two_b2s():
    choice = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(4, standardBSFamily=4)), {})
    assert (choice.dc_size, choice.ws_size, choice.enable_ws01) == ("Standard_B2s", "Standard_B2s", True)


def test_restricted_size_falls_through_to_the_next_one():
    catalogue = [sku("Standard_B2s", "standardBSFamily", restricted_in="uksouth"), *CATALOGUE[2:]]
    choice = choose(parse_skus(catalogue, "uksouth"), parse_free_quota(usage(4)), {})
    assert choice.dc_size == "Standard_B2als_v2"
    assert any("not available to this subscription" in note for note in choice.notes)


def test_restriction_in_another_region_does_not_count():
    catalogue = [sku("Standard_B2s", "standardBSFamily", restricted_in="westus")]
    choice = choose(parse_skus(catalogue, "uksouth"), parse_free_quota(usage(4)), {})
    assert choice.dc_size == "Standard_B2s"


def test_family_quota_spreads_the_vms_over_two_families():
    choice = choose(
        parse_skus(CATALOGUE, "uksouth"),
        parse_free_quota(usage(4, standardBSFamily=2, standardBasv2Family=2)),
        {},
    )
    assert (choice.dc_size, choice.ws_size) == ("Standard_B2s", "Standard_B2als_v2")


def test_pc_is_dropped_when_only_two_vcpus_are_free():
    choice = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(2)), {})
    assert choice.dc_size == "Standard_B2s"
    assert choice.ws_size is None and not choice.enable_ws01
    assert choice.outputs() == {"dc_size": "Standard_B2s", "ws_size": "", "enable_ws01": "false"}


def test_no_quota_at_all_means_no_domain_controller_here():
    choice = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(0)), {})
    assert choice.dc_size is None
    assert (
        "vm-dc01: Standard_B2s skipped, not enough vCPU quota left (standardBSFamily, regional)"
        in choice.notes
    )
    assert choice.notes[-1] == "No size here fits the domain controller."


def test_existing_vms_keep_their_size_and_a_deallocated_one_needs_its_cores_back():
    existing = {"vm-dc01": {"size": "Standard_D2as_v5", "power_state": "VM deallocated"}}
    choice = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(4)), existing)
    assert choice.dc_size == "Standard_D2as_v5"
    assert choice.ws_size == "Standard_B2s"

    tight = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(2)), existing)
    assert tight.dc_size == "Standard_D2as_v5" and tight.ws_size is None


def test_running_vm_is_already_counted_in_usage():
    existing = {"vm-dc01": {"size": "Standard_B2s", "power_state": "VM running"}}
    choice = choose(parse_skus(CATALOGUE, "uksouth"), parse_free_quota(usage(2)), existing)
    assert (choice.dc_size, choice.ws_size) == ("Standard_B2s", "Standard_B2s")


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (sku("Standard_B1ms", "f", vcpus=1, memory=2), "memory"),
        (sku("Standard_A2_v2", "f", gens="V1"), "Trusted Launch"),
        (sku("Standard_X2", "f", tl_disabled=True), "Trusted Launch"),
    ],
)
def test_unsuitable_sizes_are_skipped(entry, reason):
    skus = parse_skus([entry], "uksouth")
    assert reason in (lab_sizes.unusable_reason(skus[entry["name"]]) or "")


def test_cli_prints_github_outputs(tmp_path, capsys):
    (tmp_path / "skus.json").write_text(json.dumps(CATALOGUE))
    (tmp_path / "usage.json").write_text(json.dumps(usage(4)))
    (tmp_path / "vms.json").write_text("[]")
    code = lab_sizes.main(
        [
            "--skus",
            str(tmp_path / "skus.json"),
            "--usage",
            str(tmp_path / "usage.json"),
            "--vms",
            str(tmp_path / "vms.json"),
            "--location",
            "uksouth",
            "--ws-candidates",
            "Standard_D2as_v5",
        ]
    )
    out = capsys.readouterr()
    assert code == 0
    assert out.out.splitlines() == ["dc_size=Standard_B2s", "ws_size=Standard_D2as_v5", "enable_ws01=true"]
    assert "vm-dc01: Standard_B2s" in out.err


def test_sizes_that_only_take_nvme_disks_are_skipped():
    catalogue = [
        sku("Standard_D2als_v5", "standardDALSv5Family", disks="SCSI, NVMe"),
        sku("Standard_B2s", "standardBSFamily", restricted_in="uksouth"),
    ]
    nvme = sku("Standard_D2als_v6", "standardDALSv6Family", disks="NVMe")
    choice = choose(
        parse_skus([*catalogue, nvme], "uksouth"),
        parse_free_quota(usage(4)),
        {},
        dc_candidates=["Standard_D2als_v6", "Standard_B2s", "Standard_D2als_v5"],
    )
    assert choice.dc_size == "Standard_D2als_v5"
    assert "vm-dc01: Standard_D2als_v6 skipped, NVMe disks only" in choice.notes


def test_a_region_closed_to_the_subscription_fails_so_the_next_one_is_tried(tmp_path, capsys):
    closed = [sku(name, "standardBSFamily", restricted_in="uksouth") for name in lab_sizes.DC_CANDIDATES]
    files = {"skus": closed, "usage": usage(4), "vms": []}
    for name, content in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(content))
    args = [f"--{name}={tmp_path / f'{name}.json'}" for name in files]
    assert lab_sizes.main([*args, "--location", "uksouth"]) == 1
    assert "No size here fits the domain controller." in capsys.readouterr().err


# What the lab met in Sweden Central: no room for Standard_B2als_v2, which has no temporary disk.
SWEDEN = [
    sku("Standard_B2s", "standardBSFamily", temp_mb=8192),
    sku("Standard_B2als_v2", "standardBasv2Family"),
    sku("Standard_B2as_v2", "standardBasv2Family", memory=8),
    sku("Standard_B2ls_v2", "standardBsv2Family"),
    sku("Standard_B2ms", "standardBSFamily", memory=8, temp_mb=16384),
]
BOTH_STOPPED = {
    "vm-dc01": {"size": "Standard_B2als_v2", "power_state": "VM deallocated"},
    "vm-ws01": {"size": "Standard_B2als_v2", "power_state": "VM deallocated"},
}


def test_temporary_disk_comes_from_max_resource_volume():
    skus = parse_skus(SWEDEN, "swedencentral")
    assert skus["Standard_B2s"].temp_disk and not skus["Standard_B2als_v2"].temp_disk
    without = {**sku("Standard_X2", "f"), "capabilities": []}
    assert not parse_skus([without], "swedencentral")["Standard_X2"].temp_disk


def test_replacement_skips_its_own_size_and_sizes_with_a_temporary_disk():
    size, notes = choose_replacement(
        parse_skus(SWEDEN, "swedencentral"), parse_free_quota(usage(4)), BOTH_STOPPED, "vm-dc01", set()
    )
    assert size == "Standard_B2as_v2"
    assert "vm-dc01: Standard_B2als_v2 skipped, Azure has no room for it just now" in notes
    assert any(note.startswith("vm-dc01: Standard_B2s skipped, a temporary disk") for note in notes)


def test_replacement_leaves_out_tried_sizes_and_keeps_room_for_the_other_stopped_vm():
    skus = parse_skus(SWEDEN, "swedencentral")
    # The PC still needs 2 of the Basv2 family's 2 vCPUs, so the DC has to move to another family.
    size, _ = choose_replacement(
        skus, parse_free_quota(usage(4, standardBasv2Family=2)), BOTH_STOPPED, "vm-dc01", set()
    )
    assert size == "Standard_B2ls_v2"

    size, _ = choose_replacement(
        skus, parse_free_quota(usage(4)), BOTH_STOPPED, "vm-ws01", {"Standard_B2as_v2"}
    )
    assert size == "Standard_B2ls_v2"


def test_replacement_counts_a_running_vm_through_usage_only():
    running = {**BOTH_STOPPED, "vm-dc01": {"size": "Standard_B2ls_v2", "power_state": "VM running"}}
    usage_now = [{"name": {"value": "cores"}, "limit": 4, "currentValue": 2}]
    size, _ = choose_replacement(
        parse_skus(SWEDEN, "swedencentral"), parse_free_quota(usage_now), running, "vm-ws01", set()
    )
    assert size == "Standard_B2as_v2"


def test_no_replacement_when_every_size_is_used_up():
    tried = {"Standard_B2as_v2", "Standard_B2ls_v2"}
    size, notes = choose_replacement(
        parse_skus(SWEDEN, "swedencentral"), parse_free_quota(usage(4)), BOTH_STOPPED, "vm-dc01", tried
    )
    assert size is None
    assert any(note.startswith("vm-dc01: Standard_B2ms skipped, a temporary disk") for note in notes)
    assert choose_replacement({}, {}, {}, "vm-dc01", set()) == (None, ["vm-dc01 does not exist"])


def test_cli_replace_prints_the_new_size(tmp_path, capsys):
    vms = [
        {"name": name, "hardwareProfile": {"vmSize": vm["size"]}, "powerState": vm["power_state"]}
        for name, vm in BOTH_STOPPED.items()
    ]
    files = {"skus": SWEDEN, "usage": usage(4), "vms": vms}
    for name, content in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(content))
    args = [f"--{name}={tmp_path / f'{name}.json'}" for name in files] + ["--location", "swedencentral"]

    assert lab_sizes.main([*args, "--replace", "vm-ws01", "--tried", "Standard_B2as_v2"]) == 0
    assert capsys.readouterr().out.splitlines() == ["size=Standard_B2ls_v2"]

    assert (
        lab_sizes.main([*args, "--replace", "vm-ws01", "--tried", "Standard_B2as_v2,Standard_B2ls_v2"]) == 1
    )
    out = capsys.readouterr()
    assert out.out == ""
    assert "vm-ws01: Standard_B2ls_v2 skipped, Azure has no room for it just now" in out.err
