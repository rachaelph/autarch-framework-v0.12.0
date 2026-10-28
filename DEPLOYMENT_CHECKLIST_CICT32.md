# CICT-32 Deployment Checklist: Blob Storage Integration

**Status:** Code complete ✓ Tests passing ✓ Bicep compiled ✓  
**Target Demo Date:** 2026-10-22  
**Last Updated:** 2026-10-21  

## Changes Completed

- ✓ Added `azure-storage-blob>=12.25,<13` to pyproject.toml capex dependencies
- ✓ Created `autarch/capex/blob_job.py` with ManagedIdentityCredential support
- ✓ Created `tests/test_capex_blob_job.py` with 5 comprehensive unit tests
- ✓ Added `capex-prepare-blob` entry point to pyproject.toml
- ✓ Updated `infra/container-apps/capex-invoice-preparation-job.bicep` with Blob parameters
- ✓ All 99 capex tests passing
- ✓ Bicep template compiles successfully

## Azure Environment Details

**Subscription:** NA AI Dev/Test (0451df15-250e-42f5-b15c-f19a9bb9c21a)  
**Resource Group:** cict-aisandbox-cus-rg  
**Region:** Central US  

### Reusable Resources

```
ACR:                        cictaicusacr.azurecr.io
ACR build pool:             fuelbuildpool
Container Apps environment: cict-ai-cus-acaenv
Azure SQL server:           cict-ai-cus-sql.database.windows.net
Azure SQL database:         cict-ai-cus-sqldb
Service Bus namespace:      cict-ai-cus-bus
Service Bus topic:          capex-invoice-ready-v1
Storage account:            cictaicusst
Blob container:             test
```

### Managed Identity

```
Name:         cict-ai-cus-capex-id
Client ID:    562512ef-d515-428a-8df5-6c4c589812ef
Principal ID: cf7d7929-5beb-426b-bd19-493c5c0c506d
Resource ID:  /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id
```

## Deployment Steps

### 1. Build New Container Image

Build a new immutable image tag. Do NOT overwrite `20261020.2`.

**Option A: Using Azure Container Registry (Recommended)**

```bash
# Authenticate to Azure
az login

# Set subscription
az account set --subscription "0451df15-250e-42f5-b15c-f19a9bb9c21a"

# Build image using ACR task
az acr build \
  --registry cictaicusacr \
  --image "capex/invoice-runtime:20261021.1" \
  --file Dockerfile .

# Verify image was pushed
az acr repository show-tags \
  --name cictaicusacr \
  --repository "capex/invoice-runtime" \
  --orderby time_desc \
  --top 3
```

**Option B: Using Docker locally (if Docker available)**

```bash
# Build image
docker build -t cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.1 .

# Push to ACR (requires ACR auth)
az acr login --name cictaicusacr
docker push cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.1
```

**Record the image digest:**

```bash
az acr repository show \
  --name cictaicusacr \
  --repository "capex/invoice-runtime" \
  --image "20261021.1" \
  --query "digest" -o tsv
```

Save this digest for later reference.

### 2. Validate Bicep Template

```bash
# Compile Bicep
az bicep build --file infra/container-apps/capex-invoice-preparation-job.bicep

# Verify JSON output exists
ls -la infra/container-apps/capex-invoice-preparation-job.json
```

### 3. Preview Deployment Changes

```bash
# Run what-if to preview changes
az deployment group what-if \
  --name "capex-prep-job-update" \
  --resource-group "cict-aisandbox-cus-rg" \
  --template-file "infra/container-apps/capex-invoice-preparation-job.json" \
  --parameters \
    jobName="capex-invoice-preparation" \
    location="Central US" \
    containerAppsEnvironmentId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.App/managedEnvironments/cict-ai-cus-acaenv" \
    image="cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.1" \
    registryServer="cictaicusacr.azurecr.io" \
    userAssignedIdentityResourceId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id" \
    userAssignedIdentityClientId="562512ef-d515-428a-8df5-6c4c589812ef" \
    sqlServerName="cict-ai-cus-sql" \
    sqlDatabaseName="cict-ai-cus-sqldb" \
    blobAccountUrl="https://cictaicusst.blob.core.windows.net" \
    blobContainer="test" \
    blobInputPrefix="capex/input/" \
    blobOutputPrefix="capex/output/" \
    blobQuarantinePrefix="capex/quarantine/" \
    scheduleEnabled=false \
    containerCpu="0.5" \
    containerMemory="1Gi"

# ✓ VERIFY: Only capex-invoice-preparation is modified, no deletions, no other resources changed
```

### 4. Deploy Updated Preparation Job

```bash
# Deploy with manual trigger (keep schedule disabled until validation passes)
az deployment group create \
  --name "capex-prep-job-update" \
  --resource-group "cict-aisandbox-cus-rg" \
  --template-file "infra/container-apps/capex-invoice-preparation-job.json" \
  --parameters \
    jobName="capex-invoice-preparation" \
    location="Central US" \
    containerAppsEnvironmentId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.App/managedEnvironments/cict-ai-cus-acaenv" \
    image="cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.1" \
    registryServer="cictaicusacr.azurecr.io" \
    userAssignedIdentityResourceId="/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourcegroups/cict-aisandbox-cus-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id" \
    userAssignedIdentityClientId="562512ef-d515-428a-8df5-6c4c589812ef" \
    sqlServerName="cict-ai-cus-sql" \
    sqlDatabaseName="cict-ai-cus-sqldb" \
    blobAccountUrl="https://cictaicusst.blob.core.windows.net" \
    blobContainer="test" \
    blobInputPrefix="capex/input/" \
    blobOutputPrefix="capex/output/" \
    blobQuarantinePrefix="capex/quarantine/" \
    scheduleEnabled=false \
    containerCpu="0.5" \
    containerMemory="1Gi"
```

### 5. Prepare Demo Input Data

Upload controlled invoice samples to Blob Storage:

```bash
# Login to Azure
az login
az account set --subscription "0451df15-250e-242f5-b15c-f19a9bb9c21a"

# Create local directory with test files
mkdir -p ./test-invoices
# Copy your Bruegmann USA and Kimley-Horn ABBYY ZIP files here

# Upload to test/capex/input/ 
az storage blob upload-batch \
  --account-name cictaicusst \
  --destination "test/capex/input/" \
  --source "./test-invoices" \
  --auth-mode login \
  --pattern "*.zip"

# Verify uploads
az storage blob list \
  --account-name cictaicusst \
  --container-name test \
  --prefix "capex/input/" \
  --auth-mode login
```

## End-to-End Validation Steps

### Step 1: List Input Blobs

```bash
az storage blob list \
  --account-name cictaicusst \
  --container-name test \
  --prefix "capex/input/" \
  --auth-mode login \
  --query "[].name" -o table
```

Expected: 2 ZIP files (Bruegmann USA + Kimley-Horn)

### Step 2: Start Preparation Job Manually

```bash
# Trigger the job
az containerapp job start \
  --name "capex-invoice-preparation" \
  --resource-group "cict-aisandbox-cus-rg"

# Get execution details
az containerapp job execution list \
  --name "capex-invoice-preparation" \
  --resource-group "cict-aisandbox-cus-rg" \
  --query "[0].[name,status,startTime,endTime]" -o table

# Follow logs (get execution name first)
EXECUTION_NAME=$(az containerapp job execution list \
  --name "capex-invoice-preparation" \
  --resource-group "cict-aisandbox-cus-rg" \
  --query "[0].name" -o tsv)

az containerapp job execution show \
  --name "capex-invoice-preparation" \
  --resource-group "cict-aisandbox-cus-rg" \
  --execution-name "$EXECUTION_NAME" \
  --query "properties.template.containers[0].logs" -o table
```

Expected: Execution succeeds within ~2 minutes

### Step 3: Verify Output in Blob Storage

```bash
# List output blobs
az storage blob list \
  --account-name cictaicusst \
  --container-name test \
  --prefix "capex/output/" \
  --auth-mode login \
  --query "[].name" -o table

# Download run summary
RUN_ID=$(az storage blob list \
  --account-name cictaicusst \
  --container-name test \
  --prefix "capex/output/" \
  --auth-mode login \
  --query "[0].name" -o tsv | cut -d/ -f3)

az storage blob download \
  --account-name cictaicusst \
  --container-name test \
  --name "capex/output/$RUN_ID/_run-summary.json" \
  --file "_run-summary.json" \
  --auth-mode login

cat _run-summary.json | jq '{runId, succeededCount, failedCount, packages: .packages[].packageId}'
```

Expected: 2 successful packages, 0 failures, JSON and summary files in output directory

### Step 4: Verify SQL Persistence

```bash
# Connect to Azure SQL Database
# User: Use your Entra ID account or SQL auth
sqlcmd -S cict-ai-cus-sql.database.windows.net -d cict-ai-cus-sqldb -U your-username -P your-password

-- Query prepared packages
SELECT TOP 5 
    packageId, 
    invoiceId, 
    createdAtUtc,
    COUNT(*) OVER (PARTITION BY packageId) as lineCount
FROM pub.v1_InvoicePackage
ORDER BY createdAtUtc DESC;

-- Query outbox status
SELECT TOP 10
    outboxId,
    packageId,
    status,
    createdAtUtc
FROM prep.InvoiceOutbox
ORDER BY createdAtUtc DESC;
```

Expected: 2 packages persisted, 2 pending outbox rows

### Step 5: Start Dispatcher Job

```bash
# Trigger dispatcher job
az containerapp job start \
  --name "capex-invoice-ready-dispatch" \
  --resource-group "cict-aisandbox-cus-rg"

# Follow logs
az containerapp job execution list \
  --name "capex-invoice-ready-dispatch" \
  --resource-group "cict-aisandbox-cus-rg" \
  --query "[0].[name,status]" -o table
```

Expected: Execution succeeds within ~30 seconds

### Step 6: Verify Service Bus Publication

```bash
-- Query outbox status (should be PUBLISHED)
SELECT TOP 10
    outboxId,
    packageId,
    status,
    publishedAtUtc
FROM prep.InvoiceOutbox
ORDER BY publishedAtUtc DESC;

-- Check Service Bus metrics in Azure Portal
-- Navigate to: cict-ai-cus-bus > Topics > capex-invoice-ready-v1 > Metrics
-- Verify "Incoming Messages" increased by 2
```

Expected: Outbox rows status = 'PUBLISHED', Service Bus metrics show 2 incoming messages

## Rollback Plan

If deployment fails:

1. Keep the previous image tag `20261020.2` running by reverting the Bicep deployment
2. Restore the prior job configuration:
   ```bash
   az deployment group create \
     --resource-group "cict-aisandbox-cus-rg" \
     --template-file "infra/container-apps/capex-invoice-preparation-job.json" \
     --parameters image="cictaicusacr.azurecr.io/capex/invoice-runtime:20261020.2" ...
   ```
3. Restore state from last successful execution in SQL and Blob Storage
4. No data loss expected as all writes are transactional

## Demo Talking Points (If Validation Succeeds)

✓ Circle K Azure resources reused (no duplicate infrastructure)  
✓ Managed identity used end-to-end (ACR, Blob Storage, SQL, Service Bus)  
✓ ABBYY packages staged from existing Blob Storage  
✓ Preparation validates and persists versioned invoice packages  
✓ Business data and outbox events commit transactionally  
✓ Dispatcher publishes InvoiceReady v1 and records durable status  
✓ Retry and idempotency protections implemented  
✓ Task suggestions use governed task data  
✓ Unsupported cases remain explicit and require human review  
✓ Nothing automatically posts to accounting system  

## Safe Demo Claims (Post-Validation)

If the Blob-to-Service-Bus validation **succeeds**, you can claim:

- End-to-end invoice processing pipeline proven: ABBYY → Preparation → SQL → Service Bus
- No manual intervention required after invoice upload
- Service Bus publication ready for downstream Function and Agent integration
- Data governance and audit trail maintained throughout pipeline

Do NOT claim:
- Downstream Function/Agent integration (not yet deployed)
- Automatic POPA posting (not implemented)
- Final CapEx/OpEx decisions (not autonomously validated)
- European coverage or production readiness

## Next Steps After Validation

- CICT-33: Build Invoice Agent Invocation Azure Function
- CICT-34: Build Foundry Invoice Task Determination Agent
- CICT-35: Configure RBAC and secrets
- CICT-36: Add observability and monitoring
- CICT-37: Create automated test runbook

