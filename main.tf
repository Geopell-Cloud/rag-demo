terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.30"
    }
  }

  backend "azurerm" {
    resource_group_name  = "rag-demo-rg"
    storage_account_name = "ragterraformstate"
    container_name       = "ragterraformstate-container"
    key                  = "dev.terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}

data "azurerm_resource_group" "rg" {
  name = "rag-demo-rg"
}

data "azurerm_cognitive_account" "rag_shared_foundry" {
  name                = "paschalogbannu-6488-resource"
  resource_group_name = "ai-rcm-dev"
}

# Ensures unique CAF compliant name for resources
module "naming" {
  source  = "Azure/naming/azurerm"
  version = "0.4.3"
}

resource "azurerm_storage_account" "rag_stacc" {
  name                          = "ragstoragedev${module.naming.storage_account.name_unique}"
  resource_group_name           = data.azurerm_resource_group.rg.name
  location                      = data.azurerm_resource_group.rg.location
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = true
}

resource "azurerm_storage_container" "rag_stcont" {
  name                  = "rag-container"
  storage_account_id    = azurerm_storage_account.rag_stacc.id
  container_access_type = "private"
}

resource "azurerm_cognitive_deployment" "rag_cogdeploy_chat" {
  name                 = "rag-cog-deploy-chat"
  cognitive_account_id = data.azurerm_cognitive_account.rag_shared_foundry.id

  model {
    format  = "OpenAI"
    name    = "gpt-5.4-mini"
    version = "2026-03-17"
  }

  sku {
    name = "GlobalStandard"
    capacity = "5"
  }
}

resource "azurerm_cognitive_deployment" "rag_cogdeploy_embed" {
  name                 = "rag-cog-deploy-embed"
  cognitive_account_id = data.azurerm_cognitive_account.rag_shared_foundry.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 1
  }
}

resource "azurerm_service_plan" "rag_srvplan" {
  name                = "rag-srvplan"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = "westus2"
  os_type             = "Linux"
  sku_name            = "B1"
}

resource "azurerm_linux_function_app" "rag_funapp" {
  name                       = "rag-funapp"
  resource_group_name        = data.azurerm_resource_group.rg.name
  location                   = azurerm_service_plan.rag_srvplan.location
  storage_account_name       = azurerm_storage_account.rag_stacc.name
  storage_account_access_key = azurerm_storage_account.rag_stacc.primary_access_key
  service_plan_id            = azurerm_service_plan.rag_srvplan.id

  site_config {
    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"       = "python"
    "PYTHON_ENABLE_WORKER_EXTENSIONS" = "1"
  }
}

resource "azurerm_search_service" "rag_search" {
  name                = "rag-${module.naming.search_service.name_unique}"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  sku                 = "basic"

  # Configures authOptions.aadOrApiKey
  local_authentication_enabled = true
  authentication_failure_mode  = "http403"
}
