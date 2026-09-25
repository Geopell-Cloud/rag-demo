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

resource "azurerm_storage_account" "rag_stacc" {
  name                          = "ragstoragedev"
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

resource "azurerm_cognitive_account" "rag_cogacc_openai" {
  name                = "cog-acc-openai-rag"
  location            = "westus3"
  resource_group_name = data.azurerm_resource_group.rg.name
  kind                = "OpenAI"
  sku_name            = "S0"
}

resource "azurerm_cognitive_deployment" "rag_cogdeploy_openai" {
  name                 = "cog-deploy-openai-rag"
  cognitive_account_id = azurerm_cognitive_account.rag_cogacc_openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-5.1"
    version = "2025-11-13"
  }

  sku {
    name = "GlobalStandard"
    capacity = "5"
  }
}

# Ensures unique CAF compliant name for resources
module "naming" {
  source  = "Azure/naming/azurerm"
  version = "0.4.3"
}

resource "azurerm_cognitive_account" "rag_cogacc_docintell" {
  name                          = "cog-acc-docintell-rag-${module.naming.cognitive_account.name_unique}"
  location                      = "westus3"
  resource_group_name           = data.azurerm_resource_group.rg.name
  kind                          = "FormRecognizer"
  sku_name                      = "S0"
  public_network_access_enabled = true
}

resource "azurerm_service_plan" "rag_srvplan" {
  name                = "srvplan-rag"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = "westus2"
  os_type             = "Linux"
  sku_name            = "B1"
}

resource "azurerm_linux_function_app" "rag_funapp" {
  name                       = "funapp-rag"
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
  name                = "azure-search-rag"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            =  "westus3"
  sku                 = "free"
}

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "rag_kv" {
  name                       = "kv-rag"
  location                   = data.azurerm_resource_group.rg.location
  resource_group_name        = data.azurerm_resource_group.rg.name
  rbac_authorization_enabled = false
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
}

resource "azurerm_ai_foundry" "rag_hub" {
  name                = "foundry-hub-rag"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  storage_account_id  = azurerm_storage_account.rag_stacc.id
  key_vault_id        = azurerm_key_vault.rag_kv.id

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_ai_foundry_project" "rag_project" {
  name               = "foundry-project-rag"
  location           = data.azurerm_resource_group.rg.location
  ai_services_hub_id = azurerm_ai_foundry.rag_hub.id
}
