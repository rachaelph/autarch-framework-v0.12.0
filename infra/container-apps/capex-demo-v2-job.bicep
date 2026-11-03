targetScope = 'resourceGroup'

@description('Name of the direct Demo V2 Container Apps Job.')
param jobName string = 'capex-demo-v2'

@description('Azure region for the job.')
param location string = resourceGroup().location

@description('Required organizational tags for the job.')
param tags object

@description('Resource ID of the existing Container Apps managed environment.')
param containerAppsEnvironmentId string

@description('Fully qualified Demo V2 container image, including its immutable tag.')
param image string

@description('Private registry server, such as example.azurecr.io.')
param registryServer string

@description('Resource ID of the user-assigned managed identity.')
param userAssignedIdentityResourceId string

@description('Client ID of the user-assigned managed identity.')
param userAssignedIdentityClientId string

@description('Blob URI of the immutable DemoWorkflowRequest.')
param requestUri string

@description('Blob URI of the governed TaskMaster snapshot.')
param taskMasterUri string

@description('Blob URI of the policy snapshot.')
param policyUri string

@description('Blob URI of the capitalization rule set. Required for synthetic runs.')
param ruleSetUri string = ''

@description('Blob URI of the synthetic-input register. Required for synthetic runs.')
param syntheticRegisterUri string = ''

@description('Blob URI where this execution must create its immutable result.')
param outputUri string

@description('Microsoft Foundry OpenAI endpoint.')
param azureOpenAiEndpoint string

@description('Azure SQL logical server name.')
param sqlServerName string = 'cict-ai-cus-sql'

@description('Azure SQL database name.')
param sqlDatabaseName string = 'cict-ai-cus-sqldb'

@description('Maximum retries after a failed execution.')
@minValue(0)
param replicaRetryLimit int = 1

@description('Maximum execution duration in seconds.')
@minValue(60)
param replicaTimeoutSeconds int = 1800

@description('CPU cores allocated to the batch container.')
param containerCpu string = '1.0'

@description('Memory allocated to the batch container.')
param containerMemory string = '2Gi'

var sqlServerFqdn = '${sqlServerName}${environment().suffixes.sqlServerHostname}'

resource demoV2Job 'Microsoft.App/jobs@2025-01-01' = {
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
      replicaRetryLimit: replicaRetryLimit
      replicaTimeout: replicaTimeoutSeconds
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
          name: 'capex-demo-v2'
          image: image
          env: concat(
            [
              {
                name: 'AZURE_CLIENT_ID'
                value: userAssignedIdentityClientId
              }
              {
                name: 'AZURE_OPENAI_ENDPOINT'
                value: azureOpenAiEndpoint
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
                name: 'CAPEX_V2_REQUEST_URI'
                value: requestUri
              }
              {
                name: 'CAPEX_V2_TASK_MASTER_URI'
                value: taskMasterUri
              }
              {
                name: 'CAPEX_V2_POLICY_URI'
                value: policyUri
              }
              {
                name: 'CAPEX_V2_OUTPUT_URI'
                value: outputUri
              }
            ],
            empty(ruleSetUri)
              ? []
              : [
                  {
                    name: 'CAPEX_V2_RULE_SET_URI'
                    value: ruleSetUri
                  }
                ],
            empty(syntheticRegisterUri)
              ? []
              : [
                  {
                    name: 'CAPEX_V2_SYNTHETIC_REGISTER_URI'
                    value: syntheticRegisterUri
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

output jobResourceId string = demoV2Job.id
output managedIdentityResourceId string = userAssignedIdentityResourceId
output resultUri string = outputUri
