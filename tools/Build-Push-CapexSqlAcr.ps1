#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('fuelbuildpool', 'fuelbuildpool2', 'shared')]
    [string] $AgentPool = 'fuelbuildpool',
    [string] $SourceRoot = (Split-Path $PSScriptRoot -Parent),
    [string] $OutputDirectory = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CapexSqlBuilds'),
    [ValidateRange(5, 120)]
    [int] $TimeoutMinutes = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Get-Command az, tar -ErrorAction Stop | Out-Null

$subscription = '0451df15-250e-42f5-b15c-f19a9bb9c21a'
$tenant = '19da1f3c-d958-44ea-850d-195c1502525c'
$registry = 'cictaicusacr'
$repository = 'capex/demo-v2'
$loginServer = "$registry.azurecr.io"
$registryUrl = "https://management.azure.com/subscriptions/$subscription/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ContainerRegistry/registries/$registry"
$apiVersion = '2019-06-01-preview'
$tag = 'sql-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$common = @('--subscription', $subscription, '--only-show-errors')
$terminalStatuses = @('Succeeded', 'Failed', 'Canceled', 'Error', 'Timeout')

function Invoke-AzJson {
    param([string[]] $Arguments)
    $raw = & az @Arguments @common --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: az $($Arguments -join ' ')"
    }
    ($raw -join "`n") | ConvertFrom-Json -NoEnumerate
}

function Get-ActivePoolRuns {
    $runs = Invoke-AzJson @('acr', 'task', 'list-runs', '--registry', $registry, '--top', '100')
    @($runs |
        Where-Object { $_.agentPoolName -eq $AgentPool -and $_.status -notin $terminalStatuses })
}

$account = Invoke-AzJson @('account', 'show')
if ($account.id -ne $subscription -or $account.tenantId -ne $tenant) {
    throw 'Azure subscription/tenant does not match the approved Circle K environment.'
}
if ($AgentPool -eq 'shared') {
    $registrySettings = Invoke-AzJson @('acr', 'show', '--name', $registry)
    if ($registrySettings.publicNetworkAccess -ne 'Enabled' -or $registrySettings.networkRuleSet.defaultAction -ne 'Allow') {
        throw 'Shared workers require an operator-approved registry access path. This script does not change network rules.'
    }
}
else {
    $pool = Invoke-AzJson @('acr', 'agentpool', 'show', '--registry', $registry, '--name', $AgentPool)
    if ($pool.count -ne 0 -or $pool.provisioningState -ne 'Succeeded') {
        throw "Pool $AgentPool must be idle at count 0 with provisioningState Succeeded. No capacity was changed."
    }
    if (@(Get-ActivePoolRuns).Count -ne 0) {
        throw "Pool $AgentPool has an active or queued run. No capacity was changed."
    }
}

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$sourceFiles = @('Dockerfile.capex-v2', 'pyproject.toml', 'README.md')
$sourceFiles += @(Get-ChildItem (Join-Path $SourceRoot 'autarch') -Recurse -File -Filter '*.py' |
    Where-Object { $_.FullName -notmatch '[\\/](__pycache__|\.venv[^\\/]*|site-packages)[\\/]' } |
    ForEach-Object { [IO.Path]::GetRelativePath($SourceRoot, $_.FullName) })
$sourceFiles += @(
    'autarch\capex\schemas\invoice-package-v1.schema.json',
    'autarch\capex\schemas\invoice-ready-v1.schema.json'
)
foreach ($relative in $sourceFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $relative) -PathType Leaf)) {
        throw "Required build source is missing: $relative"
    }
}
if ((Get-Content -Raw (Join-Path $SourceRoot 'Dockerfile.capex-v2')) -notmatch '(?m)^RUN capex-demo-v2-sql --help\s*$') {
    throw 'Dockerfile must validate capex-demo-v2-sql --help during the build.'
}

$evidenceDirectory = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) $tag
New-Item -ItemType Directory -Path $evidenceDirectory | Out-Null
Write-Host "Build evidence directory: $evidenceDirectory"
$context = Join-Path ([IO.Path]::GetTempPath()) ('capex-acr-context-' + [guid]::NewGuid().ToString('N'))
$archive = $context + '.tar.gz'
New-Item -ItemType Directory -Path $context | Out-Null
$runId = $null
$ownsPoolScale = $false
try {
    $manifest = foreach ($relative in $sourceFiles) {
        $destination = Join-Path $context $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceRoot $relative) -Destination $destination
        [ordered]@{
            path = $relative
            sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    @('**', '!Dockerfile.capex-v2', '!pyproject.toml', '!README.md', '!autarch/', '!autarch/**') |
        Set-Content -LiteralPath (Join-Path $context '.dockerignore') -Encoding utf8NoBOM
    ConvertTo-Json -InputObject @($manifest) -Depth 4 |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'source-manifest.json') -Encoding utf8NoBOM

    # Upload and queue before allocating a billable worker.
    & tar -czf $archive -C $context .
    if ($LASTEXITCODE -ne 0) { throw 'Unable to archive the clean build context.' }
    $upload = Invoke-AzJson @('rest', '--method', 'post', '--url', "$registryUrl/listBuildSourceUploadUrl?api-version=$apiVersion")
    try {
        $uploaded = Invoke-WebRequest -Method Put -Uri $upload.uploadUrl -InFile $archive `
            -Headers @{ 'x-ms-blob-type' = 'BlockBlob' } -ContentType 'application/octet-stream' -SkipHttpErrorCheck
    }
    catch {
        throw "Source upload transport failed ($($_.Exception.GetType().Name)); no worker was allocated."
    }
    if ([int]$uploaded.StatusCode -ne 201) {
        throw "ACR source upload returned HTTP $([int]$uploaded.StatusCode); no worker was allocated."
    }
    $requestPath = Join-Path $context 'build-request.json'
    $request = [ordered]@{
        type = 'DockerBuildRequest'
        imageNames = @("${repository}:$tag")
        isPushEnabled = $true
        isArchiveEnabled = $true
        sourceLocation = $upload.relativePath
        platform = @{ os = 'Linux'; architecture = 'amd64' }
        dockerFilePath = 'Dockerfile.capex-v2'
        timeout = $TimeoutMinutes * 60
    }
    if ($AgentPool -ne 'shared') { $request['agentPoolName'] = $AgentPool }
    $request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $requestPath -Encoding utf8NoBOM
    # ARM returns the run ID as JSON; some CLI versions suppress acr build --no-wait output.
    $submitted = Invoke-AzJson @(
        'rest', '--method', 'post', '--url', "$registryUrl/scheduleRun?api-version=$apiVersion",
        '--body', "@$requestPath"
    )
    $runId = $submitted.properties.runId
    if ([string]::IsNullOrWhiteSpace($runId)) {
        throw 'ACR did not return a build run ID; no worker was allocated.'
    }
    [ordered]@{ runId = $runId; agentPool = $AgentPool; imageTag = "${loginServer}/${repository}:$tag" } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDirectory 'build-request.json') -Encoding utf8NoBOM
    Write-Host "Uploaded clean source and queued ACR run $runId."
    if ($AgentPool -ne 'shared') {
        $pool = Invoke-AzJson @('acr', 'agentpool', 'show', '--registry', $registry, '--name', $AgentPool)
        $otherRuns = @(Get-ActivePoolRuns | Where-Object { $_.runId -ne $runId })
        if ($pool.count -ne 0 -or $pool.provisioningState -ne 'Succeeded' -or $otherRuns.Count -ne 0) {
            throw 'Pool activity changed after upload; refusing to alter shared capacity.'
        }
        $ownsPoolScale = $true
        Write-Host "Temporarily starting one worker in $AgentPool."
        Invoke-AzJson @('acr', 'agentpool', 'update', '--registry', $registry, '--name', $AgentPool, '--count', '1') | Out-Null
    }
    else {
        Write-Host 'Using an Azure shared build worker; no private pool is scaled.'
    }

    $deadline = [DateTime]::UtcNow.AddMinutes($TimeoutMinutes)
    do {
        $run = Invoke-AzJson @('acr', 'task', 'show-run', '--registry', $registry, '--run-id', $runId)
        if ($run.status -in $terminalStatuses) { break }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "ACR run $runId exceeded the $TimeoutMinutes minute build timeout."
        }
        Write-Host "ACR run ${runId}: $($run.status)"
        Start-Sleep -Seconds 15
    } while ($true)
    $run | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'build-result.json') -Encoding utf8NoBOM
    $log = Invoke-AzJson @('rest', '--method', 'post', '--url', "$registryUrl/runs/$runId/listLogSasUrl?api-version=$apiVersion")
    try {
        $downloaded = Invoke-WebRequest -Method Get -Uri $log.logLink `
            -OutFile (Join-Path $evidenceDirectory 'build.log') -PassThru -SkipHttpErrorCheck
    }
    catch {
        throw "ACR log download failed ($($_.Exception.GetType().Name)); signed URL not disclosed."
    }
    if ([int]$downloaded.StatusCode -ne 200) {
        throw "ACR log download returned HTTP $([int]$downloaded.StatusCode) for run $runId."
    }
    if ($run.status -ne 'Succeeded') {
        throw "ACR run $runId ended with $($run.status). See $evidenceDirectory."
    }
    $images = @($run.outputImages | Where-Object {
        $_.registry -eq $loginServer -and $_.repository -eq $repository -and $_.tag -eq $tag
    })
    if ($images.Count -ne 1 -or $images[0].digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "ACR run $runId did not provide exactly one matching immutable image digest."
    }
    [ordered]@{
        image = "${loginServer}/${repository}@$($images[0].digest)"
        imageTag = "${loginServer}/${repository}:$tag"
        digest = $images[0].digest
        runId = $runId
        agentPool = $AgentPool
        sqlCliHelpValidatedDuringBuild = $true
    } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'image-reference.json') -Encoding utf8NoBOM
    Write-Host "Build and SQL CLI help check succeeded. Image evidence: $evidenceDirectory"
}
catch {
    [ordered]@{ runId = $runId; agentPool = $AgentPool; error = $_.Exception.Message } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDirectory 'build-error.json') -Encoding utf8NoBOM
    throw
}
finally {
    try {
        if ($null -ne $runId) {
            $currentRun = Invoke-AzJson @('acr', 'task', 'show-run', '--registry', $registry, '--run-id', $runId)
            if ($currentRun.status -notin $terminalStatuses) {
                Invoke-AzJson @('acr', 'task', 'cancel-run', '--registry', $registry, '--run-id', $runId) | Out-Null
            }
        }
    }
    finally {
        try {
            if ($ownsPoolScale) {
                $remaining = @(Get-ActivePoolRuns)
                $pool = Invoke-AzJson @('acr', 'agentpool', 'show', '--registry', $registry, '--name', $AgentPool)
                if ($remaining.Count -ne 0 -or $pool.count -notin @(0, 1)) {
                    throw "Pool $AgentPool changed or still has active work; capacity was not reduced. Operator must review billable capacity."
                }
                if ($pool.count -ne 0 -or $pool.provisioningState -ne 'Succeeded') {
                    Invoke-AzJson @('acr', 'agentpool', 'update', '--registry', $registry, '--name', $AgentPool, '--count', '0') | Out-Null
                }
                $pool = Invoke-AzJson @('acr', 'agentpool', 'show', '--registry', $registry, '--name', $AgentPool)
                if ($pool.count -ne 0 -or $pool.provisioningState -ne 'Succeeded') {
                    throw "Pool $AgentPool has not returned to its original idle state. Operator review required."
                }
                Write-Host "Pool $AgentPool restored to count 0."
            }
        }
        finally {
            Remove-Item -LiteralPath $context -Recurse -Force
            if (Test-Path -LiteralPath $archive) {
                Remove-Item -LiteralPath $archive -Force
            }
        }
    }
}
