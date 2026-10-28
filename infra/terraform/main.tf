data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "lab" {
  name = var.resource_group_name
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  location = coalesce(var.location, data.azurerm_resource_group.lab.location)
  base     = "${var.name_prefix}-${random_string.suffix.result}"
  tags     = merge({ app = "it-service-desk-lab", managed_by = "terraform" }, var.tags)
  ws_count = var.enable_ws01 ? 1 : 0
}

# Role assignments take a minute or two to reach Key Vault. Secrets wait for this.
resource "time_sleep" "rbac" {
  create_duration = "90s"

  depends_on = [
    azurerm_role_assignment.deployer_secrets,
    azurerm_role_assignment.dc_secrets,
  ]
}
