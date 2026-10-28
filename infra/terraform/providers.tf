provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
    virtual_machine {
      # A destroyed lab leaves nothing behind that still costs money.
      delete_os_disk_on_deletion = true
    }
  }

  # The bootstrap script registers the resource providers once, so the deploy identity does not need
  # rights over the whole subscription.
  resource_provider_registrations = "none"
}
