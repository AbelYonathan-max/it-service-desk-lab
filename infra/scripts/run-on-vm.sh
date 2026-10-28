#!/usr/bin/env bash
# Runs a PowerShell script on a lab VM through Azure Run Command and prints what it wrote.
#   LAB_RG=rg-servicedesk-lab run-on-vm.sh VM_NAME SCRIPT_FILE [NAME=VALUE ...]
# Retries while the VM is starting, restarting or busy with another Run Command. Exits non-zero if
# Azure could not run the script, or if the script's "STAGE RESULT:" line is anything but OK.
set -euo pipefail

VM="${1:?Usage: run-on-vm.sh VM SCRIPT [NAME=VALUE ...]}"
SCRIPT="${2:?Usage: run-on-vm.sh VM SCRIPT [NAME=VALUE ...]}"
shift 2
: "${LAB_RG:?Set LAB_RG to the lab resource group}"
ATTEMPTS="${ATTEMPTS:-20}"

PARAMS=()
if [ "$#" -gt 0 ]; then PARAMS=(--parameters "$@"); fi

OUT=$(mktemp)
ERR=$(mktemp)
trap 'rm -f "$OUT" "$ERR"' EXIT

for attempt in $(seq 1 "$ATTEMPTS"); do
  if timeout 2700 az vm run-command invoke --resource-group "$LAB_RG" --name "$VM" \
    --command-id RunPowerShellScript --scripts @"$SCRIPT" "${PARAMS[@]}" -o json >"$OUT" 2>"$ERR"; then
    break
  fi
  if [ "$attempt" -lt "$ATTEMPTS" ] && grep -qiE "conflict|not ready|VMAgentStatusCommunicationError|OperationNotAllowed|another operation|in progress|timed out|Retryable|Throttled|InternalExecutionError" "$ERR"; then
    echo "   $VM is not ready for Run Command yet (attempt $attempt): $(head -c 300 "$ERR" | tr '\n' ' ')"
    sleep 30
    continue
  fi
  cat "$ERR" >&2
  exit 1
done

STDOUT=$(jq -r '.value[]? | select(.code | test("StdOut")) | .message' "$OUT")
STDERR=$(jq -r '.value[]? | select(.code | test("StdErr")) | .message' "$OUT")
printf '%s\n' "$STDOUT"
if [ -n "$STDERR" ]; then
  printf '\n-- errors --\n%s\n' "$STDERR"
fi
RESULT=$(printf '%s\n' "$STDOUT" | grep -o 'STAGE RESULT: .*' | tail -1 || true)
if [ -n "$RESULT" ] && [ "$RESULT" != "STAGE RESULT: OK" ]; then
  exit 1
fi
