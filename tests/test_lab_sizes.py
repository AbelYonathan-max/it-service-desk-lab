import json

import pytest

from servicedesk import lab_sizes
from servicedesk.lab_sizes import choose, parse_free_quota, parse_skus


def sku(name, family, vcpus=2, memory=4, gens="V1,V2", tl_disabled=None, restricted_in=None, disks=None):
    capabilities = [
        {"name": "vCPUs", "value": str(vcpus)},
        {"name": "MemoryGB", "value": str(memory)},
        {"name": "HyperVGenerations", "value": gens},
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
