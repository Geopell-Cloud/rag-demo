terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.30"
    }
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}

resource "azurerm_resource_group" "rg" {
  name     = "rag-demo-rg"
  location = "westus2"
}

resource "azurerm_storage_account" "rag_stacc_state" {
  name                          = "ragterraformstate"
  resource_group_name           = azurerm_resource_group.rg.name
  location                      = azurerm_resource_group.rg.location
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = true
}

resource "azurerm_storage_container" "rag_stcont_state" {
  name                  = "ragterraformstate-container"
  storage_account_id    = azurerm_storage_account.rag_stacc_state.id
  container_access_type = "private"
}
