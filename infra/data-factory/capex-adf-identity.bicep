targetScope = 'resourceGroup'

@description('Current factory location from a fresh ARM read.')
param factoryLocation string

@description('Current factory properties from a fresh ARM read, excluding provisioningState and createTime.')
param factoryProperties object

@description('Current factory tags from the same ARM read.')
param factoryTags object

@description('Current identity map, with the approved existing UAMI added and system identity retained.')
param factoryIdentity object

resource factory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: 'cict-ai-cus-capex-adf'
  location: factoryLocation
  tags: factoryTags
  identity: factoryIdentity
  properties: factoryProperties
}
