targetScope = 'resourceGroup'

@description('Managed identity resource ID.')
param managedIdentityResourceId string

@description('Service Bus namespace name.')
param serviceBusNamespaceName string = 'cict-ai-cus-bus'

@description('Service Bus topic name.')
param serviceBusTopicName string = 'capex-invoice-ready-v1'

@description('Blob Storage account name.')
param blobStorageAccountName string = 'cictaicusst'

@description('Foundry account name for model invocation.')
param foundryAccountName string = 'cict-ai-cus-foundry'

@description('Blob container for invoice staging.')
param blobContainerName string = 'landing'

var serviceBusDataReceiverRoleId = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247ef'
var serviceBusDataSenderRoleId = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

var managedIdentityPrincipalId = reference(managedIdentityResourceId, '2023-01-31').principalId

// Service Bus Topic resource
resource serviceBusTopic 'Microsoft.ServiceBus/namespaces/topics@2021-11-01' existing = {
  name: '${serviceBusNamespaceName}/${serviceBusTopicName}'
}

// Service Bus Data Receiver role assignment
resource serviceBusDataReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusTopic.id, managedIdentityResourceId, serviceBusDataReceiverRoleId)
  scope: serviceBusTopic
  properties: {
    roleDefinitionId: '/subscriptions/${subscription().subscriptionId}/providers/Microsoft.Authorization/roleDefinitions/${serviceBusDataReceiverRoleId}'
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Service Bus Data Sender role assignment
resource serviceBusDataSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusTopic.id, managedIdentityResourceId, serviceBusDataSenderRoleId)
  scope: serviceBusTopic
  properties: {
    roleDefinitionId: '/subscriptions/${subscription().subscriptionId}/providers/Microsoft.Authorization/roleDefinitions/${serviceBusDataSenderRoleId}'
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Blob container resource
resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' existing = {
  name: '${blobStorageAccountName}/default/${blobContainerName}'
}

// Storage Blob Data Contributor role assignment on container
resource storageBlobDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(blobContainer.id, managedIdentityResourceId, storageBlobDataContributorRoleId)
  scope: blobContainer
  properties: {
    roleDefinitionId: '/subscriptions/${subscription().subscriptionId}/providers/Microsoft.Authorization/roleDefinitions/${storageBlobDataContributorRoleId}'
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Foundry account resource
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

// Cognitive Services OpenAI User role assignment
resource foundryModelUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, managedIdentityResourceId, cognitiveServicesOpenAIUserRoleId)
  scope: foundryAccount
  properties: {
    roleDefinitionId: '/subscriptions/${subscription().subscriptionId}/providers/Microsoft.Authorization/roleDefinitions/${cognitiveServicesOpenAIUserRoleId}'
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output foundryModelUserRoleId string = foundryModelUserRole.id
output serviceBusRoleId string = serviceBusDataReceiverRole.id
output serviceBusSenderRoleId string = serviceBusDataSenderRole.id
output blobRoleId string = storageBlobDataContributorRole.id
output managedIdentityPrincipalId string = managedIdentityPrincipalId
