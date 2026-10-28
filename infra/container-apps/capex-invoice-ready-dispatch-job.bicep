targetScope = 'resourceGroup'

@description('Name of the Container Apps dispatcher job.')
param jobName string = 'capex-invoice-ready-dispatch'

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

@description('Fully qualified Service Bus namespace.')
param serviceBusNamespace string = 'cict-ai-cus-bus.servicebus.windows.net'

@description('InvoiceReady v1 topic name.')
param topicName string = 'capex-invoice-ready-v1'

@description('Dispatcher NCRONTAB schedule in UTC.')
param cronExpression string = '*/1 * * * *'

@description('Enable the configured schedule after runtime permissions are ready.')
param scheduleEnabled bool = true

@description('Maximum messages processed by one execution.')
@minValue(1)
param maxMessages int = 100

@description('Maximum retries after a failed execution.')
@minValue(0)
param replicaRetryLimit int = 2

@description('Maximum execution duration in seconds.')
@minValue(60)
param replicaTimeoutSeconds int = 900

@description('CPU cores allocated to the dispatcher container.')
param containerCpu string = '0.5'

@description('Memory allocated to the dispatcher container.')
param containerMemory string = '1Gi'

resource dispatcherJob 'Microsoft.App/jobs@2025-01-01' = {
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
          name: 'capex-dispatch'
          image: image
          command: [
            'capex-dispatch'
          ]
          env: [
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
              name: 'CAPEX_SERVICE_BUS_NAMESPACE'
              value: serviceBusNamespace
            }
            {
              name: 'CAPEX_SERVICE_BUS_TOPIC'
              value: topicName
            }
            {
              name: 'CAPEX_DISPATCH_MAX_MESSAGES'
              value: string(maxMessages)
            }
          ]
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
        }
      ]
    }
  }
}

output jobResourceId string = dispatcherJob.id
output managedIdentityResourceId string = userAssignedIdentityResourceId
output managedIdentityClientId string = userAssignedIdentityClientId
output scheduleUtc string = cronExpression
