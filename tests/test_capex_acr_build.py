"""Exercise the cloud-build operator script without making Azure changes."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")

HARNESS = r"""
param($BuildScript, $SourceRoot, $OutputDirectory, $Scenario, $Trace)
$ErrorActionPreference = 'Stop'
$global:Scenario = $Scenario
$global:Trace = $Trace
$global:PoolCount = 0
$global:PoolState = 'Succeeded'
$global:RunStatus = 'Queued'
$global:Submitted = $false
$global:Tag = ''
function global:Invoke-WebRequest {
    param($Method, $Uri, $InFile, $Headers, $ContentType, $OutFile, [switch]$PassThru, [switch]$SkipHttpErrorCheck)
    if ($Method -eq 'Get') {
        'mock build log: capex-demo-v2-sql --help ' + [char]0x2500 |
            Set-Content -LiteralPath $OutFile -Encoding utf8NoBOM
        return @{ StatusCode = $(if ($global:Scenario -eq 'logs-failure') { 403 } else { 200 }) }
    }
    @(tar -tf $InFile | Where-Object { -not $_.EndsWith('/') }) |
        ConvertTo-Json | Set-Content -LiteralPath ($global:Trace + '.context.json')
    return @{ StatusCode = $(if ($global:Scenario -eq 'upload-failure') { 403 } else { 201 }) }
}
function global:az {
    $a = @($args)
    ConvertTo-Json -InputObject $a -Compress | Add-Content -LiteralPath $global:Trace
    $global:LASTEXITCODE = 0
    $command = $a[0..1] -join ' '
    if ($command -eq 'account show') {
        return '{"id":"0451df15-250e-42f5-b15c-f19a9bb9c21a","tenantId":"19da1f3c-d958-44ea-850d-195c1502525c"}'
    }
    if ($command -eq 'acr show') {
        if ($global:Scenario -eq 'shared-blocked') {
            return '{"publicNetworkAccess":"Disabled","networkRuleSet":{"defaultAction":"Deny"}}'
        }
        return '{"publicNetworkAccess":"Enabled","networkRuleSet":{"defaultAction":"Allow"}}'
    }
    if ($command -eq 'acr agentpool') {
        if ($a[2] -eq 'update') {
            $global:PoolCount = [int]$a[[array]::IndexOf($a, '--count') + 1]
            if ($global:Scenario -in @('scale-failure', 'scale-rollback') -and $global:PoolCount -eq 1) {
                if ($global:Scenario -eq 'scale-rollback') {
                    $global:PoolCount = 0
                    $global:PoolState = 'Failed'
                }
                $global:LASTEXITCODE = 1
                return
            }
            $global:PoolState = 'Succeeded'
        }
        return (@{ count = $global:PoolCount; provisioningState = $global:PoolState } | ConvertTo-Json -Compress)
    }
    if ($a[0] -eq 'rest') {
        $url = $a[[array]::IndexOf($a, '--url') + 1]
        if ($url.Contains('/listBuildSourceUploadUrl?')) {
            return '{"uploadUrl":"https://example.invalid/mock-upload","relativePath":"mock-clean-source.tar.gz"}'
        }
        if ($url.Contains('/listLogSasUrl?')) {
            return '{"logLink":"https://example.invalid/mock-log"}'
        }
        if ($url.Contains('/scheduleRun?')) {
            if ($global:Scenario -eq 'schedule-failure') {
                $global:LASTEXITCODE = 1
                return
            }
            $requestPath = $a[[array]::IndexOf($a, '--body') + 1].Substring(1)
            $request = Get-Content -Raw -LiteralPath $requestPath | ConvertFrom-Json
            $namedPool = @($request.PSObject.Properties | Where-Object { $_.Name -eq 'agentPoolName' })
            if ($global:Scenario.StartsWith('shared')) {
                if ($namedPool.Count -ne 0) { throw 'Shared builds must not name a private pool.' }
            }
            elseif ($request.agentPoolName -ne 'fuelbuildpool') { throw 'Wrong private pool.' }
            if ($request.type -ne 'DockerBuildRequest' -or
                $request.dockerFilePath -ne 'Dockerfile.capex-v2' -or
                $request.sourceLocation -ne 'mock-clean-source.tar.gz' -or
                $request.platform.os -ne 'Linux' -or
                -not $request.isPushEnabled) { throw 'Invalid ACR build request.' }
            $global:Tag = ($request.imageNames[0] -split ':')[1]
            $global:Submitted = $true
            return '{"properties":{"runId":"mock-own-run"}}'
        }
    }
    if ($command -eq 'acr task') {
        switch ($a[2]) {
            'list-runs' {
                $runs = @(
                    @{ runId = 'historic-failed'; agentPoolName = 'fuelbuildpool'; status = 'Failed' }
                    @{ runId = 'different-pool'; agentPoolName = 'fuelbuildpool2'; status = 'Running' }
                )
                if ($global:Submitted) {
                    $runs += @{ runId = 'mock-own-run'; agentPoolName = 'fuelbuildpool'; status = $global:RunStatus }
                }
                if ($global:Scenario -eq 'busy-pool' -or
                    ($global:Submitted -and $global:Scenario -eq 'concurrent-work') -or
                    ($global:RunStatus -eq 'Succeeded' -and $global:Scenario -eq 'cleanup-busy')) {
                    $runs += @{ runId = 'someone-else'; agentPoolName = 'fuelbuildpool'; status = 'Running' }
                }
                return (ConvertTo-Json -InputObject $runs -Compress)
            }
            'show-run' {
                if (($global:PoolCount -eq 1 -and $global:Scenario -ne 'scale-failure') -or
                    $global:Scenario -eq 'shared-success') {
                    $global:RunStatus = if ($global:Scenario -eq 'build-failure') { 'Failed' } else { 'Succeeded' }
                }
                return (@{
                    runId = 'mock-own-run'; status = $global:RunStatus
                    outputImages = @(@{
                        registry = 'cictaicusacr.azurecr.io'; repository = 'capex/demo-v2'
                        tag = $global:Tag; digest = 'sha256:' + ('a' * 64)
                    })
                } | ConvertTo-Json -Depth 5 -Compress)
            }
            'cancel-run' {
                $global:RunStatus = 'Canceled'
                return
            }
            'logs' { return 'mock build log: capex-demo-v2-sql --help' }
        }
    }
    throw "Unexpected mock Azure command: $($a -join ' ')"
}
try {
    $pool = if ($Scenario.StartsWith('shared')) { 'shared' } else { 'fuelbuildpool' }
    & $BuildScript -SourceRoot $SourceRoot -OutputDirectory $OutputDirectory -AgentPool $pool
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def run_build(tmp_path: Path, scenario: str) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    source = tmp_path / "source"
    files = {
        "Dockerfile.capex-v2": (ROOT / "Dockerfile.capex-v2").read_text(),
        "pyproject.toml": "[project]\nname = 'mock'\n",
        "README.md": "Mock source\n",
        "autarch/__init__.py": "",
        "autarch/capex/schemas/invoice-package-v1.schema.json": "{}",
        "autarch/capex/schemas/invoice-ready-v1.schema.json": "{}",
        "autarch/.env": "DO_NOT_UPLOAD",
        "autarch/.venv/secret.py": "DO_NOT_UPLOAD",
        "invoice.xlsx": "DO_NOT_UPLOAD",
        "credentials.json": "DO_NOT_UPLOAD",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS)
    trace = tmp_path / "calls.jsonl"
    evidence = tmp_path / "evidence"
    result = subprocess.run(
        [
            str(PWSH), "-NoProfile", "-File", str(harness),
            "-BuildScript", str(ROOT / "tools" / "Build-Push-CapexSqlAcr.ps1"),
            "-SourceRoot", str(source), "-OutputDirectory", str(evidence),
            "-Scenario", scenario, "-Trace", str(trace),
        ],
        capture_output=True, text=True, timeout=30,
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    return result, calls, evidence


def updates(calls: list[list[str]]) -> list[str]:
    return [
        call[call.index("--count") + 1]
        for call in calls if call[:3] == ["acr", "agentpool", "update"]
    ]


def test_cloud_build_source_only_digest_and_pool_cleanup(tmp_path: Path) -> None:
    result, calls, evidence = run_build(tmp_path, "success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert updates(calls) == ["1", "0"]
    build_index = next(i for i, call in enumerate(calls) if any("/scheduleRun?" in arg for arg in call))
    scale_index = next(i for i, call in enumerate(calls) if call[:3] == ["acr", "agentpool", "update"])
    assert build_index < scale_index
    context = json.loads((tmp_path / "calls.jsonl.context.json").read_text())
    assert {name.replace("\\", "/").removeprefix("./") for name in context} == {
        ".dockerignore", "Dockerfile.capex-v2", "pyproject.toml", "README.md",
        "autarch/__init__.py",
        "autarch/capex/schemas/invoice-package-v1.schema.json",
        "autarch/capex/schemas/invoice-ready-v1.schema.json",
    }
    reference = json.loads(next(evidence.glob("*/image-reference.json")).read_text())
    assert reference["image"] == "cictaicusacr.azurecr.io/capex/demo-v2@sha256:" + "a" * 64
    assert reference["sqlCliHelpValidatedDuringBuild"] is True
    assert reference["runId"] == "mock-own-run"
    assert "\u2500" in next(evidence.glob("*/build.log")).read_text(encoding="utf-8")
    request_path = Path(calls[build_index][calls[build_index].index("--body") + 1][1:])
    assert not request_path.parent.exists()
    assert not Path(str(request_path.parent) + ".tar.gz").exists()


@pytest.mark.parametrize("scenario", ["upload-failure", "schedule-failure", "busy-pool", "concurrent-work"])
def test_cloud_build_does_not_scale_on_upload_failure_or_shared_activity(tmp_path: Path, scenario: str) -> None:
    result, calls, evidence = run_build(tmp_path, scenario)
    assert result.returncode != 0
    assert updates(calls) == []
    assert not list(evidence.glob("*/image-reference.json"))
    canceled = [call for call in calls if call[:3] == ["acr", "task", "cancel-run"]]
    assert len(canceled) == (1 if scenario == "concurrent-work" else 0)
    if canceled:
        assert canceled[0][canceled[0].index("--run-id") + 1] == "mock-own-run"


@pytest.mark.parametrize("scenario", ["build-failure", "scale-failure", "scale-rollback", "logs-failure"])
def test_cloud_build_restores_owned_capacity_on_failure(tmp_path: Path, scenario: str) -> None:
    result, calls, evidence = run_build(tmp_path, scenario)
    assert result.returncode != 0
    assert updates(calls) == ["1", "0"]
    assert not list(evidence.glob("*/image-reference.json"))
    assert list(evidence.glob("*/build-error.json"))
    canceled = [call for call in calls if call[:3] == ["acr", "task", "cancel-run"]]
    assert len(canceled) == (1 if scenario in {"scale-failure", "scale-rollback"} else 0)


def test_cloud_build_does_not_scale_away_new_shared_work(tmp_path: Path) -> None:
    result, calls, evidence = run_build(tmp_path, "cleanup-busy")
    assert result.returncode != 0
    assert updates(calls) == ["1"]
    assert "Operator must review billable capacity" in result.stderr
    assert list(evidence.glob("*/image-reference.json"))


def test_shared_build_uses_no_private_pool_operations(tmp_path: Path) -> None:
    result, calls, evidence = run_build(tmp_path, "shared-success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[:2] == ["acr", "agentpool"] for call in calls)
    reference = json.loads(next(evidence.glob("*/image-reference.json")).read_text())
    assert reference["agentPool"] == "shared"
    assert reference["sqlCliHelpValidatedDuringBuild"] is True


def test_shared_build_does_not_change_restricted_network_settings(tmp_path: Path) -> None:
    result, calls, evidence = run_build(tmp_path, "shared-blocked")
    assert result.returncode != 0
    assert "does not change network rules" in result.stderr
    assert not any(any("/scheduleRun?" in arg for arg in call) for call in calls)
    assert updates(calls) == []
    assert not list(evidence.glob("*/image-reference.json"))
