targetScope = 'resourceGroup'

@description('Name of the Function App for invoice agent invocation.')
param functionAppName string = 'cict-ai-cus-func'

@description('Azure region for the Function App.')
param location string = resourceGroup().location

@description('Resource ID of the existing App Service Plan.')
param appServicePlanId string

@description('Resource ID of the approved user-assigned managed identity.')
param userAssignedIdentityResourceId string

@description('Client ID of the approved user-assigned managed identity.')
param userAssignedIdentityClientId string

@description('Storage account name for Azure Functions runtime.')
param functionsStorageAccountName string

@description('Service Bus namespace FQDN (e.g., cict-ai-cus-bus.servicebus.windows.net).')
param serviceBusNamespace string

@description('Service Bus topic name (e.g., capex-invoice-ready-v1).')
param serviceBusTopicName string = 'capex-invoice-ready-v1'

@description('Service Bus subscription name for the function.')
param serviceBusSubscriptionName string = 'invoice-agent-invocation'

@description('Azure Blob Storage account name for invoice packages.')
param blobStorageAccountName string = 'cictaicusst'

@description('Blob Storage container name.')
param blobContainer string = 'landing'

@description('Azure SQL logical server name.')
param sqlServerName string = 'cict-ai-cus-sql'

@description('Azure SQL database name.')
param sqlDatabaseName string = 'cict-ai-cus-sqldb'

@description('Azure OpenAI endpoint for the Foundry account.')
param foundryEndpoint string


@description('Foundry deployment name.')
param foundryDeployment string = 'gpt-5.1'

@description('TaskMaster region used for task-code governance.')
param taskRegion string = 'US'

@description('Approved TaskMaster workbook SHA-256.')
param taskMasterSha256 string = 'CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13'

@description('Version of the Task Determination prompt contract.')
param taskPromptVersion string = 'task-determination-v1'

@description('Application Insights connection string.')
@secure()
param applicationInsightsConnectionString string

var blobAccountUrl = 'https://${blobStorageAccountName}.blob.${environment().suffixes.storage}'
var sqlServerFqdn = '${sqlServerName}.${environment().suffixes.sqlServerHostname}'

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityResourceId}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlanId
    httpsOnly: true
    siteConfig: {
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: functionsStorageAccountName
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__clientId'
          value: userAssignedIdentityClientId
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'WEBSITE_USE_PLACEHOLDER'
          value: '0'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: applicationInsightsConnectionString
        }
        {
          name: 'ApplicationInsightsAgent_EXTENSION_VERSION'
          value: '~3'
        }
        {
          name: 'XDT_MicrosoftApplicationInsights_Mode'
          value: 'default'
        }
        {
          name: 'ServiceBusConnection__fullyQualifiedNamespace'
          value: serviceBusNamespace
        }
        {
          name: 'ServiceBusConnection__credential'
          value: 'managedidentity'
        }
        {
          name: 'ServiceBusConnection__clientId'
          value: userAssignedIdentityClientId
        }
        {
          name: 'CAPEX_SERVICE_BUS_TOPIC'
          value: serviceBusTopicName
        }
        {
          name: 'CAPEX_SERVICE_BUS_SUBSCRIPTION'
          value: serviceBusSubscriptionName
        }
        {
          name: 'CAPEX_BLOB_ACCOUNT_URL'
          value: blobAccountUrl
        }
        {
          name: 'CAPEX_BLOB_CONTAINER'
          value: blobContainer
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
        {
          name: 'FOUNDRY_ENDPOINT'
          value: foundryEndpoint
        }

        {
          name: 'FOUNDRY_DEPLOYMENT'
          value: foundryDeployment
        }
        {
          name: 'CAPEX_TASK_REGION'
          value: taskRegion
        }
        {
          name: 'CAPEX_TASK_MASTER_SHA256'
          value: taskMasterSha256
        }
        {
          name: 'CAPEX_TASK_PROMPT_VERSION'
          value: taskPromptVersion
        }
      ]
      pythonVersion: '3.12'
      use32BitWorkerProcess: false
      webSocketsEnabled: false
      managedPipelineMode: 'Integrated'
      remoteDebuggingEnabled: false
      http20Enabled: true
    }
  }
}

output functionAppId string = functionApp.id
output functionAppName string = functionApp.name
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'
output identityResourceId string = userAssignedIdentityResourceId
output identityClientId string = userAssignedIdentityClientId
