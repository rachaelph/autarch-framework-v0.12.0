targetScope = 'resourceGroup'

@description('Existing Premium Service Bus namespace.')
param namespaceName string = 'cict-ai-cus-bus'

@description('InvoiceReady v1 topic.')
param topicName string = 'capex-invoice-ready-v1'

@description('Dedicated subscription for Task Determination.')
param taskDeterminationSubscriptionName string = 'invoice-agent-invocation'

module invoiceReadyTopic 'br/public:avm/res/service-bus/namespace/topic:0.1.1' = {
  name: 'deploy-${topicName}'
  params: {
    namespaceName: namespaceName
    name: topicName
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT1H'
    defaultMessageTimeToLive: 'P14D'
    enableBatchedOperations: true
    enableExpress: false
    enablePartitioning: false
    maxMessageSizeInKilobytes: 1024
    maxSizeInMegabytes: 1024
    supportOrdering: true
  }
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: namespaceName
}

resource invoiceReadyTopicResource 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' existing = {
  parent: serviceBusNamespace
  name: topicName
}

resource taskDeterminationSubscription 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01' = {
  parent: invoiceReadyTopicResource
  name: taskDeterminationSubscriptionName
  properties: {
    deadLetteringOnFilterEvaluationExceptions: true
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
    enableBatchedOperations: true
    lockDuration: 'PT5M'
    maxDeliveryCount: 10
    requiresSession: false
    status: 'Active'
  }
  dependsOn: [
    invoiceReadyTopic
  ]
}

output namespaceName string = namespaceName
output topicName string = topicName
output taskDeterminationSubscriptionName string = taskDeterminationSubscription.name
