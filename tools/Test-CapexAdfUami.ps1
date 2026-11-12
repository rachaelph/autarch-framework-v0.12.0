[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EvidenceRoot = (Join-Path $env:LOCALAPPDATA ('CapexDemoBackups\uami-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)
$ErrorActionPreference = 'Stop'
$subscription = '0451df15-250e-42f5-b15c-f19a9bb9c21a'
$group = 'cict-aisandbox-cus-rg'
$factoryId = "/subscriptions/$subscription/resourceGroups/$group/providers/Microsoft.DataFactory/factories/cict-ai-cus-capex-adf"
$identityId = "/subscriptions/$subscription/resourceGroups/$group/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cict-ai-cus-capex-id"

function Invoke-AzJson {
    param([string[]]$Arguments)
    $raw = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure command failed: $($Arguments -join ' ')" }
    return (($raw -join "`n") | ConvertFrom-Json -AsHashtable)
}

$null = New-Item -ItemType Directory -Path $EvidenceRoot -Force
$factory = Invoke-AzJson @('rest', '--method', 'get', '--url', "${factoryId}?api-version=2018-06-01")
$factory | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'factory-before.json') -Encoding utf8
if ($factory.identity.type -notlike '*SystemAssigned*') {
    throw 'Expected existing system identity is absent. Stop for configuration review.'
}
foreach ($resource in @('pipelines/pl_capex_intake_check', 'datasets/ds_capex_landing_files', 'linkedservices/ls_capex_landing_blob', 'credentials')) {
    $snapshot = Invoke-AzJson @('rest', '--method', 'get', '--url', "$factoryId/${resource}?api-version=2018-06-01")
    $snapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $EvidenceRoot (($resource -replace '/', '-') + '-before.json')) -Encoding utf8
}
$properties = @{}
$writable = @('encryption', 'publicNetworkAccess', 'repoConfiguration', 'globalParameters', 'purviewConfiguration', 'trustModeClaimForMi')
$readOnly = @('createTime', 'provisioningState', 'factoryStatistics', 'version')
foreach ($key in $factory.properties.Keys) {
    if ($key -in $writable) { $properties[$key] = $factory.properties[$key] }
    elseif ($key -notin $readOnly) { throw "Unreviewed factory property: $key. Stop before replacing factory configuration." }
}
$identities = @{}
if ($factory.identity.userAssignedIdentities) {
    foreach ($key in $factory.identity.userAssignedIdentities.Keys) { $identities[$key] = @{} }
}
$identities[$identityId] = @{}
$tags = if ($factory.tags) { $factory.tags } else { @{} }
$identityParameters = @{
    contentVersion = '1.0.0.0'
    parameters = @{
        factoryLocation = @{ value = $factory.location }
        factoryProperties = @{ value = $properties }
        factoryTags = @{ value = $tags }
        factoryIdentity = @{ value = @{ type = 'SystemAssigned, UserAssigned'; userAssignedIdentities = $identities } }
    }
}
$pipelineParameters = @{
    contentVersion = '1.0.0.0'
    parameters = @{
        imageSha256 = @{ value = '4506f434e5fe22923f96beef1c963f33f103d339039ee8e1afd9755ef5b306b9' }
        armCredentialName = @{ value = 'cred_capex_uami' }
    }
}
$identityParameters | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'identity.parameters.json') -Encoding utf8
$pipelineParameters | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'pipeline.parameters.json') -Encoding utf8

foreach ($step in @(
    @{ Name = 'identity'; Template = 'capex-adf-identity.bicep'; Allowed = @($factoryId) },
    @{ Name = 'pipeline'; Template = 'capex-existing-demo.bicep'; Allowed = @("$factoryId/credentials/cred_capex_uami", "$factoryId/pipelines/pl_capex_intake_check", "$factoryId/datasets/ds_capex_landing_files") }
)) {
    $template = Join-Path $SourceRoot "infra\data-factory\$($step.Template)"
    $parameters = Join-Path $EvidenceRoot "$($step.Name).parameters.json"
    $validation = Invoke-AzJson @('deployment', 'group', 'validate', '--subscription', $subscription, '--resource-group', $group, '--template-file', $template, '--parameters', "@$parameters")
    $validation | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $EvidenceRoot "$($step.Name)-validation.json") -Encoding utf8
    $preview = Invoke-AzJson @('deployment', 'group', 'what-if', '--subscription', $subscription, '--resource-group', $group, '--template-file', $template, '--parameters', "@$parameters", '--mode', 'Incremental', '--result-format', 'FullResourcePayloads', '--no-pretty-print')
    $preview | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $EvidenceRoot "$($step.Name)-what-if.json") -Encoding utf8
    if ($preview.status -ne 'Succeeded') { throw "Preview did not succeed: $($step.Name)" }
    foreach ($change in $preview.changes) {
        if ($change.changeType -in @('Ignore', 'NoChange')) { continue }
        if ($change.resourceId -notin $step.Allowed) { throw "Unapproved resource change: $($change.resourceId)" }
        if ($change.changeType -eq 'Create' -and $change.resourceId -ne "$factoryId/credentials/cred_capex_uami") {
            throw "Unexpected resource creation: $($change.resourceId)"
        }
        if ($change.changeType -notin @('Create', 'Modify')) { throw "Unexpected change type: $($change.changeType)" }
        [pscustomobject]@{ Step = $step.Name; Change = $change.changeType; Resource = $change.resourceId } | Format-List
    }
}
Write-Output "ADF_UAMI_PREVIEW_READY: $EvidenceRoot"
Write-Output 'No deployment or invoice execution started. Review full property deltas before publishing.'
