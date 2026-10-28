# CICT-35 Deployment Checklist: Managed Identities, RBAC, Secrets, Network Access

**Status:** In Progress  
**Target Demo Date:** 2026-10-22  
**Purpose:** Configure secure, identity-based access for prep job and Function  

---

## Overview

CICT-35 sets up the security foundation for the entire pipeline:
- ✓ Managed identity for both Prep Job and Function (should already exist: `cict-ai-cus-capex-id`)
- ✓ RBAC role assignments (Service Bus, Blob Storage, SQL)
- ✓ Foundry API key storage (Key Vault)
- ✓ Network access validation (firewall rules if needed)

**No hardcoded secrets** — everything uses managed identity authentication.

---

## Prerequisites

| Resource | Location | Notes |
|----------|----------|-------|
| Managed Identity | `cict-ai-cus-capex-id` | Should exist in resource group |
| Service Bus | `cict-ai-cus-bus` | Topic: `capex-invoice-ready-v1` |
| Blob Storage | `cictaicusst` | Containers: `landing`, `test` |
| SQL Database | `cict-ai-cus-sql` / `cict-ai-cus-sqldb` | Already has `task` schema |
| Key Vault | `cict-ai-cus-kv` | Optional, for Foundry API key |

---

## Step 1: Verify Managed Identity Exists

```bash
# Check if managed identity exists
az identity show \
  --name cict-ai-cus-capex-id \
  --resource-group cict-aisandbox-cus-rg

# If not found, create it
az identity create \
  --name cict-ai-cus-capex-id \
  --resource-group cict-aisandbox-cus-rg \
  --location eastus
```

**Expected Output:**
```json
{
  "clientId": "...",
  "id": "/subscriptions/.../resourcegroups/cict-aisandbox-cus-rg/providers/microsoft.managedidentity/userassignedidentities/cict-ai-cus-capex-id",
  "principalId": "..."
}
```

Save these values for next step.

---

## Step 2: Deploy RBAC Role Assignments

Use the Bicep template to assign all required roles at once:

```bash
IDENTITY_ID="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/microsoft.managedidentity/userassignedidentities/cict-ai-cus-capex-id"

SB_NS_ID="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ServiceBus/namespaces/cict-ai-cus-bus"

BLOB_ID="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Storage/storageAccounts/cictaicusst"

SQL_ID="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Sql/servers/cict-ai-cus-sql"

# Deploy RBAC via Bicep
az deployment group create \
  --resource-group cict-aisandbox-cus-rg \
  --template-file infra/rbac/capex-managed-identity-roles.bicep \
  --parameters \
    resourceGroupName=cict-aisandbox-cus-rg \
    managedIdentityResourceId=$IDENTITY_ID \
    serviceBusNamespaceResourceId=$SB_NS_ID \
    blobStorageAccountResourceId=$BLOB_ID \
    sqlServerResourceId=$SQL_ID \
    sqlDatabaseName=cict-ai-cus-sqldb
```

**What this deploys:**
- ✓ Service Bus Data Receiver on `capex-invoice-ready-v1` topic
- ✓ Storage Blob Data Reader on `landing` and `test` containers
- ✓ SQL db_datawriter on `cict-ai-cus-sqldb`

---

## Step 3: Verify RBAC Assignments

```bash
# Check Service Bus role
az role assignment list \
  --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ServiceBus/namespaces/cict-ai-cus-bus/topics/capex-invoice-ready-v1" \
  --query "[?principalName=='cict-ai-cus-capex-id'].{role:roleDefinitionName, principal:principalName}"

# Check Blob Storage roles
az role assignment list \
  --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Storage/storageAccounts/cictaicusst" \
  --query "[?principalName=='cict-ai-cus-capex-id'].{role:roleDefinitionName, principal:principalName}"

# Check SQL role
az role assignment list \
  --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Sql/servers/cict-ai-cus-sql/databases/cict-ai-cus-sqldb" \
  --query "[?principalName=='cict-ai-cus-capex-id'].{role:roleDefinitionName, principal:principalName}"
```

**Expected:**
- Service Bus Data Receiver ✓
- Storage Blob Data Reader ✓
- SQL db_datawriter ✓

---

## Step 4: Configure Foundry API Key in Key Vault (Optional)

If using Key Vault for secrets:

```bash
# Create Key Vault if not exists
az keyvault create \
  --name cict-ai-cus-kv \
  --resource-group cict-aisandbox-cus-rg \
  --location eastus

# Store Foundry API key
az keyvault secret set \
  --vault-name cict-ai-cus-kv \
  --name foundry-api-key \
  --value "your-foundry-api-key-here"

# Grant managed identity permission to read secrets
az keyvault set-policy \
  --name cict-ai-cus-kv \
  --object-id $(az identity show -n cict-ai-cus-capex-id -g cict-aisandbox-cus-rg --query principalId -o tsv) \
  --secret-permissions get list
```

**Alternative:** Store directly in Function App settings (less secure but simpler for demo).

---

## Step 5: Validate SQL Permissions

Connect to SQL and verify the managed identity has correct permissions:

```sql
-- Connect as Entra ID admin, then:

-- Check if managed identity has database access
SELECT * FROM sys.database_principals 
WHERE name = 'cict-ai-cus-capex-id';

-- Grant INSERT on task.TaskSuggestion if needed
CREATE USER [cict-ai-cus-capex-id] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [cict-ai-cus-capex-id];
ALTER ROLE db_datawriter ADD MEMBER [cict-ai-cus-capex-id];

-- Verify
SELECT name, type, type_desc FROM sys.database_principals 
WHERE name = 'cict-ai-cus-capex-id';
```

---

## Step 6: Validate Network Access

### Service Bus
```bash
# Check if firewall allows Function App
az servicebus namespace network-rule-set show \
  --name cict-ai-cus-bus \
  --resource-group cict-aisandbox-cus-rg

# If default-action is "Deny", add Function App subnet/IP
az servicebus namespace network-rule-set update \
  --name cict-ai-cus-bus \
  --resource-group cict-aisandbox-cus-rg \
  --default-action Allow
```

### SQL Server
```bash
# Check SQL firewall rules
az sql server firewall-rule list \
  --name cict-ai-cus-sql \
  --resource-group cict-aisandbox-cus-rg

# Add Function App IP if needed (get from Azure Portal)
az sql server firewall-rule create \
  --name AllowFunctionApp \
  --server cict-ai-cus-sql \
  --resource-group cict-aisandbox-cus-rg \
  --start-ip-address <function-app-ip> \
  --end-ip-address <function-app-ip>
```

### Blob Storage
```bash
# Check storage firewall
az storage account show \
  --name cictaicusst \
  --resource-group cict-aisandbox-cus-rg \
  --query networkRuleSet

# Allow all Azure services (for demo)
az storage account update \
  --name cictaicusst \
  --resource-group cict-aisandbox-cus-rg \
  --default-action Allow
```

---

## Step 7: End-to-End Validation

### 7a. Test Managed Identity Authentication

```bash
# Get access token for managed identity
TOKEN=$(az account get-access-token \
  --resource https://servicebus.azure.net/ \
  -u /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/microsoft.managedidentity/userassignedidentities/cict-ai-cus-capex-id \
  --query accessToken -o tsv)

# Test Service Bus access
curl -X GET \
  -H "Authorization: Bearer $TOKEN" \
  "https://cict-ai-cus-bus.servicebus.windows.net/capex-invoice-ready-v1?api-version=2021-11"
```

### 7b. Test Blob Access

```python
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobClient

credential = ManagedIdentityCredential(
    client_id="<your-managed-identity-client-id>"
)

blob_client = BlobClient(
    account_url="https://cictaicusst.blob.core.windows.net",
    container_name="landing",
    blob_name="capex/input/test.zip",
    credential=credential
)

# Try to list blobs
container_client = blob_client.container_client
for blob in container_client.list_blobs():
    print(blob.name)
```

### 7c. Test SQL Access

```python
from azure.identity import ManagedIdentityCredential
import pyodbc

credential = ManagedIdentityCredential(
    client_id="<your-managed-identity-client-id>"
)

# Get access token for SQL
token = credential.get_token("https://database.windows.net/.default")

# Connect using token
connection_string = f"""
Driver={{ODBC Driver 17 for SQL Server}};
Server=cict-ai-cus-sql.database.windows.net;
Database=cict-ai-cus-sqldb;
UID=;PWD={token.token};
Authentication=ActiveDirectoryInteractive;
"""

conn = pyodbc.connect(connection_string)
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM task.TaskSuggestion")
print(cursor.fetchone())
```

---

## Step 8: Deployment Summary Checklist

- [ ] Managed identity exists (`cict-ai-cus-capex-id`)
- [ ] Service Bus Data Receiver role assigned
- [ ] Storage Blob Data Reader role assigned
- [ ] SQL db_datawriter role assigned
- [ ] Foundry API key stored (Key Vault or Function settings)
- [ ] SQL permissions verified (managed identity in database)
- [ ] Service Bus firewall allows access
- [ ] SQL firewall allows Function App IP
- [ ] Blob Storage firewall allows access
- [ ] Token-based access tested (Service Bus, Blob, SQL)

---

## Next Steps

Once CICT-35 is complete:
1. Apply SQL migration 004 (task.TaskSuggestion table)
2. Deploy Function App (CICT-33)
3. Upload test invoices to `landing/capex/input`
4. Run full pipeline end-to-end

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| "Unauthorized" on Service Bus | Missing Service Bus Data Receiver role | Re-run Bicep deployment |
| "Access Denied" on Blob | Missing Storage Blob Data Reader role | Re-run Bicep deployment |
| SQL connection fails | Firewall blocking or missing db user | Check firewall rules, run SQL user creation script |
| Token expires | Token not refreshed | ManagedIdentityCredential auto-refreshes; check credential scope |

