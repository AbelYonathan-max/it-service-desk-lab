terraform {
  # 1.11 or later: the DSRM password is a write-only Key Vault secret, so it never reaches the state.
  required_version = ">= 1.11.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # State lives in the storage account made by infra/bootstrap/bootstrap.sh. The lab workflow
  # passes the account details with -backend-config and signs in with OIDC: no storage keys.
  backend "azurerm" {}
}
