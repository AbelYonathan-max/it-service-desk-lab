#!/usr/bin/env bash
# start-vms.sh RESOURCE_GROUP [VM ...]: starts the lab VMs that are not running and waits for them.
# With no VM names it starts every VM in the resource group.
#
# A deallocated VM restarts wherever Azure has room for its size, so a start can fail (for example
# for lack of capacity) after Azure has accepted it. `az vm wait` only says "The operation failed",
# so on failure this prints the VM's own status lines, which carry Azure's error code and message.
set -euo pipefail

rg="$1"
shift
vms=("$@")
if [ ${#vms[@]} -eq 0 ]; then
  mapfile -t vms < <(az vm list -g "$rg" --query "[].name" -o tsv)
fi

for vm in "${vms[@]}"; do
  state=$(az vm get-instance-view -g "$rg" -n "$vm" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" -o tsv)
  echo "$vm: $state"
  if [ "$state" != "VM running" ]; then
    az vm start -g "$rg" -n "$vm" --no-wait
  fi
done

failed=0
for vm in "${vms[@]}"; do
  if az vm wait -g "$rg" -n "$vm" --custom "instanceView.statuses[?code=='PowerState/running']" --timeout 900; then
    echo "$vm is running"
  else
    failed=1
    echo "::error::$vm did not start. Its status from Azure:"
    az vm get-instance-view -g "$rg" -n "$vm" --query "instanceView.statuses[].[code, message]" -o tsv || true
  fi
done
exit "$failed"
