# Tests with mocked providers: they need no Azure account and run in CI on every push.
# Run with: terraform init -backend=false && terraform test

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "11111111-1111-1111-1111-111111111111"
      object_id       = "22222222-2222-2222-2222-222222222222"
      subscription_id = "33333333-3333-3333-3333-333333333333"
    }
  }

  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab"
      location = "uksouth"
    }
  }
}

mock_provider "time" {
  mock_resource "time_static" {
    defaults = {
      rfc3339 = "2026-11-14T10:00:00Z"
    }
  }
}

# The azurerm provider still checks the format of IDs in mocked runs, so the resources that others
# point at get realistic values.
override_resource {
  target = azurerm_virtual_network.lab
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/virtualNetworks/vnet-test"
  }
}

override_resource {
  target = azurerm_subnet.servers
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-servers"
  }
}

override_resource {
  target = azurerm_subnet.clients
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-clients"
  }
}

override_resource {
  target = azurerm_network_security_group.servers
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/networkSecurityGroups/nsg-servers"
  }
}

override_resource {
  target = azurerm_network_security_group.clients
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/networkSecurityGroups/nsg-clients"
  }
}

override_resource {
  target = azurerm_network_interface.dc
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/networkInterfaces/nic-dc01"
  }
}

override_resource {
  target = azurerm_network_interface.ws
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Network/networkInterfaces/nic-ws01"
  }
}

override_resource {
  target = azurerm_windows_virtual_machine.dc
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Compute/virtualMachines/vm-dc01"
    identity = {
      principal_id = "44444444-4444-4444-4444-444444444444"
      tenant_id    = "11111111-1111-1111-1111-111111111111"
    }
  }
}

override_resource {
  target = azurerm_windows_virtual_machine.ws
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Compute/virtualMachines/vm-ws01"
  }
}

override_resource {
  target = azurerm_managed_disk.dc_data
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Compute/disks/datadisk-dc01"
  }
}

override_resource {
  target = azurerm_key_vault.lab
  values = {
    id        = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.KeyVault/vaults/kv-sdlab-test"
    vault_uri = "https://kv-sdlab-test.vault.azure.net/"
  }
}

override_resource {
  target = azurerm_monitor_action_group.owners
  values = {
    id = "/subscriptions/33333333-3333-3333-3333-333333333333/resourceGroups/rg-servicedesk-lab/providers/Microsoft.Insights/actionGroups/ag-test"
  }
}

variables {
  resource_group_name = "rg-servicedesk-lab"
}

run "defaults_are_private_cheap_and_hardened" {
  command = apply

  assert {
    condition     = azurerm_network_interface.dc.ip_configuration[0].private_ip_address == "10.20.1.4" && azurerm_network_interface.dc.ip_configuration[0].private_ip_address_allocation == "Static"
    error_message = "The domain controller needs a fixed address, because the network hands it out as the DNS server."
  }

  assert {
    condition     = azurerm_virtual_network.lab.dns_servers == tolist(["10.20.1.4", "168.63.129.16"])
    error_message = "VMs must ask the domain controller first and Azure DNS second."
  }

  assert {
    condition = alltrue([
      for ip in concat(azurerm_network_interface.dc.ip_configuration, azurerm_network_interface.ws[0].ip_configuration) : ip.public_ip_address_id == null
    ])
    error_message = "No VM may have a public IP address."
  }

  assert {
    condition = alltrue(flatten([
      for nsg in [azurerm_network_security_group.servers, azurerm_network_security_group.clients] : [
        for rule in nsg.security_rule : !(rule.direction == "Inbound" && rule.access == "Allow" && contains(["*", "Internet", "0.0.0.0/0"], rule.source_address_prefix))
      ]
    ]))
    error_message = "No inbound rule may allow traffic from the internet."
  }

  assert {
    condition     = azurerm_bastion_host.lab[0].sku == "Developer" && azurerm_bastion_host.lab[0].virtual_network_id == azurerm_virtual_network.lab.id
    error_message = "Sign-in to the VMs is through the free Bastion Developer SKU."
  }

  assert {
    condition = alltrue([
      for vm in [azurerm_windows_virtual_machine.dc, azurerm_windows_virtual_machine.ws[0]] : vm.secure_boot_enabled && vm.vtpm_enabled
    ])
    error_message = "Both VMs use Trusted Launch (Secure Boot and vTPM); Windows 11 and BitLocker need the TPM."
  }

  assert {
    condition     = azurerm_virtual_machine_data_disk_attachment.dc_data.caching == "None"
    error_message = "The AD database disk must have host caching off."
  }

  assert {
    condition     = azurerm_windows_virtual_machine.dc.identity[0].type == "SystemAssigned" && azurerm_role_assignment.dc_secrets.principal_id == "44444444-4444-4444-4444-444444444444"
    error_message = "The DC reads its secrets from Key Vault with its own managed identity."
  }

  assert {
    condition     = azurerm_key_vault.lab.rbac_authorization_enabled
    error_message = "Key Vault access must be by Azure role, not access policy."
  }

  assert {
    condition     = azurerm_key_vault_secret.dsrm.value == null
    error_message = "The DSRM password must be write-only, so it never reaches the state."
  }

  assert {
    condition     = azurerm_windows_virtual_machine.ws[0].license_type == "Windows_Client" && azurerm_windows_virtual_machine.ws[0].source_image_reference[0].offer == "windows-11"
    error_message = "WS01 is Windows 11 Enterprise, licensed per user."
  }

  assert {
    condition     = azurerm_dev_test_global_vm_shutdown_schedule.dc[0].daily_recurrence_time == "1900" && azurerm_dev_test_global_vm_shutdown_schedule.ws[0].timezone == "GMT Standard Time"
    error_message = "Both VMs shut down at 19:00 UK time."
  }

  assert {
    condition     = length(azurerm_virtual_machine_extension.ws_entra_join) == 0
    error_message = "WS01 joins Entra ID only once Microsoft 365 is set up in the tenant."
  }

  assert {
    condition     = length(azurerm_consumption_budget_resource_group.lab) == 0
    error_message = "The budget is off unless the bootstrap script turns it on."
  }
}

run "entra_join_enrols_in_intune" {
  command = apply

  variables {
    ws01_entra_join = true
    enable_budget   = true
  }

  assert {
    condition     = jsondecode(azurerm_virtual_machine_extension.ws_entra_join[0].settings).mdmId == "0000000a-0000-0000-c000-000000000000"
    error_message = "The Entra join must enrol the PC in Intune (Intune's MDM application ID)."
  }

  assert {
    condition     = azurerm_virtual_machine_extension.ws_entra_join[0].type == "AADLoginForWindows"
    error_message = "WS01 joins with the AADLoginForWindows extension."
  }

  assert {
    condition     = azurerm_consumption_budget_resource_group.lab[0].amount == 20 && length(azurerm_consumption_budget_resource_group.lab[0].notification) == 3
    error_message = "The budget alerts at 50% and 90% of spend and at a forecast of 100%."
  }
}

run "domain_controller_only" {
  command = apply

  variables {
    enable_ws01          = false
    ws01_entra_join      = true
    enable_bastion       = false
    enable_auto_shutdown = false
  }

  assert {
    condition     = length(azurerm_windows_virtual_machine.ws) == 0 && length(azurerm_virtual_machine_extension.ws_entra_join) == 0 && length(azurerm_key_vault_secret.ws_admin) == 0
    error_message = "Without WS01 there is nothing to join or enrol."
  }

  assert {
    condition     = length(azurerm_bastion_host.lab) == 0 && length(azurerm_dev_test_global_vm_shutdown_schedule.dc) == 0
    error_message = "Bastion and auto-shutdown follow their switches."
  }

  assert {
    condition     = output.ws_vm_name == "" && output.bastion_name == ""
    error_message = "Outputs for missing parts are empty."
  }
}

run "rejects_a_bad_shutdown_time" {
  command = plan

  variables {
    auto_shutdown_time = "7pm"
  }

  expect_failures = [var.auto_shutdown_time]
}

run "rejects_a_reserved_admin_name" {
  command = plan

  variables {
    admin_username = "administrator"
  }

  expect_failures = [var.admin_username]
}
