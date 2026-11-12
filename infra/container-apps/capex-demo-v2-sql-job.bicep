targetScope = 'resourceGroup'

@description('Name of the SQL-backed Demo V2 manual job.')
param jobName string = 'capex-demo-v2-sql'

param location string = resourceGroup().location

@description('Required organizational tags.')
param tags object = resourceGroup().tags

@description('Resource ID of the existing private-network Container Apps environment.')
param containerAppsEnvironmentId string

@description('Fully qualified updated image, preferably pinned by digest.')
param image string

param registryServer string
param userAssignedIdentityResourceId string
param userAssignedIdentityClientId string

@description('Explicit SQL CLI arguments. Use read-only preflight flags before reviewed execution.')
@minLength(1)
param jobArguments array

param sqlServerName string = 'cict-ai-cus-sql'
param sqlDatabaseName string = 'cict-ai-cus-sqldb'

@description('Existing Azure OpenAI endpoint; required only for opt-in --llm-showcase.')
param azureOpenAIEndpoint string = ''

resource sqlDemoJob 'Microsoft.App/jobs@2025-01-01' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityResourceId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      triggerType: 'Manual'
      replicaRetryLimit: 0
      replicaTimeout: 1800
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registryServer
          identity: userAssignedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'capex-demo-v2-sql'
          image: image
          command: [
            'capex-demo-v2-sql'
          ]
          args: jobArguments
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: userAssignedIdentityClientId
            }
            {
              name: 'CAPEX_SQL_SERVER'
              value: '${sqlServerName}${environment().suffixes.sqlServerHostname}'
            }
            {
              name: 'CAPEX_SQL_DATABASE'
              value: sqlDatabaseName
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAIEndpoint
            }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

output jobResourceId string = sqlDemoJob.id
