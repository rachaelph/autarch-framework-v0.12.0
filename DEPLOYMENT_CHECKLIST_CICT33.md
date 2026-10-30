# CICT-33 Deployment Checklist: Invoice Agent Invocation Azure Function

**Status:** Code complete ✓ Tests passing ✓ Bicep compiled ✓  
**Target Demo Date:** 2026-10-22  
**Last Updated:** 2026-10-21  

## Changes Completed

- ✓ Created `sql/migrations/004_create_task_suggestion_table.sql` - Task schema with TaskSuggestion table
- ✓ Created `autarch/capex/agent_invocation_models.py` - Pydantic models for messages and suggestions
- ✓ Created `autarch/capex/agent_invocation_blob.py` - Blob Storage package fetcher
- ✓ Created `autarch/capex/agent_invocation_handler.py` - Foundry agent invocation (gpt-5.1)
- ✓ Created `autarch/capex/agent_invocation_persistence.py` - SQL persistence for task suggestions
- ✓ Created `azure_function/function_app.py` - Service Bus trigger function
- ✓ Created Azure Function configuration (host.json, local.settings.json, requirements.txt)
- ✓ Created `infra/function-apps/capex-invoice-agent-invocation.bicep` - IaC template
- ✓ Created comprehensive unit tests (7 test cases, all passing)
- ✓ All 106 capex tests passing
- ✓ Bicep template compiles successfully

## Architecture

```
Service Bus Topic (capex-invoice-ready-v1)
    ↓ [InvoiceReady v1 message with correlation ID]
    ↓
Azure Function (Service Bus Trigger)
    ├─ Parse InvoiceReady message
    ├─ Fetch invoice package from Blob Storage (capex/output/<run-id>/<package-id>.json)
    ├─ Invoke Foundry Agent (gpt-5.1 model)
    └─ Persist task suggestions to SQL (task.TaskSuggestion table)
        └─ One row per invoice with per-line recommendations (CAPEX/OPEX/REVIEW)

Auth: ManagedIdentityCredential (no keys, no SAS tokens, no connection strings)
```

## Prerequisites

### Azure Resources (Existing)

```
Service Bus:       cict-ai-cus-bus (namespace: cict-ai-cus-bus.servicebus.windows.net)
Topic:             capex-invoice-ready-v1
Blob Storage:      cictaicusst (container: test)
SQL Database:      cict-ai-cus-sql.database.windows.net / cict-ai-cus-sqldb
App Service Plan:  Must exist (create if needed: Standard tier recommended)
Managed Identity:  cict-ai-cus-capex-id (562512ef-d515-428a-8df5-6c4c589812ef)
Application Insights: Must exist (create if needed)
Foundry Project:   Endpoint: https://cict-ai-cus-foundry.openai.azure.com
```

### Required RBAC Roles

Grant these roles to the managed identity:

1. **Service Bus Data Receiver** on topic `capex-invoice-ready-v1`
   ```
   Scope: /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ServiceBus/namespaces/cict-ai-cus-bus/topics/capex-invoice-ready-v1
   Role: Azure Service Bus Data Receiver
   ```

2. **Storage Blob Data Reader** on Blob container `test`
   ```
   Scope: /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Storage/storageAccounts/cictaicusst/blobServices/default/containers/test
   Role: Storage Blob Data Reader
   ```

3. **Existing SQL permissions** (already granted)
   - CONNECT
   - SELECT on prep and pub schemas
   - INSERT on task.TaskSuggestion

## Pre-Deployment: SQL Migration

Before deploying the function, apply the task suggestion table migration:

```bash
# Connect to Azure SQL using your Entra ID account
sqlcmd -S cict-ai-cus-sql.database.windows.net -d cict-ai-cus-sqldb -U your-username -P your-password

-- Then run the migration:
:r sql/migrations/004_create_task_suggestion_table.sql
GO

-- Verify schema and table created:
SELECT name FROM sys.schemas WHERE name = 'task';
SELECT name FROM sys.tables WHERE name = 'TaskSuggestion';
```

## Deployment Steps

### 1. Prepare Environment Variables

Create or update `local.settings.json` with your Foundry API key:

```json
{
  "FOUNDRY_API_KEY": "your-actual-api-key-here",
  "FOUNDRY_ENDPOINT": "https://cict-ai-cus-foundry.openai.azure.com/openai",
  "FOUNDRY_DEPLOYMENT": "gpt-5.1"
}
```

**Important:** Never commit local.settings.json with actual API keys to Git.

### 2. Build and Test Locally (Optional)

```bash
# Create Python virtual environment
python -m venv venv
source venv/Scripts/activate  # On Windows

# Install dependencies
pip install -r azure_function/requirements.txt

# Run tests
python -m pytest tests/test_capex_agent_invocation.py -v

# Run function locally (requires Azure Functions Core Tools)
func start --python
```

### 3. Get Prerequisites Information

Gather IDs for Bicep deployment:

```bash
# Authenticate to Azure
az login
az account set --subscription "0451df15-250e-42f5-b15c-f19a9bb9c21a"

# Get App Service Plan ID (if exists)
az appservice plan list \
  --resource-group "cict-aisandbox-cus-rg" \
  --query "[?contains(name, 'plan')].[id,name]" -o table

# Get Application Insights instrumentation key
az resource show \
  --resource-group "cict-aisandbox-cus-rg" \
  --name "your-appinsights-name" \
  --resource-type "Microsoft.Insights/components" \
  --query "properties.InstrumentationKey" -o tsv

# Get Storage account key for Functions runtime
az storage account keys list \
  --account-name "cictaicusst" \
  --resource-group "cict-aisandbox-cus-rg" \
  --query "[0].value" -o tsv
```

If App Service Plan doesn't exist, create one:

```bash
az appservice plan create \
  --name "capex-service-plan" \
  --resource-group "cict-aisandbox-cus-rg" \
  --sku "S1" \
  --is-linux
```

### 4. Validate Bicep Template

```bash
# Compile Bicep
az bicep build --file infra/function-apps/capex-invoice-agent-invocation.bicep

# Preview what-if deployment
az deployment group what-if \
  --resource-group "cict-aisandbox-cus-rg" \
  --template-file "infra/function-apps/capex-invoice-agent-invocation.bicep" \
  --parameters \
    functionAppName="capex-invoice-agent-invocation" \
    location="Central US" \
    appServicePlanId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Web/serverfarms/capex-service-plan" \
    userAssignedIdentityResourceId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id" \
    userAssignedIdentityClientId="562512ef-d515-428a-8df5-6c4c589812ef" \
    functionsStorageAccountName="cictaicusst" \
    functionsStorageAccountKey="<key-from-step-3>" \
    serviceBusNamespace="cict-ai-cus-bus.servicebus.windows.net" \
    foundryEndpoint="https://cict-ai-cus-foundry.openai.azure.com/openai" \
    foundryApiKey="<your-foundry-api-key>" \
    applicationInsightsInstrumentationKey="<key-from-step-3>"
```

Expected: Only Function App resource created, no changes to existing resources.

### 5. Deploy Function App

```bash
az deployment group create \
  --resource-group "cict-aisandbox-cus-rg" \
  --template-file "infra/function-apps/capex-invoice-agent-invocation.bicep" \
  --parameters \
    functionAppName="capex-invoice-agent-invocation" \
    location="Central US" \
    appServicePlanId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Web/serverfarms/capex-service-plan" \
    userAssignedIdentityResourceId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id" \
    userAssignedIdentityClientId="562512ef-d515-428a-8df5-6c4c589812ef" \
    functionsStorageAccountName="cictaicusst" \
    functionsStorageAccountKey="<key>" \
    serviceBusNamespace="cict-ai-cus-bus.servicebus.windows.net" \
    foundryEndpoint="https://cict-ai-cus-foundry.openai.azure.com/openai" \
    foundryApiKey="<your-key>" \
    applicationInsightsInstrumentationKey="<key>"
```

### 6. Publish Function Code

```bash
# Install Azure Functions Core Tools if needed
# See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local

# Build deployment package
cd azure_function
func azure functionapp publish "capex-invoice-agent-invocation"
```

### 7. Create Service Bus Subscription

Create the subscription that the function will consume from:

```bash
az servicebus topic subscription create \
  --namespace-name "cict-ai-cus-bus" \
  --topic-name "capex-invoice-ready-v1" \
  --name "invoice-agent-invocation" \
  --resource-group "cict-aisandbox-cus-rg"
```

### 8. Grant RBAC Roles

Grant necessary permissions to the managed identity:

```bash
# Service Bus Data Receiver role
az role assignment create \
  --assignee "562512ef-d515-428a-8df5-6c4c589812ef" \
  --role "Azure Service Bus Data Receiver" \
  --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ServiceBus/namespaces/cict-ai-cus-bus/topics/capex-invoice-ready-v1"

# Storage Blob Data Reader role
az role assignment create \
  --assignee "562512ef-d515-428a-8df5-6c4c589812ef" \
  --role "Storage Blob Data Reader" \
  --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.Storage/storageAccounts/cictaicusst/blobServices/default/containers/test"
```

## End-to-End Validation

### Trigger the Pipeline

1. **Run CICT-32 Preparation Job** (if not already done)
   - Manually trigger `capex-invoice-preparation` in Container Apps
   - Verify outputs in `capex/output/<run-id>/`
   - Verify outbox events created in SQL with status PENDING

2. **Run CICT-32 Dispatcher Job**
   - Manually trigger `capex-invoice-ready-dispatch`
   - Publishes InvoiceReady v1 messages to Service Bus
   - Updates outbox status to PUBLISHED

3. **Observe Function Execution**
   ```bash
   # Stream function logs
   az functionapp log tail \
     --name "capex-invoice-agent-invocation" \
     --resource-group "cict-aisandbox-cus-rg"
   
   # Expected logs:
   # - invoice_agent_invocation_started
   # - invoice_ready_message_parsed
   # - fetching_invoice_package
   # - invoice_package_fetched
   # - invoking_foundry_agent
   # - agent_invocation_completed
   # - persisting_task_suggestions
   # - task_suggestions_persisted
   # - invoice_agent_invocation_completed (status: success)
   ```

4. **Verify Task Suggestions in SQL**
   ```bash
   sqlcmd -S cict-ai-cus-sql.database.windows.net -d cict-ai-cus-sqldb -U your-username -P your-password
   
   -- Query task suggestions for processed invoices
   SELECT TOP 10
       taskSuggestionId,
       invoiceId,
       packageId,
       recommendation,
       createdAtUtc,
       status
   FROM task.TaskSuggestion
   ORDER BY createdAtUtc DESC;
   ```

   Expected: One row per invoice with:
   - Overall recommendation (CAPEX, OPEX, or REVIEW)
   - Confidence score
   - Per-line suggestions in rationale field
   - Status = PENDING (awaiting human review)

5. **Check Function App Metrics**
   - Navigate to: Azure Portal → Function App → Metrics
   - Verify: Executions, Success Rate, Response Time
   - Expected: At least 2 executions, 100% success rate

## Troubleshooting

### Function Not Triggering

1. Verify subscription exists:
   ```bash
   az servicebus topic subscription show \
     --namespace-name "cict-ai-cus-bus" \
     --topic-name "capex-invoice-ready-v1" \
     --name "invoice-agent-invocation" \
     --resource-group "cict-aisandbox-cus-rg"
   ```

2. Check function logs for errors:
   ```bash
   az functionapp log tail --name "capex-invoice-agent-invocation" --resource-group "cict-aisandbox-cus-rg"
   ```

3. Verify Service Bus Receiver role is assigned:
   ```bash
   az role assignment list \
     --assignee "562512ef-d515-428a-8df5-6c4c589812ef" \
     --scope "/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a"
   ```

### Agent Invocation Failures

1. Verify Foundry endpoint and API key are correct
2. Check Foundry service health
3. Review function logs for response parsing errors
4. Verify invoice package structure matches expected schema

### SQL Persistence Errors

1. Verify SQL credentials have INSERT permission on task.TaskSuggestion
2. Check migration was applied successfully
3. Verify schema `task` exists
4. Review function logs for SQL connection errors

## Demo Success Criteria

✓ Service Bus message consumed by function  
✓ Invoice package fetched from Blob Storage  
✓ Foundry agent invoked successfully  
✓ Task suggestions generated (CAPEX/OPEX/REVIEW)  
✓ Results persisted to SQL task.TaskSuggestion table  
✓ Correlation IDs linked through entire pipeline  
✓ Audit trail complete (structured JSON logs)  

## Demo Claims (Post-Validation)

Once this validation succeeds, you can claim:

- ✓ Service Bus consumption implemented
- ✓ Invoice agent integration complete
- ✓ Task suggestions generated with confidence scores
- ✓ Governance and audit trail maintained
- ✓ End-to-end pipeline operational

Do NOT claim:
- Automatic POPA posting (not implemented)
- Final CapEx decisions (requires human review)
- Full production readiness (needs monitoring/alerting)

## Next Steps After Validation

- CICT-34: Build Foundry Invoice Task Determination Agent (if not using existing)
- CICT-35: Configure RBAC and secrets management
- CICT-36: Add observability and monitoring
- CICT-37: Create automated demo runbook
