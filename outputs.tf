output "search_endpoint" {
  # azurerm_search_service has no endpoint attribute, so builds it from resource name
  value = "https://${azurerm_search_service.rag_search.name}.search.windows.net"
}

output "docintel_endpoint" {
  value = data.azurerm_cognitive_account.rag_shared_foundry.endpoint
}

output "openai_endpoint" {
  value = data.azurerm_cognitive_account.rag_shared_foundry.endpoint
}

output "storage_account_name" {
  value = azurerm_storage_account.rag_stacc.name
}

output "function_app_name" {
  value = azurerm_linux_function_app.rag_funapp.name
}

output "resource_group_name" {
  value = data.azurerm_resource_group.rg.name
}

output "foundry_resource_group_name" {
  value = data.azurerm_cognitive_account.rag_shared_foundry.resource_group_name
}
