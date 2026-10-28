variable "resource_group_name" {
  description = "Resource group for the lab. The bootstrap script creates it and gives the deploy identity Contributor on it, and nothing more."
  type        = string
}

variable "location" {
  description = "Azure region. Empty means the resource group's region."
  type        = string
  default     = ""
}

variable "name_prefix" {
  description = "Prefix for resource names; a random suffix keeps globally unique names (Key Vault) unique."
  type        = string
  default     = "sdlab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.name_prefix))
    error_message = "Use 2 to 10 lower-case letters and digits, starting with a letter."
  }
}

variable "address_space" {
  description = "Address space of the lab network."
  type        = string
  default     = "10.20.0.0/16"
}

variable "servers_subnet_prefix" {
  description = "Subnet for servers (the domain controller)."
  type        = string
  default     = "10.20.1.0/24"
}

variable "clients_subnet_prefix" {
  description = "Subnet for the Windows 11 PC."
  type        = string
  default     = "10.20.2.0/24"
}

variable "dc_private_ip" {
  description = "Fixed private IP of the domain controller. The network hands it out as the DNS server, so it must not change."
  type        = string
  default     = "10.20.1.4"

  validation {
    condition     = can(cidrhost("${var.dc_private_ip}/32", 0))
    error_message = "Must be an IPv4 address."
  }
}

variable "admin_username" {
  description = "Local administrator on both VMs. On the domain controller it becomes the domain's built-in administrator. Its password is generated and kept in Key Vault."
  type        = string
  default     = "sdlab.admin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9.-]{2,18}[a-z0-9]$", var.admin_username)) && !contains(["administrator", "admin", "user", "guest", "root"], var.admin_username)
    error_message = "Use 4 to 20 lower-case letters, digits, dots or hyphens; Azure refuses names such as admin or administrator."
  }
}

variable "dc_size" {
  description = "VM size for the domain controller. The lab workflow picks one that the subscription has quota and capacity for."
  type        = string
  default     = "Standard_B2s"
}

variable "ws_size" {
  description = "VM size for the Windows 11 PC (Windows 11 needs at least 2 vCPUs and 4 GB)."
  type        = string
  default     = "Standard_B2s"
}

variable "server_image_sku" {
  description = "Windows Server image SKU (publisher MicrosoftWindowsServer, offer WindowsServer). Only used when the VM is first created."
  type        = string
  default     = "2025-datacenter-smalldisk-g2"
}

variable "client_image_sku" {
  description = "Windows 11 Enterprise image SKU (publisher MicrosoftWindowsDesktop, offer windows-11). Only used when the VM is first created."
  type        = string
  default     = "win11-25h2-ent"
}

variable "disk_type" {
  description = "Managed disk type for every disk in the lab."
  type        = string
  default     = "StandardSSD_LRS"

  validation {
    condition     = contains(["Standard_LRS", "StandardSSD_LRS", "Premium_LRS"], var.disk_type)
    error_message = "Use Standard_LRS, StandardSSD_LRS or Premium_LRS."
  }
}

variable "dc_os_disk_gb" {
  description = "Domain controller OS disk size. The small-disk image starts at 30 GB; stage 1 grows C: to fill the disk."
  type        = number
  default     = 64
}

variable "dc_data_disk_gb" {
  description = "Data disk for the AD database, SYSVOL and the file shares (F:)."
  type        = number
  default     = 8
}

variable "timezone" {
  description = "Windows time zone for both VMs and the auto-shutdown schedule. Leaver and mover timings follow it."
  type        = string
  default     = "GMT Standard Time"
}

variable "enable_auto_shutdown" {
  description = "Shut the VMs down every evening, so a forgotten lab does not run all night."
  type        = bool
  default     = true
}

variable "auto_shutdown_time" {
  description = "Daily shutdown time, HHMM in var.timezone."
  type        = string
  default     = "1900"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3])[0-5][0-9]$", var.auto_shutdown_time))
    error_message = "Use HHMM, for example 1900."
  }
}

variable "enable_bastion" {
  description = "Azure Bastion Developer: free browser sign-in to the VMs from the Azure portal, with no public IP on any VM."
  type        = bool
  default     = true
}

variable "enable_ws01" {
  description = "Create the Windows 11 PC. Off leaves a domain-controller-only lab (2 vCPUs)."
  type        = bool
  default     = true
}

variable "ws01_entra_join" {
  description = "Join the Windows 11 PC to Microsoft Entra ID. Turn on once Microsoft 365 is set up in the tenant (the lab workflow does this when ENTRA_READY is true)."
  type        = bool
  default     = false
}

variable "ws01_intune_enrollment" {
  description = "Enrol the PC in Intune as part of the Entra join."
  type        = bool
  default     = true
}

variable "entra_join_extension_version" {
  description = "type_handler_version of the AADLoginForWindows extension."
  type        = string
  default     = "1.0"
}

variable "admin_object_ids" {
  description = "Entra object IDs of the people who run the lab. They can read the lab's Key Vault secrets (the admin password and starters' temporary passwords)."
  type        = list(string)
  default     = []
}

variable "deployer_principal_type" {
  description = "Principal type of the identity running Terraform (ServicePrincipal in GitHub Actions)."
  type        = string
  default     = "ServicePrincipal"
}

variable "enable_budget" {
  description = "Monthly cost budget with alerts to the subscription owners. Not every subscription type supports budgets; the bootstrap script checks."
  type        = bool
  default     = false
}

variable "monthly_budget" {
  description = "Monthly budget amount, in the subscription's billing currency."
  type        = number
  default     = 20
}

variable "secret_version" {
  description = "Raise to generate and store a new DSRM password."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
