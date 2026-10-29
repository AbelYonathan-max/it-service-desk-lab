#!/usr/bin/env bash
# start-vms.sh RESOURCE_GROUP [VM ...]: starts the lab VMs that are not running and waits for them.
# With no VM names it starts every VM in the resource group.
#
# A deallocated VM starts wherever Azure has room for its size, and sometimes there is none
# (AllocationFailed). Then the VM moves to the next size the lab can use, which
# src/servicedesk/lab_sizes.py picks (from DC_SIZES or WS_SIZES when they are set), and starts again.
# The lab workflow keeps whatever size a VM has, so the move sticks.
set -euo pipefail

rg="$1"
shift
vms=("$@")
if [ ${#vms[@]} -eq 0 ]; then
  mapfile -t vms < <(az vm list -g "$rg" --query "[].name" -o tsv)
fi
src="$(cd "$(dirname "$0")/../.." && pwd)/src"
work=$(mktemp -d)
# Azure's ways of saying it has no room for a size there just now.
no_room='AllocationFailed|ZonalAllocationFailed|OverconstrainedAllocationRequest|OverconstrainedZonalAllocationRequest|SkuNotAvailable'

# All at once. A plain `az vm start` waits for Azure's answer to this start, where `az vm wait` can
# read the failure left over from an earlier one.
declare -A starting
for vm in "${vms[@]}"; do
  state=$(az vm get-instance-view -g "$rg" -n "$vm" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" -o tsv)
  echo "$vm: $state"
  if [ "$state" != "VM running" ]; then
    az vm start -g "$rg" -n "$vm" -o none >"$work/$vm.log" 2>&1 &
    starting[$vm]=$!
  fi
done

failed=0
full=()
for vm in "${vms[@]}"; do
  if [ -z "${starting[$vm]:-}" ]; then continue; fi
  if wait "${starting[$vm]}"; then
    echo "$vm is running"
  elif grep -qE "$no_room" "$work/$vm.log"; then
    echo "$vm: Azure has no room for its size just now:"
    cat "$work/$vm.log"
    full+=("$vm")
  else
    echo "::error::$vm did not start:"
    cat "$work/$vm.log"
    failed=1
  fi
done

# Sizes Azure has had no room for in this run, comma-separated.
tried=""
for vm in "${full[@]}"; do
  location=$(az vm show -g "$rg" -n "$vm" --query location -o tsv)
  size=$(az vm show -g "$rg" -n "$vm" --query hardwareProfile.vmSize -o tsv)
  if [ ! -s "$work/skus-$location.json" ]; then
    az vm list-skus --location "$location" --resource-type virtualMachines --all -o json >"$work/skus-$location.json"
  fi
  while :; do
    case ",$tried," in *",$size,"*) ;; *) tried="${tried:+$tried,}$size" ;; esac
    az vm list -g "$rg" --show-details -o json >"$work/vms.json"
    az vm list-usage --location "$location" -o json >"$work/usage.json"
    if ! next=$(PYTHONPATH="$src" python3 -m servicedesk.lab_sizes --skus "$work/skus-$location.json" \
      --usage "$work/usage.json" --vms "$work/vms.json" --location "$location" \
      --dc-candidates "${DC_SIZES:-}" --ws-candidates "${WS_SIZES:-}" --replace "$vm" --tried "$tried"); then
      echo "::error::$vm did not start: Azure has no room in $location for any size the lab can use (tried ${tried//,/, }). Run the workflow again later."
      failed=1
      break
    fi
    next="${next#size=}"
    echo "Moving $vm from $size to $next and starting it"
    if az vm resize -g "$rg" -n "$vm" --size "$next" -o none >"$work/$vm.log" 2>&1 &&
      az vm start -g "$rg" -n "$vm" -o none >>"$work/$vm.log" 2>&1; then
      echo "::notice::$vm now runs as $next, because Azure had no room for ${tried//,/, } in $location."
      break
    fi
    cat "$work/$vm.log"
    if ! grep -qE "$no_room" "$work/$vm.log"; then
      echo "::error::$vm did not start as $next."
      failed=1
      break
    fi
    size="$next"
  done
done
exit "$failed"
