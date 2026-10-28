#!/usr/bin/env bash
# One-time set-up for the IT service desk lab, run by the Azure subscription owner.
#
# In Azure Cloud Shell (Bash):
#   cd ~ && rm -rf REPO && git clone https://github.com/OWNER/REPO.git && bash REPO/infra/bootstrap/bootstrap.sh OWNER/REPO
#
# What it does, all inside your own subscription and tenant:
#   1. Signs in to GitHub with a one-time code, to read the repository's IDs and set its variables.
#   2. Picks a region (UK South unless your subscription only allows others) and checks VM quota.
#   3. Registers the Azure resource providers the lab uses.
#   4. Creates two resource groups: the lab, and "ops" for the Terraform state and deploy identity.
#   5. Creates a storage account for Terraform state (no shared keys, no public access, versioning on).
#   6. Creates the deploy identity that GitHub Actions signs in as, trusted only for this repository's
#      "lab" environment (OpenID Connect, so no password or key is stored anywhere).
#   7. Gives that identity Contributor on the lab resource group only, plus the right to grant two
#      Key Vault roles there, and access to the state container.
#   8. Microsoft Entra ID: creates a cloud admin account for the Microsoft 365 admin centre and Entra
#      Cloud Sync (a personal Microsoft account cannot use either), and lets the deploy identity
#      manage Intune policies and licences through Microsoft Graph.
#   9. Once Microsoft 365 Business Premium is active: turns on automatic Intune enrolment and Windows
#      LAPS, and marks the tenant ready so the next lab deploy joins the PC to Entra ID.
#  10. Asks for your ServiceNow developer instance (optional) and stores it as GitHub secrets.
#  11. Sets the repository variables and starts the lab deploy.
#
# Safe to run again; run it again after starting the Microsoft 365 trial. Every step checks what
# already exists. To choose the region yourself, put LOCATION=<region> in front of the command.
set -euo pipefail

REPO="${1:?Usage: bootstrap.sh OWNER/REPO}"
LOCATION="${LOCATION:-}"
LAB_RG="${LAB_RG:-rg-servicedesk-lab}"
OPS_RG="${OPS_RG:-rg-servicedesk-ops}"
IDENTITY_NAME="${IDENTITY_NAME:-id-servicedesk-github}"
ADMIN_ALIAS="${ADMIN_ALIAS:-lab.admin}"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
GRAPH="https://graph.microsoft.com"
PREFERRED_REGIONS="uksouth ukwest northeurope westeurope swedencentral francecentral germanywestcentral norwayeast switzerlandnorth polandcentral italynorth spaincentral"
# Microsoft Graph application permissions for the deploy identity (lab/intune and the Entra workflow):
#   DeviceManagementConfiguration.ReadWrite.All  compliance, configuration and endpoint security policies
#   DeviceManagementManagedDevices.Read.All      report which devices are enrolled and compliant
#   Group.Read.All                               find the groups synced from AD
#   LicenseAssignment.ReadWrite.All              group-based licensing for GG-LIC-M365-BusinessPremium
#   Organization.Read.All                        read which licences the tenant has
#   User.Read.All                                check that staff accounts arrived from AD
GRAPH_ROLES="DeviceManagementConfiguration.ReadWrite.All DeviceManagementManagedDevices.Read.All Group.Read.All LicenseAssignment.ReadWrite.All Organization.Read.All User.Read.All"

say() { printf '\n== %s\n' "$*"; }

on_exit() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo
    echo "!! The script stopped because the step above failed. Fix what its message says and run the same"
    echo "   command again: every step is safe to repeat. If the message says RequestDisallowedByAzure, your"
    echo "   subscription can only deploy to some regions: put one in front, for example LOCATION=northeurope bash ..."
  fi
}
trap on_exit EXIT

retry() {
  # Run a command until it works, for permissions and identities that take a while to propagate.
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if "$@"; then return 0; fi
    echo "   not ready yet (attempt $attempt), waiting 15 seconds"
    sleep 15
  done
  return 1
}

say "Checking your Azure account"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
SUBSCRIPTION_NAME=$(az account show --query name -o tsv)
QUOTA_ID=$(az rest --method get --url "/subscriptions/${SUBSCRIPTION_ID}?api-version=2022-12-01" \
  --query subscriptionPolicies.quotaId -o tsv 2>/dev/null || echo unknown)
ME=$(az ad signed-in-user show --query id -o tsv)
echo "   subscription: $SUBSCRIPTION_NAME ($QUOTA_ID)"

say "Signing in to GitHub"
GH=false
GH_LOGGED_IN_HERE=false
if command -v gh >/dev/null 2>&1; then
  if gh auth status --hostname github.com >/dev/null 2>&1; then
    GH=true
  else
    echo "   Needed once, to read the repository's IDs, set its variables and start the lab deploy."
    echo "   Press Enter, copy the one-time code, open https://github.com/login/device and paste the code there."
    if gh auth login --hostname github.com --git-protocol https --web; then
      GH=true
      GH_LOGGED_IN_HERE=true
    fi
  fi
fi
github_api() {
  # github_api PATH JQ_FILTER: read from the GitHub API, signed in if possible.
  if [ "$GH" = true ]; then
    gh api "$1" --jq "$2"
  else
    curl -fsS "https://api.github.com/$1" | jq -r "$2"
  fi
}
REPO_INFO=$(github_api "repos/$REPO" '[.owner.login, .owner.id, .name, .id, .created_at] | @tsv' 2>/dev/null || true)
if [ -z "$REPO_INFO" ]; then
  echo "   Could not read https://github.com/$REPO. Check the OWNER/REPO name, and sign in to GitHub if it is private."
  exit 1
fi
read -r OWNER OWNER_ID NAME REPO_ID CREATED <<<"$REPO_INFO"
REPO="$OWNER/$NAME" # GitHub's exact spelling, which the deploy token's subject uses
echo "   repository: https://github.com/$REPO"

say "Choosing the region"
if [ -z "$LOCATION" ]; then
  # Some subscriptions (Azure for Students, some trials) may only deploy to regions listed in a policy
  # assignment such as "Allowed resource deployment regions". Use one of those if UK South is not allowed.
  ALLOWED=$(az policy assignment list --disable-scope-strict-match -o json 2>/dev/null | python3 -c '
import json, sys
regions = set()
for assignment in json.load(sys.stdin):
    title = (assignment.get("displayName") or assignment.get("name") or "").lower()
    if "region" in title or "location" in title:
        for parameter in (assignment.get("parameters") or {}).values():
            value = parameter.get("value") if isinstance(parameter, dict) else None
            if isinstance(value, list):
                regions.update(v.lower() for v in value if isinstance(v, str) and v.lower() != "global")
print(" ".join(sorted(regions)))
' 2>/dev/null || true)
  LOCATION=uksouth
  if [ -n "$ALLOWED" ] && [[ " $ALLOWED " != *" uksouth "* ]]; then
    echo "   your subscription only allows: $ALLOWED"
    for region in $PREFERRED_REGIONS $ALLOWED; do
      if [[ " $ALLOWED " == *" $region "* ]]; then
        LOCATION=$region
        break
      fi
    done
  fi
fi
echo "   using $LOCATION"

say "Registering resource providers (can take a few minutes the first time)"
PROVIDERS="Microsoft.Compute Microsoft.Network Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Storage Microsoft.DevTestLab Microsoft.Insights Microsoft.Consumption Microsoft.AlertsManagement"
for ns in $PROVIDERS; do
  az provider register --namespace "$ns" --output none
done
for ns in $PROVIDERS; do
  until [ "$(az provider show --namespace "$ns" --query registrationState -o tsv)" = "Registered" ]; do
    echo "   waiting for $ns"
    sleep 10
  done
done

say "Virtual machine quota in $LOCATION"
# The lab needs 4 vCPUs: 2 for the domain controller and 2 for the Windows 11 PC. Free trials have 4.
FREE_CORES=$(az vm list-usage --location "$LOCATION" --query "[?name.value=='cores'] | [0].[limit, currentValue]" -o tsv 2>/dev/null |
  awk '{ print $1 - $2 }' || echo "?")
echo "   vCPUs free in this region: $FREE_CORES (the lab needs 4; the PC is skipped if only 2 are free)"

say "Resource groups"
for rg in "$LAB_RG" "$OPS_RG"; do
  if [ "$(az group exists --name "$rg")" != "true" ]; then
    az group create --name "$rg" --location "$LOCATION" --tags app=it-service-desk-lab --output none
  fi
done
LAB_RG_ID=$(az group show --name "$LAB_RG" --query id -o tsv)

say "Terraform state storage"
STATE_ACCOUNT=$(az storage account list --resource-group "$OPS_RG" --query "[0].name" -o tsv)
if [ -z "$STATE_ACCOUNT" ]; then
  STATE_ACCOUNT="stsdlab$(printf '%04x%04x' "$RANDOM" "$RANDOM")"
  az storage account create --name "$STATE_ACCOUNT" --resource-group "$OPS_RG" --location "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --https-only true \
    --allow-blob-public-access false --allow-shared-key-access false --output none
fi
az storage account blob-service-properties update --account-name "$STATE_ACCOUNT" --resource-group "$OPS_RG" \
  --enable-versioning true --enable-delete-retention true --delete-retention-days 14 --output none
if [ "$(az storage container-rm exists --storage-account "$STATE_ACCOUNT" --name tfstate --query exists -o tsv)" != "true" ]; then
  az storage container-rm create --storage-account "$STATE_ACCOUNT" --name tfstate --output none
fi
STATE_ACCOUNT_ID=$(az storage account show --name "$STATE_ACCOUNT" --resource-group "$OPS_RG" --query id -o tsv)

say "Deploy identity for GitHub Actions"
if ! az identity show --name "$IDENTITY_NAME" --resource-group "$OPS_RG" --output none 2>/dev/null; then
  az identity create --name "$IDENTITY_NAME" --resource-group "$OPS_RG" --location "$LOCATION" --output none
fi
CLIENT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$OPS_RG" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$OPS_RG" --query principalId -o tsv)

federate() {
  # federate NAME SUBJECT: trust GitHub Actions tokens with this subject (updated if the repository moved).
  local current
  current=$(az identity federated-credential show --name "$1" --identity-name "$IDENTITY_NAME" \
    --resource-group "$OPS_RG" --query subject -o tsv 2>/dev/null || true)
  if [ -z "$current" ]; then
    az identity federated-credential create --name "$1" --identity-name "$IDENTITY_NAME" \
      --resource-group "$OPS_RG" --issuer https://token.actions.githubusercontent.com \
      --subject "$2" --audiences api://AzureADTokenExchange --output none
  elif [ "$current" != "$2" ]; then
    az identity federated-credential update --name "$1" --identity-name "$IDENTITY_NAME" \
      --resource-group "$OPS_RG" --subject "$2" --output none
  fi
  echo "   trusts GitHub tokens for: $2"
}
# GitHub puts the repository in the token's subject. Repositories created or renamed after 15 July 2026
# use the owner and repository IDs as well as the names, so nobody can reuse the name to deploy here.
# Older repositories may still use names only, so they also get that form.
federate github-lab "repo:${OWNER}@${OWNER_ID}/${NAME}@${REPO_ID}:environment:lab"
if [[ "$CREATED" < "2026-07-15" ]]; then
  federate github-lab-names "repo:${REPO}:environment:lab"
fi

assign() {
  # assign ROLE SCOPE [extra az arguments]: idempotent role assignment for the deploy identity.
  local role="$1" scope="$2"
  shift 2
  if [ -n "$(az role assignment list --assignee "$PRINCIPAL_ID" --role "$role" --scope "$scope" --query '[0].id' -o tsv)" ]; then
    return 0
  fi
  retry az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "$role" --scope "$scope" "$@" --output none
}

say "Azure permissions for the deploy identity"
assign "Contributor" "$LAB_RG_ID"
assign "Storage Blob Data Contributor" "$STATE_ACCOUNT_ID"
# It may grant only these roles, and only inside the lab resource group.
role_id() { az role definition list --name "$1" --query "[0].name" -o tsv; }
GRANTABLE="$(role_id 'Key Vault Secrets Officer'), $(role_id 'Key Vault Secrets User')"
CONDITION="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${GRANTABLE}})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${GRANTABLE}}))"
assign "Role Based Access Control Administrator" "$LAB_RG_ID" --condition "$CONDITION" --condition-version 2.0

say "Microsoft Entra ID"
DOMAIN=$(az rest --method get --url "$GRAPH/v1.0/domains" --query "value[?isInitial].id | [0]" -o tsv)
echo "   tenant domain: $DOMAIN"

# A work account: the Microsoft 365 admin centre and Entra Cloud Sync do not accept personal Microsoft
# accounts. It gets Global Administrator; you choose its password at first sign-in.
ADMIN_UPN="${ADMIN_ALIAS}@${DOMAIN}"
ADMIN_ID=$(az ad user show --id "$ADMIN_UPN" --query id -o tsv 2>/dev/null || true)
if [ -z "$ADMIN_ID" ]; then
  TEMP_PASSWORD="Lab-$(openssl rand -hex 6)-$(openssl rand -hex 6)!"
  ADMIN_ID=$(az ad user create --display-name "Lab Administrator" --user-principal-name "$ADMIN_UPN" \
    --password "$TEMP_PASSWORD" --force-change-password-next-sign-in true --query id -o tsv)
  echo "   created $ADMIN_UPN"
  echo "   temporary password (shown once; you will choose a new one at first sign-in): $TEMP_PASSWORD"
else
  echo "   $ADMIN_UPN already exists"
fi
GLOBAL_ADMIN_ROLE="62e90394-69f5-4237-9190-012177145e10"
HAS_ROLE=$(az rest --method get \
  --url "$GRAPH/v1.0/roleManagement/directory/roleAssignments?\$filter=principalId eq '$ADMIN_ID' and roleDefinitionId eq '$GLOBAL_ADMIN_ROLE'" \
  --query "length(value)" -o tsv)
if [ "$HAS_ROLE" != "0" ]; then
  echo "   $ADMIN_UPN is a Global Administrator"
elif retry az rest --method post --url "$GRAPH/v1.0/roleManagement/directory/roleAssignments" \
  --headers "Content-Type=application/json" \
  --body "{\"principalId\":\"$ADMIN_ID\",\"roleDefinitionId\":\"$GLOBAL_ADMIN_ROLE\",\"directoryScopeId\":\"/\"}" --output none; then
  echo "   $ADMIN_UPN is now a Global Administrator"
else
  echo "   could not assign the role; in Entra admin centre > Users > $ADMIN_UPN > Assigned roles, add Global Administrator"
fi

# Microsoft Graph permissions for the deploy identity, so policy changes go through Git and CI.
GRAPH_SP=$(az ad sp show --id "$GRAPH_APP_ID" --query id -o tsv)
GRANTED=$(az rest --method get --url "$GRAPH/v1.0/servicePrincipals/$PRINCIPAL_ID/appRoleAssignments" \
  --query "value[].appRoleId" -o tsv 2>/dev/null || true)
for permission in $GRAPH_ROLES; do
  ROLE_ID=$(az ad sp show --id "$GRAPH_APP_ID" --query "appRoles[?value=='$permission'].id | [0]" -o tsv)
  if [[ "$GRANTED" != *"$ROLE_ID"* ]]; then
    az rest --method post --url "$GRAPH/v1.0/servicePrincipals/$PRINCIPAL_ID/appRoleAssignments" \
      --headers "Content-Type=application/json" \
      --body "{\"principalId\":\"$PRINCIPAL_ID\",\"resourceId\":\"$GRAPH_SP\",\"appRoleId\":\"$ROLE_ID\"}" --output none
  fi
  echo "   deploy identity has $permission"
done

say "Microsoft 365 and Intune"
# Business Premium (SPB) or another plan that includes Intune (INTUNE_A).
LICENCES=$(az rest --method get --url "$GRAPH/v1.0/subscribedSkus" \
  --query "value[?capabilityStatus=='Enabled' && servicePlans[?servicePlanName=='INTUNE_A']].skuPartNumber" -o tsv 2>/dev/null || true)
ENTRA_READY=false
if [ -n "$LICENCES" ]; then
  echo "   licences with Intune: $(echo "$LICENCES" | tr '\n' ' ')"
  # Windows devices that join Entra ID enrol in Intune automatically (MDM user scope: all).
  if az rest --method patch \
    --url "$GRAPH/beta/policies/mobileDeviceManagementPolicies/0000000a-0000-0000-c000-000000000000" \
    --headers "Content-Type=application/json" --body '{"appliesTo":"all"}' --output none 2>/dev/null; then
    echo "   automatic Intune enrolment: on for all users"
  else
    echo "   could not set automatic enrolment; set it in Entra admin centre > Mobility (MDM and WIP) > Microsoft Intune > MDM user scope: All"
  fi
  # Windows LAPS: each PC's local admin password is rotated and kept in Entra ID.
  if az rest --method get --url "$GRAPH/v1.0/policies/deviceRegistrationPolicy" -o json 2>/dev/null |
    python3 -c 'import json,sys; p=json.load(sys.stdin); p["localAdminPassword"]={"isEnabled":True}; [p.pop(k,None) for k in ("@odata.context","id","displayName","description")]; print(json.dumps(p))' >/tmp/device-reg.json &&
    az rest --method put --url "$GRAPH/v1.0/policies/deviceRegistrationPolicy" \
      --headers "Content-Type=application/json" --body @/tmp/device-reg.json --output none 2>/dev/null; then
    echo "   Windows LAPS in Entra ID: on"
  else
    echo "   could not turn on LAPS; turn it on in Entra admin centre > Devices > Device settings > Enable Microsoft Entra LAPS"
  fi
  rm -f /tmp/device-reg.json
  ENTRA_READY=true
else
  echo "   No Microsoft 365 licence with Intune yet. Next:"
  echo "   1. Open https://admin.microsoft.com in a private window and sign in as $ADMIN_UPN."
  echo "   2. Marketplace > Microsoft 365 Business Premium > Start free trial (25 licences, 1 month)."
  echo "      Then Billing > Your products > the trial > turn recurring billing off."
  echo "   3. Run this command again. The lab deploy then joins the Windows 11 PC to Entra ID and Intune."
fi

say "ServiceNow developer instance (optional)"
SN_SET=false
if [ "$GH" = true ] && gh secret list --repo "$REPO" 2>/dev/null | grep -q '^SERVICENOW_ADMIN_PASSWORD'; then
  SN_SET=true
  echo "   already stored in GitHub"
elif [ "$GH" = true ] && [ -t 0 ]; then
  echo "   From https://developer.servicenow.com > your instance: the instance name (dev123456) and the admin password."
  read -r -p "   Instance name, or Enter to skip for now: " SN_INSTANCE
  if [ -n "$SN_INSTANCE" ]; then
    SN_INSTANCE="${SN_INSTANCE#https://}"
    SN_INSTANCE="${SN_INSTANCE%%.*}"
    read -r -s -p "   admin password (hidden): " SN_PASSWORD
    echo
    printf '%s' "$SN_INSTANCE" | gh secret set SERVICENOW_INSTANCE --repo "$REPO"
    printf '%s' "$SN_PASSWORD" | gh secret set SERVICENOW_ADMIN_PASSWORD --repo "$REPO"
    unset SN_PASSWORD
    SN_SET=true
    echo "   stored as GitHub secrets SERVICENOW_INSTANCE and SERVICENOW_ADMIN_PASSWORD"
  fi
fi

# Budgets are not available on every subscription type.
case "$QUOTA_ID" in
  AzureForStudents* | Sponsored* | AzurePass* | FreeTrial* | unknown) BUDGET=false ;;
  *) BUDGET=true ;;
esac

say "GitHub repository variables and secrets"
VARS=(
  "AZURE_CLIENT_ID=$CLIENT_ID"
  "AZURE_TENANT_ID=$TENANT_ID"
  "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
  "AZURE_LOCATION=$LOCATION"
  "LAB_RESOURCE_GROUP=$LAB_RG"
  "TFSTATE_RESOURCE_GROUP=$OPS_RG"
  "TFSTATE_STORAGE_ACCOUNT=$STATE_ACCOUNT"
  "ENABLE_BUDGET=$BUDGET"
  "LAB_ADMIN_OBJECT_IDS=[\"$ME\"]"
  "ENTRA_READY=$ENTRA_READY"
)
SET_BY_SCRIPT=false
DEPLOY_STARTED=false
if [ "$GH" = true ]; then
  SET_BY_SCRIPT=true
  for pair in "${VARS[@]}"; do
    gh variable set "${pair%%=*}" --repo "$REPO" --body "${pair#*=}" || SET_BY_SCRIPT=false
  done
  # The tenant's domain becomes the staff accounts' sign-in suffix. It is stored as a secret so it is
  # masked in the public workflow logs.
  printf '%s' "$DOMAIN" | gh secret set ENTRA_DOMAIN --repo "$REPO" || SET_BY_SCRIPT=false
fi
if [ "$SET_BY_SCRIPT" = true ]; then
  echo "   set on https://github.com/$REPO/settings/variables/actions"
  echo "   giving Azure a minute to apply the new permissions, then starting the lab deploy"
  sleep 60
  if gh workflow run lab.yml --repo "$REPO" -f action=deploy; then
    DEPLOY_STARTED=true
  fi
else
  echo "   Could not set them from here. Add these under Settings > Secrets and variables > Actions > Variables,"
  echo "   add a secret ENTRA_DOMAIN = $DOMAIN, then run the Lab workflow from the Actions tab:"
  for pair in "${VARS[@]}"; do echo "     ${pair%%=*} = ${pair#*=}"; done
fi
if [ "$GH_LOGGED_IN_HERE" = true ]; then
  gh auth logout --hostname github.com </dev/null >/dev/null 2>&1 || true
fi

say "Done"
echo "   region: $LOCATION, subscription type: $QUOTA_ID, cost budget: $BUDGET"
echo "   Microsoft 365 with Intune: $ENTRA_READY, ServiceNow stored: $SN_SET"
if [ "$DEPLOY_STARTED" = true ]; then
  echo "   lab deploy started, about 40 minutes: https://github.com/$REPO/actions/workflows/lab.yml"
fi
echo "   None of the variables is a secret: they are names and IDs. Azure accepts sign-ins for the deploy"
echo "   identity only from GitHub Actions runs in ${REPO}, environment 'lab'."
