terraform {
  required_version = ">= 1.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription ID. Set via TF_VAR_subscription_id or -var."
}

variable "location" {
  type    = string
  default = "eastus"
}

variable "prefix" {
  type    = string
  default = "cpl-idx"
}

# ── Resource Group ──────────────────────────────────────────────
resource "azurerm_resource_group" "this" {
  name     = "rg-${var.prefix}"
  location = var.location

  tags = {
    project = "codeplane"
    purpose = "batch-indexing"
  }
}

# ── Storage Account + Container ─────────────────────────────────
resource "azurerm_storage_account" "this" {
  name                          = "st${replace(var.prefix, "-", "")}${substr(sha256(azurerm_resource_group.this.id), 0, 6)}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  min_tls_version               = "TLS1_2"
  shared_access_key_enabled     = false
  default_to_oauth_authentication = true

  tags = azurerm_resource_group.this.tags
}

resource "azurerm_storage_container" "indexes" {
  name                  = "indexes"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# ── Container Registry ──────────────────────────────────────────
resource "azurerm_container_registry" "this" {
  name                = "acr${replace(var.prefix, "-", "")}${substr(sha256(azurerm_resource_group.this.id), 0, 6)}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = true

  tags = azurerm_resource_group.this.tags
}

# ── Managed Identity (for ACI → Blob + ACR) ─────────────────────
resource "azurerm_user_assigned_identity" "indexer" {
  name                = "id-${var.prefix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

# ACR pull
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.indexer.principal_id
}

# Blob write
resource "azurerm_role_assignment" "blob_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.indexer.principal_id
}

# ── Outputs ──────────────────────────────────────────────────────
output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "acr_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "acr_name" {
  value = azurerm_container_registry.this.name
}

output "storage_account" {
  value = azurerm_storage_account.this.name
}

output "storage_container" {
  value = azurerm_storage_container.indexes.name
}

output "identity_id" {
  value = azurerm_user_assigned_identity.indexer.id
}

output "identity_client_id" {
  value = azurerm_user_assigned_identity.indexer.client_id
}
