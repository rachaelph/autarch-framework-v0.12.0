targetScope = 'resourceGroup'

@description('Digest from the approved worker build in the existing registry; no sha256: prefix.')
@minLength(64)
@maxLength(64)
param imageSha256 string

@description('Leave empty for the historical system-identity path. Set cred_capex_uami only after attaching the approved UAMI.')
@allowed([
  ''
  'cred_capex_uami'
])
param armCredentialName string = ''

@description('Strict by default. Demo-only provisional recommendations retain extraction warnings and require analyst review.')
@allowed([
  'strict'
  'provisional-demo'
])
param pdfRecommendationMode string = 'strict'

@description('Conditional demo advice uses disclosed native-currency policies and line-amount proxies; never finance approval.')
@allowed([
  'strict'
  'conditional-demo'
])
param recommendationMode string = 'strict'

@description('Use SQL policy, screening and outcome catalogs; honor native unit-cost basis and defer bundling.')
param governedSqlReferences bool = false

@minLength(64)
@maxLength(64)
param taskMasterSha256 string = '29130f66b5d793b4be93a9d08a1fe725dd2e0b8f1f66adcd8dc6d48a30087cf2'

@minLength(64)
@maxLength(64)
param policySha256 string = 'e43a747b3f773a04897732a8845da1048b1f64e1a48daab0f414c7a6556f3042'

var jobId = resourceId('Microsoft.App/jobs', 'capex-task-determination-proof')
var jobUrl = uri(environment().resourceManager, jobId)
var readPolicy = {
  timeout: '0.00:05:00'
  retry: 2
  retryIntervalInSeconds: 30
  secureInput: false
  secureOutput: false
}
var authentication = empty(armCredentialName) ? {
  type: 'MSI'
  resource: environment().resourceManager
} : {
  type: 'UserAssignedManagedIdentity'
  resource: environment().resourceManager
  credential: {
    referenceName: armCredentialName
    type: 'CredentialReference'
  }
}
var fileFilter = '''
@and(equals(item().type, 'File'), contains(createArray('Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip', 'Kimley Hor - 36764068GRP005.pdf-58aceec5-f35a-48a7-8faa-2f1a195af203.zip', 'JC2000A07465-Ariba.xlsx', 'JC2000A07814 - Ariba+Proj.tool.xlsx'), item().name))
'''
var executionTemplate = {
  containers: [
    {
      name: 'capex-task-determination-proof'
      image: 'cictaicusacr.azurecr.io/capex/demo-v2-adf@sha256:${imageSha256}'
      command: [
        'python'
        '-m'
        'autarch.capex.demo_v2_adf_job'
      ]
      args: concat([
        '--input-base-uri'
        'https://cictaicusst.blob.${environment().suffixes.storage}/landing/capex/input'
        '--output-base-uri'
        'https://cictaicusst.blob.${environment().suffixes.storage}/landing/capex/output/adf-runs'
        '--task-master-uri'
        '/app/references/task-master-us-v2.json'
        '--task-master-sha256'
        taskMasterSha256
        '--policy-uri'
        '/app/references/policy-config-v2.json'
        '--policy-sha256'
        policySha256
        '--execute'
        '--sql-persist'
        '--fresh-pdf-extraction'
        '--pdf-recommendation-mode'
        pdfRecommendationMode
        '--recommendation-mode'
        recommendationMode
        '--workflow-run-id'
        '__ADF_WORKFLOW_RUN_ID__'
      ], governedSqlReferences ? ['--governed-sql-references'] : [])
      env: [
        {
          name: 'AZURE_CLIENT_ID'
          value: '562512ef-d515-428a-8df5-6c4c589812ef'
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: 'https://cict-ai-cus-foundry.cognitiveservices.azure.com/'
        }
        {
          name: 'CAPEX_SQL_SERVER'
          value: 'cict-ai-cus-sql.database.windows.net'
        }
        {
          name: 'CAPEX_SQL_DATABASE'
          value: 'cict-ai-cus-sqldb'
        }
      ]
      resources: {
        cpu: json('1.0')
        memory: '2Gi'
      }
    }
  ]
}
var startExpression = '@replace(\'${replace(string(executionTemplate), '\'', '\'\'')}\', \'__ADF_WORKFLOW_RUN_ID__\', pipeline().RunId)'

resource factory 'Microsoft.DataFactory/factories@2018-06-01' existing = {
  name: 'cict-ai-cus-capex-adf'
}

resource credential 'Microsoft.DataFactory/factories/credentials@2018-06-01' = if (!empty(armCredentialName)) {
  parent: factory
  name: armCredentialName
  properties: {
    type: 'ManagedIdentity'
    typeProperties: {
      resourceId: resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', 'cict-ai-cus-capex-id')
    }
  }
}

resource dataset 'Microsoft.DataFactory/factories/datasets@2018-06-01' = {
  parent: factory
  name: 'ds_capex_landing_files'
  properties: {
    type: 'Binary'
    linkedServiceName: {
      referenceName: 'ls_capex_landing_blob'
      type: 'LinkedServiceReference'
    }
    parameters: {
      folderPath: { type: 'String', defaultValue: 'capex/input' }
      fileName: { type: 'String', defaultValue: '' }
    }
    typeProperties: {
      location: {
        type: 'AzureBlobStorageLocation'
        container: 'landing'
        folderPath: { type: 'Expression', value: '@dataset().folderPath' }
        fileName: { type: 'Expression', value: '@dataset().fileName' }
      }
    }
    annotations: []
  }
}

resource pipeline 'Microsoft.DataFactory/factories/pipelines@2018-06-01' = {
  parent: factory
  name: 'pl_capex_intake_check'
  dependsOn: [
    credential
  ]
  properties: {
    concurrency: 1
    parameters: {
      processInvoices: {
        type: 'Bool'
        defaultValue: false
      }
    }
    variables: {
      executionStatus: { type: 'String', defaultValue: 'NotStarted' }
    }
    annotations: [
      'Existing intake pipeline upgraded in place. Explicitly set processInvoices=true; otherwise stop after discovery with CAPEX_PROCESSING_NOT_ENABLED.'
      'Uses an execution override of capex-task-determination-proof; saved job definition and Friday job are unchanged.'
      empty(armCredentialName)
        ? 'ARM activities use the temporary system-identity exception.'
        : 'ARM activities use the approved existing cict-ai-cus-capex-id UAMI through cred_capex_uami. Blob linked service is unchanged.'
    ]
    activities: [
      {
        name: 'DiscoverSources'
        type: 'GetMetadata'
        policy: readPolicy
        typeProperties: {
          dataset: {
            referenceName: dataset.name
            type: 'DatasetReference'
            parameters: { folderPath: 'capex/input', fileName: '' }
          }
          fieldList: ['childItems']
          storeSettings: { type: 'AzureBlobStorageReadSettings', recursive: false }
          formatSettings: { type: 'BinaryReadSettings' }
        }
      }
      {
        name: 'RequireProcessingOptIn'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'DiscoverSources', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: { type: 'Expression', value: '@pipeline().parameters.processInvoices' }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'ProcessingNotEnabled'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_PROCESSING_NOT_ENABLED'
                message: 'File discovery completed. Processing was not requested; set processInvoices=true only for an approved billable demo execution.'
              }
            }
          ]
        }
      }
      {
        name: 'SelectDemoFiles'
        type: 'Filter'
        dependsOn: [
          { activity: 'RequireProcessingOptIn', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          items: { type: 'Expression', value: '@activity(\'DiscoverSources\').output.childItems' }
          condition: { type: 'Expression', value: fileFilter }
        }
      }
      {
        name: 'RequireAllSources'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'SelectDemoFiles', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: { type: 'Expression', value: '@equals(length(activity(\'SelectDemoFiles\').output.value), 4)' }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'MissingDemoSources'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_INPUTS_MISSING'
                message: 'One or more of the four selected invoice files is missing from landing/capex/input.'
              }
            }
          ]
        }
      }
      {
        name: 'ReadExistingJob'
        type: 'WebActivity'
        policy: readPolicy
        dependsOn: [
          { activity: 'RequireAllSources', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          method: 'GET'
          url: '${jobUrl}?api-version=2025-01-01'
          authentication: authentication
        }
      }
      {
        name: 'RequireSafeJobConfiguration'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'ReadExistingJob', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: {
            type: 'Expression'
            value: '@and(equals(activity(\'ReadExistingJob\').output.properties.configuration.triggerType, \'Manual\'), and(equals(activity(\'ReadExistingJob\').output.properties.configuration.replicaRetryLimit, 0), and(equals(activity(\'ReadExistingJob\').output.properties.configuration.manualTriggerConfig.parallelism, 1), equals(activity(\'ReadExistingJob\').output.properties.configuration.manualTriggerConfig.replicaCompletionCount, 1))))'
          }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'UnsafeExistingJob'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_JOB_CONFIGURATION_CHANGED'
                message: 'Existing proof job is no longer manual, single-replica and retry-free. No job configuration was changed and no execution was started.'
              }
            }
          ]
        }
      }
      {
        name: 'StartJob'
        type: 'WebActivity'
        policy: { timeout: '0.00:10:00', retry: 0, secureInput: false, secureOutput: false }
        dependsOn: [
          { activity: 'RequireSafeJobConfiguration', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          method: 'POST'
          url: '${jobUrl}/start?api-version=2025-01-01'
          authentication: authentication
          headers: { 'Content-Type': 'application/json' }
          body: { type: 'Expression', value: startExpression }
          turnOffAsync: false
        }
      }
      {
        name: 'RequireExecutionIdentity'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'StartJob', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: {
            type: 'Expression'
            value: '@and(not(empty(activity(\'StartJob\').output.name)), equals(toLower(activity(\'StartJob\').output.id), toLower(concat(\'${jobId}/executions/\', activity(\'StartJob\').output.name))))'
          }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'MissingExecutionIdentity'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_EXECUTION_ID_MISSING'
                message: 'ARM did not return this job execution identity. Inspect the start response; never retry an uncertain start or select the latest execution.'
              }
            }
          ]
        }
      }
      {
        name: 'WaitForExecution'
        type: 'Until'
        dependsOn: [
          { activity: 'RequireExecutionIdentity', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          timeout: '0.00:25:00'
          expression: {
            type: 'Expression'
            value: '@contains(createArray(\'Succeeded\', \'Failed\', \'Stopped\', \'Canceled\', \'Degraded\', \'POLL_FAILED\'), variables(\'executionStatus\'))'
          }
          activities: [
            {
              name: 'ReadExecution'
              type: 'WebActivity'
              policy: readPolicy
              typeProperties: {
                method: 'GET'
                url: {
                  type: 'Expression'
                  value: '@concat(\'${jobUrl}/executions/\', activity(\'StartJob\').output.name, \'?api-version=2025-01-01\')'
                }
                authentication: authentication
              }
            }
            {
              name: 'RecordExecutionStatus'
              type: 'SetVariable'
              dependsOn: [
                { activity: 'ReadExecution', dependencyConditions: ['Succeeded'] }
              ]
              typeProperties: {
                variableName: 'executionStatus'
                value: { type: 'Expression', value: '@activity(\'ReadExecution\').output.properties.status' }
              }
            }
            {
              name: 'RecordPollFailure'
              type: 'SetVariable'
              dependsOn: [
                { activity: 'ReadExecution', dependencyConditions: ['Failed'] }
              ]
              typeProperties: { variableName: 'executionStatus', value: 'POLL_FAILED' }
            }
            {
              name: 'PollDelay'
              type: 'Wait'
              dependsOn: [
                { activity: 'RecordExecutionStatus', dependencyConditions: ['Succeeded'] }
              ]
              typeProperties: { waitTimeInSeconds: 20 }
            }
          ]
        }
      }
      {
        name: 'RequireExecutionSucceeded'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'WaitForExecution', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: { type: 'Expression', value: '@equals(variables(\'executionStatus\'), \'Succeeded\')' }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'JobExecutionFailed'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_JOB_FAILED'
                message: 'The correlated job execution did not succeed. Inspect logs and partial artifacts before starting a fresh run.'
              }
            }
          ]
        }
      }
      {
        name: 'CheckCompletionMarker'
        type: 'GetMetadata'
        policy: readPolicy
        dependsOn: [
          { activity: 'RequireExecutionSucceeded', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          dataset: {
            referenceName: dataset.name
            type: 'DatasetReference'
            parameters: {
              folderPath: { type: 'Expression', value: '@concat(\'capex/output/adf-runs/\', pipeline().RunId)' }
              fileName: 'batch.json'
            }
          }
          fieldList: ['exists']
          storeSettings: { type: 'AzureBlobStorageReadSettings', recursive: false }
          formatSettings: { type: 'BinaryReadSettings' }
        }
      }
      {
        name: 'RequireCompletionMarker'
        type: 'IfCondition'
        dependsOn: [
          { activity: 'CheckCompletionMarker', dependencyConditions: ['Succeeded'] }
        ]
        typeProperties: {
          expression: {
            type: 'Expression'
            value: '@equals(activity(\'CheckCompletionMarker\').output.exists, true)'
          }
          ifTrueActivities: []
          ifFalseActivities: [
            {
              name: 'MissingCompletionMarker'
              type: 'Fail'
              typeProperties: {
                errorCode: 'CAPEX_COMPLETION_MISSING'
                message: 'The correlated job returned success but its run-scoped batch.json is missing. Do not present this as completed processing.'
              }
            }
          ]
        }
      }
    ]
  }
}

output pipelineResourceId string = pipeline.id
output datasetResourceId string = dataset.id
output reusedJobResourceId string = jobId
