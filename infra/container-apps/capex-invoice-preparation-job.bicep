targetScope = 'resourceGroup'

@description('Name of the Container Apps Job.')
param jobName string = 'capex-invoice-preparation'

@description('Azure region for the job.')
param location string = resourceGroup().location

@description('Resource ID of the existing Container Apps managed environment.')
param containerAppsEnvironmentId string

@description('Fully qualified container image, including its tag.')
param image string

@description('Private registry server, such as example.azurecr.io. Leave empty for a public image.')
param registryServer string = ''

@description('Resource ID of the approved user-assigned managed identity.')
param userAssignedIdentityResourceId string

@description('Client ID of the approved user-assigned managed identity.')
param userAssignedIdentityClientId string

@description('Azure SQL logical server name.')
param sqlServerName string = 'cict-ai-cus-sql'

var sqlServerFqdn = '${sqlServerName}${environment().suffixes.sqlServerHostname}'

@description('Azure SQL database name.')
param sqlDatabaseName string = 'cict-ai-cus-sqldb'

@description('Nightly NCRONTAB schedule in UTC.')
param cronExpression string = '0 2 * * *'

@description('Enable the configured schedule after runtime permissions are ready.')
param scheduleEnabled bool = true

@description('Maximum retries after a failed execution.')
@minValue(0)
param replicaRetryLimit int = 2

@description('Maximum execution duration in seconds.')
@minValue(60)
param replicaTimeoutSeconds int = 1800

@description('CPU cores allocated to the preparation container.')
param containerCpu string = '0.5'

@description('Memory allocated to the preparation container.')
param containerMemory string = '1Gi'

@description('Azure Blob Storage account name for persistent invoice staging.')
param blobStorageAccountName string = 'cictaicusst'

@description('Azure Blob Storage account URL for persistent invoice staging.')
param blobAccountUrl string = 'https://${blobStorageAccountName}.blob.${environment().suffixes.storage}'

@description('Container name for Blob Storage invoices.')
param blobContainer string = 'landing'

@description('Prefix for input blobs (e.g., capex/input/).')
param blobInputPrefix string = 'capex/input/'

@description('Prefix for output blobs (e.g., capex/output/).')
param blobOutputPrefix string = 'capex/output/'

@description('Prefix for quarantine blobs (e.g., capex/quarantine/).')
param blobQuarantinePrefix string = 'capex/quarantine/'

resource preparationJob 'Microsoft.App/jobs@2025-01-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityResourceId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: union(
      {
        triggerType: scheduleEnabled ? 'Schedule' : 'Manual'
        replicaRetryLimit: replicaRetryLimit
        replicaTimeout: replicaTimeoutSeconds
        registries: empty(registryServer)
          ? []
          : [
              {
                server: registryServer
                identity: userAssignedIdentityResourceId
              }
            ]
      },
      scheduleEnabled
        ? {
            scheduleTriggerConfig: {
              cronExpression: cronExpression
              parallelism: 1
              replicaCompletionCount: 1
            }
          }
        : {
            manualTriggerConfig: {
              parallelism: 1
              replicaCompletionCount: 1
            }
          }
    )
    template: {
      containers: [
        {
          name: 'capex-prepare'
          image: image
          env: concat(
            [
              {
                name: 'CAPEX_INPUT_DIR'
                value: '/input'
              }
              {
                name: 'CAPEX_OUTPUT_DIR'
                value: '/output'
              }
              {
                name: 'CAPEX_SQL_SERVER'
                value: sqlServerFqdn
              }
              {
                name: 'CAPEX_SQL_DATABASE'
                value: sqlDatabaseName
              }
              {
                name: 'AZURE_CLIENT_ID'
                value: userAssignedIdentityClientId
              }
            ],
            empty(blobAccountUrl)
              ? []
              : [
                  {
                    name: 'CAPEX_BLOB_ACCOUNT_URL'
                    value: blobAccountUrl
                  }
                  {
                    name: 'CAPEX_BLOB_CONTAINER'
                    value: blobContainer
                  }
                  {
                    name: 'CAPEX_BLOB_INPUT_PREFIX'
                    value: blobInputPrefix
                  }
                  {
                    name: 'CAPEX_BLOB_OUTPUT_PREFIX'
                    value: blobOutputPrefix
                  }
                  {
                    name: 'CAPEX_BLOB_QUARANTINE_PREFIX'
                    value: blobQuarantinePrefix
                  }
                ]
          )
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
        }
      ]
    }
  }
}

output jobResourceId string = preparationJob.id
output managedIdentityResourceId string = userAssignedIdentityResourceId
output managedIdentityClientId string = userAssignedIdentityClientId
output scheduleUtc string = cronExpression
