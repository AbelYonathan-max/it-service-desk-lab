# The lab's secrets: the VMs' administrator passwords, the Directory Services Restore Mode password,
# the ServiceNow integration account (written by the ServiceNow workflow) and new starters' temporary
# passwords (written by the fulfilment agent, each with a 14-day expiry). Access is by Azure role,
# never by key: the deploy identity and the domain controller manage secrets; the lab's admins read.
resource "azurerm_key_vault" "lab" {
  name                          = "kv-${local.base}"
  resource_group_name           = data.azurerm_resource_group.lab.name
  location                      = local.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = false # a lab that is destroyed and rebuilt; see docs/security.md
  public_network_access_enabled = true  # GitHub Actions and the DC sign in with Entra ID; no keys exist
  tags                          = local.tags
}

resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.lab.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
  principal_type       = var.deployer_principal_type
}

resource "azurerm_role_assignment" "dc_secrets" {
  scope                = azurerm_key_vault.lab.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = azurerm_windows_virtual_machine.dc.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "admin_secrets" {
  for_each             = toset(var.admin_object_ids)
  scope                = azurerm_key_vault.lab.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = each.value
  principal_type       = "User"
}

# The VMs' administrator passwords. Azure needs them when it creates a VM, so they are in the
# Terraform state too, which lives in a private storage account that only the deploy identity can read.
resource "random_password" "dc_admin" {
  length           = 28
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
  override_special = "!#%+=?"
}

resource "random_password" "ws_admin" {
  length           = 28
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
  override_special = "!#%+=?"
}

resource "azurerm_key_vault_secret" "dc_admin" {
  name         = "dc01-admin-password"
  key_vault_id = azurerm_key_vault.lab.id
  value        = random_password.dc_admin.result
  content_type = "password for ${var.admin_username} (the domain's built-in administrator)"
  depends_on   = [time_sleep.rbac]
}

resource "azurerm_key_vault_secret" "ws_admin" {
  count        = local.ws_count
  name         = "ws01-admin-password"
  key_vault_id = azurerm_key_vault.lab.id
  value        = random_password.ws_admin.result
  content_type = "password for WS01\\${var.admin_username} (local administrator)"
  depends_on   = [time_sleep.rbac]
}

# Only needed to repair Active Directory offline. Generated and stored without ever reaching the
# state file (write-only argument). Raise var.secret_version to rotate it.
ephemeral "random_password" "dsrm" {
  length           = 28
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
  override_special = "!#%+=?"
}

resource "azurerm_key_vault_secret" "dsrm" {
  name             = "dsrm-password"
  key_vault_id     = azurerm_key_vault.lab.id
  value_wo         = ephemeral.random_password.dsrm.result
  value_wo_version = var.secret_version
  content_type     = "Directory Services Restore Mode password for DC01"
  depends_on       = [time_sleep.rbac]
}
