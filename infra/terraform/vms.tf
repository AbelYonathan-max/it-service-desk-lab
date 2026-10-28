# DC01: Windows Server, the domain controller, DNS server and file server for corp.kestrel.internal.
# The lab workflow builds it after Terraform, in stages, through Azure Run Command (lab/dc/*.ps1).
resource "azurerm_network_interface" "dc" {
  name                = "nic-${local.base}-dc01"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.servers.id
    private_ip_address_allocation = "Static"
    private_ip_address            = var.dc_private_ip
  }
}

resource "azurerm_windows_virtual_machine" "dc" {
  name                  = "vm-dc01"
  computer_name         = "DC01"
  resource_group_name   = data.azurerm_resource_group.lab.name
  location              = local.location
  size                  = var.dc_size
  admin_username        = var.admin_username
  admin_password        = random_password.dc_admin.result
  network_interface_ids = [azurerm_network_interface.dc.id]
  timezone              = var.timezone
  patch_mode            = "AutomaticByOS"
  # Trusted Launch: Secure Boot and a virtual TPM.
  secure_boot_enabled = true
  vtpm_enabled        = true
  tags                = merge(local.tags, { role = "domain-controller" })

  # The DC reads its secrets from Key Vault with this identity; there is no password in any script.
  identity {
    type = "SystemAssigned"
  }

  os_disk {
    name                 = "osdisk-${local.base}-dc01"
    caching              = "ReadWrite"
    storage_account_type = var.disk_type
    disk_size_gb         = var.dc_os_disk_gb
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = var.server_image_sku
    version   = "latest"
  }

  # Boot diagnostics in Azure-managed storage: the portal can show the console screenshot.
  boot_diagnostics {}

  lifecycle {
    # A newer image or a different default SKU must never rebuild a running domain controller.
    ignore_changes = [source_image_reference]
  }
}

# Microsoft's guidance for a DC in Azure: keep the AD database and SYSVOL on a data disk with host
# caching off, so a write the DC thinks is safe really is on disk.
resource "azurerm_managed_disk" "dc_data" {
  name                 = "datadisk-${local.base}-dc01"
  resource_group_name  = data.azurerm_resource_group.lab.name
  location             = local.location
  storage_account_type = var.disk_type
  create_option        = "Empty"
  disk_size_gb         = var.dc_data_disk_gb
  tags                 = local.tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "dc_data" {
  managed_disk_id    = azurerm_managed_disk.dc_data.id
  virtual_machine_id = azurerm_windows_virtual_machine.dc.id
  lun                = 0
  caching            = "None"
}

# WS01: a Windows 11 Enterprise PC for a member of staff. Cloud-native: joined to Microsoft Entra ID
# and managed by Intune (lab/intune), the way councils are moving their estates.
resource "azurerm_network_interface" "ws" {
  count               = local.ws_count
  name                = "nic-${local.base}-ws01"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.clients.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_windows_virtual_machine" "ws" {
  count                 = local.ws_count
  name                  = "vm-ws01"
  computer_name         = "WS01"
  resource_group_name   = data.azurerm_resource_group.lab.name
  location              = local.location
  size                  = var.ws_size
  admin_username        = var.admin_username
  admin_password        = random_password.ws_admin.result
  network_interface_ids = [azurerm_network_interface.ws[0].id]
  timezone              = var.timezone
  # Windows 11 in Azure is licensed per user (Microsoft 365 / Windows Enterprise), not per VM.
  license_type        = "Windows_Client"
  secure_boot_enabled = true
  vtpm_enabled        = true
  tags                = merge(local.tags, { role = "workstation" })

  # The Entra join extension signs in as this identity to register the device.
  identity {
    type = "SystemAssigned"
  }

  os_disk {
    name                 = "osdisk-${local.base}-ws01"
    caching              = "ReadWrite"
    storage_account_type = var.disk_type
  }

  source_image_reference {
    publisher = "MicrosoftWindowsDesktop"
    offer     = "windows-11"
    sku       = var.client_image_sku
    version   = "latest"
  }

  boot_diagnostics {}

  lifecycle {
    ignore_changes = [source_image_reference]
  }
}

# Joins WS01 to Microsoft Entra ID and, with mdmId, enrols it in Intune in the same step.
resource "azurerm_virtual_machine_extension" "ws_entra_join" {
  count                      = var.enable_ws01 && var.ws01_entra_join ? 1 : 0
  name                       = "AADLoginForWindows"
  virtual_machine_id         = azurerm_windows_virtual_machine.ws[0].id
  publisher                  = "Microsoft.Azure.ActiveDirectory"
  type                       = "AADLoginForWindows"
  type_handler_version       = var.entra_join_extension_version
  auto_upgrade_minor_version = true
  settings                   = jsonencode(var.ws01_intune_enrollment ? { mdmId = "0000000a-0000-0000-c000-000000000000" } : {})
  tags                       = local.tags
}

# Every evening the VMs shut down (and stop costing compute), in case someone forgets.
resource "azurerm_dev_test_global_vm_shutdown_schedule" "dc" {
  count                 = var.enable_auto_shutdown ? 1 : 0
  virtual_machine_id    = azurerm_windows_virtual_machine.dc.id
  location              = local.location
  enabled               = true
  daily_recurrence_time = var.auto_shutdown_time
  timezone              = var.timezone
  tags                  = local.tags

  notification_settings {
    enabled = false
  }
}

resource "azurerm_dev_test_global_vm_shutdown_schedule" "ws" {
  count                 = var.enable_auto_shutdown ? local.ws_count : 0
  virtual_machine_id    = azurerm_windows_virtual_machine.ws[0].id
  location              = local.location
  enabled               = true
  daily_recurrence_time = var.auto_shutdown_time
  timezone              = var.timezone
  tags                  = local.tags

  notification_settings {
    enabled = false
  }
}
