#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('shared', 'fuelbuildpool', 'fuelbuildpool2')]
    [string] $AgentPool = 'shared',
    [string] $SourceRoot = (Split-Path $PSScriptRoot -Parent),
    [string] $OutputDirectory = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CapexAdfBuilds'),
    [ValidateRange(5, 120)]
    [int] $TimeoutMinutes = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Get-Command az, tar -ErrorAction Stop | Out-Null
$subscription = '0451df15-250e-42f5-b15c-f19a9bb9c21a'
$tenant = '19da1f3c-d958-44ea-850d-195c1502525c'
$registry = 'cictaicusacr'
$repository = 'capex/demo-v2-adf'
$loginServer = "$registry.azurecr.io"
$registryUrl = "https://management.azure.com/subscriptions/$subscription/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.ContainerRegistry/registries/$registry"
$apiVersion = '2019-06-01-preview'
$tag = 'adf-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$terminal = @('Succeeded', 'Failed', 'Canceled', 'Error', 'Timeout')

function Invoke-AzJson {
    param([string[]] $Arguments)
    $raw = & az @Arguments --subscription $subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: az $($Arguments -join ' ')" }
    ($raw -join "`n") | ConvertFrom-Json -NoEnumerate
}

$account = Invoke-AzJson @('account', 'show')
if ($account.id -ne $subscription -or $account.tenantId -ne $tenant) {
    throw 'Azure subscription/tenant does not match the approved Circle K environment.'
}
if ($AgentPool -eq 'shared') {
    $settings = Invoke-AzJson @('acr', 'show', '--name', $registry)
    if ($settings.publicNetworkAccess -ne 'Enabled' -or $settings.networkRuleSet.defaultAction -ne 'Allow') {
        throw 'Shared ACR workers cannot use the current registry access path. Network rules were not changed.'
    }
}
else {
    $pool = Invoke-AzJson @('acr', 'agentpool', 'show', '--registry', $registry, '--name', $AgentPool)
    if ($pool.count -lt 1 -or $pool.provisioningState -ne 'Succeeded') {
        throw "Private pool $AgentPool is not running. This helper never scales shared infrastructure."
    }
}
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$sourceFiles = @(
    'Dockerfile.capex-adf', 'pyproject.toml', 'README.md',
    'autarch\capex\schemas\invoice-package-v1.schema.json',
    'autarch\capex\schemas\invoice-ready-v1.schema.json',
    'examples\data\capex\reference-v2\task-master-us-v2.json',
    'examples\data\capex\reference-v2\policy-config-v2.json',
    'examples\data\capex\reference-v2\asset-class-master-eu-v1.json'
)
$sourceFiles += @(Get-ChildItem (Join-Path $SourceRoot 'autarch') -Recurse -File -Filter '*.py' |
    Where-Object { $_.FullName -notmatch '[\\/](__pycache__|\.venv[^\\/]*|site-packages)[\\/]' } |
    ForEach-Object { [IO.Path]::GetRelativePath($SourceRoot, $_.FullName) })
foreach ($relative in $sourceFiles) {
    $path = Join-Path $SourceRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required build source missing: $relative" }
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Build sources must be regular files, not links: $relative"
    }
}
if ((Get-Content -Raw (Join-Path $SourceRoot 'Dockerfile.capex-adf')) -notmatch 'capex-demo-v2-adf --help') {
    throw 'The separate Dockerfile must validate the ADF CLI during image build.'
}

$evidenceDirectory = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) $tag
New-Item -ItemType Directory -Path $evidenceDirectory | Out-Null
$context = Join-Path ([IO.Path]::GetTempPath()) ('capex-adf-context-' + [guid]::NewGuid().ToString('N'))
$archive = $context + '.tar.gz'
New-Item -ItemType Directory -Path $context | Out-Null
$runId = $null
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
    ConvertTo-Json -InputObject @($manifest) -Depth 4 |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'source-manifest.json') -Encoding utf8NoBOM
    & tar -czf $archive -C $context .
    if ($LASTEXITCODE -ne 0) { throw 'Unable to archive the allowlisted build context.' }
    $upload = Invoke-AzJson @('rest', '--method', 'post', '--url', "$registryUrl/listBuildSourceUploadUrl?api-version=$apiVersion")
    try {
        $uploaded = Invoke-WebRequest -Method Put -Uri $upload.uploadUrl -InFile $archive `
            -Headers @{ 'x-ms-blob-type' = 'BlockBlob' } -ContentType 'application/octet-stream' -SkipHttpErrorCheck
    }
    catch {
        throw "Source upload failed ($($_.Exception.GetType().Name)); signed URL not disclosed."
    }
    if ([int]$uploaded.StatusCode -ne 201) { throw "Source upload returned HTTP $([int]$uploaded.StatusCode)." }
    $request = [ordered]@{
        type = 'DockerBuildRequest'
        imageNames = @("${repository}:$tag")
        isPushEnabled = $true
        isArchiveEnabled = $true
        sourceLocation = $upload.relativePath
        platform = @{ os = 'Linux'; architecture = 'amd64' }
        dockerFilePath = 'Dockerfile.capex-adf'
        timeout = $TimeoutMinutes * 60
    }
    if ($AgentPool -ne 'shared') { $request['agentPoolName'] = $AgentPool }
    $requestPath = Join-Path $context 'build-request.json'
    $request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $requestPath -Encoding utf8NoBOM
    $submitted = Invoke-AzJson @('rest', '--method', 'post', '--url', "$registryUrl/scheduleRun?api-version=$apiVersion", '--body', "@$requestPath")
    $runId = $submitted.properties.runId
    if ([string]::IsNullOrWhiteSpace($runId)) { throw 'ACR did not return a build run ID; inspect registry runs before retrying.' }
    [ordered]@{ runId = $runId; agentPool = $AgentPool; imageTag = "${loginServer}/${repository}:$tag" } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDirectory 'build-request.json') -Encoding utf8NoBOM
    $deadline = [DateTime]::UtcNow.AddMinutes($TimeoutMinutes)
    do {
        $run = Invoke-AzJson @('acr', 'task', 'show-run', '--registry', $registry, '--run-id', $runId)
        if ($run.status -in $terminal) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw "Own build $runId exceeded its timeout." }
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
        throw "Build log download failed ($($_.Exception.GetType().Name)); signed URL not disclosed."
    }
    if ([int]$downloaded.StatusCode -ne 200) { throw "Build log download returned HTTP $([int]$downloaded.StatusCode)." }
    if ($run.status -ne 'Succeeded') { throw "Own ACR run $runId ended with $($run.status). See $evidenceDirectory." }
    $images = @($run.outputImages | Where-Object {
        $_.registry -eq $loginServer -and $_.repository -eq $repository -and $_.tag -eq $tag
    })
    if ($images.Count -ne 1 -or $images[0].digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw 'Build did not return exactly one matching immutable ADF image digest.'
    }
    [ordered]@{
        image = "${loginServer}/${repository}@$($images[0].digest)"
        imageSha256 = $images[0].digest.Substring(7)
        runId = $runId
        taskMasterSha256 = (Get-FileHash (Join-Path $context 'examples\data\capex\reference-v2\task-master-us-v2.json')).Hash.ToLowerInvariant()
        policySha256 = (Get-FileHash (Join-Path $context 'examples\data\capex\reference-v2\policy-config-v2.json')).Hash.ToLowerInvariant()
        adfCliHelpValidatedDuringBuild = $true
    } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'image-reference.json') -Encoding utf8NoBOM
    Write-Host "Separate ADF image built. Evidence: $evidenceDirectory"
}
catch {
    [ordered]@{ runId = $runId; error = $_.Exception.Message } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $evidenceDirectory 'build-error.json') -Encoding utf8NoBOM
    throw
}
finally {
    try {
        if ($null -ne $runId) {
            $ownRun = Invoke-AzJson @('acr', 'task', 'show-run', '--registry', $registry, '--run-id', $runId)
            if ($ownRun.status -notin $terminal) {
                Invoke-AzJson @('acr', 'task', 'cancel-run', '--registry', $registry, '--run-id', $runId) | Out-Null
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $context -Recurse -Force
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
    }
}
