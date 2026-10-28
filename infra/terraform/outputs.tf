output "resource_group_name" {
  value = data.azurerm_resource_group.lab.name
}

output "location" {
  value = local.location
}

output "dc_vm_name" {
  value = azurerm_windows_virtual_machine.dc.name
}

output "ws_vm_name" {
  value = var.enable_ws01 ? azurerm_windows_virtual_machine.ws[0].name : ""
}

output "dc_private_ip" {
  value = var.dc_private_ip
}

output "key_vault_name" {
  value = azurerm_key_vault.lab.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.lab.vault_uri
}

output "bastion_name" {
  value = var.enable_bastion ? azurerm_bastion_host.lab[0].name : ""
}

output "admin_username" {
  value = var.admin_username
}

output "entra_joined" {
  description = "Whether WS01 has the Entra join (and Intune enrolment) extension."
  value       = length(azurerm_virtual_machine_extension.ws_entra_join) == 1
}
