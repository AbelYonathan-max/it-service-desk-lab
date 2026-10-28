# A monthly budget for the lab's resource group, with alerts to the subscription owners at 50% and 90%
# of actual spend and when the forecast passes 100%. Owners are found by role, so no email address is
# written anywhere in this repository.
resource "azurerm_monitor_action_group" "owners" {
  count               = var.enable_budget ? 1 : 0
  name                = "ag-${local.base}-owners"
  resource_group_name = data.azurerm_resource_group.lab.name
  short_name          = "sdlabown"
  tags                = local.tags

  arm_role_receiver {
    name                    = "subscription-owners"
    role_id                 = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635" # Owner
    use_common_alert_schema = true
  }
}

resource "time_static" "budget_start" {
  count = var.enable_budget ? 1 : 0
}

resource "azurerm_consumption_budget_resource_group" "lab" {
  count             = var.enable_budget ? 1 : 0
  name              = "budget-${local.base}"
  resource_group_id = data.azurerm_resource_group.lab.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"

  time_period {
    start_date = "${formatdate("YYYY-MM", time_static.budget_start[0].rfc3339)}-01T00:00:00Z"
  }

  dynamic "notification" {
    for_each = { "50" = "Actual", "90" = "Actual", "100" = "Forecasted" }
    content {
      enabled        = true
      threshold      = tonumber(notification.key)
      threshold_type = notification.value
      operator       = "GreaterThan"
      contact_groups = [azurerm_monitor_action_group.owners[0].id]
      contact_roles  = ["Owner"]
    }
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
