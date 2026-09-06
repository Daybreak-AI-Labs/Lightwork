# Destructive ONLY to a fresh CI installation under RUNNER_TEMP. Does not use
# Python/Git after installation. This is CI acceptance, not a clean-VM claim.
param(
    [Parameter(Mandatory = $true)][string]$BaseMsi,
    [Parameter(Mandatory = $true)][string]$CandidateMsi,
    [Parameter(Mandatory = $true)][string]$Inventory,
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [Parameter(Mandatory = $true)][string]$Report
)
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true' -or -not $env:RUNNER_TEMP) { throw 'Run package acceptance only on a disposable CI runner.' }
$installPath = [IO.Path]::GetFullPath($InstallDirectory)
$runnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (-not $installPath.StartsWith($runnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $installPath)) {
    throw 'The acceptance installation must be a fresh directory strictly inside RUNNER_TEMP.'
}
foreach ($candidate in @($BaseMsi, $CandidateMsi, $installPath, $Report)) {
    if ($candidate -match '["\r\n]') { throw 'Unsupported installer argument.' }
}
$metadata = Get-Content -Raw -LiteralPath $Inventory | ConvertFrom-Json
$observed = [ordered]@{ schema = 'lightwork.windows-package-acceptance.v1'; clean_virtual_machine = $false }
$originalPath = $env:PATH
$originalHome = $env:MAVERICK_HOME
$originalNoIndex = $env:PIP_NO_INDEX
$homePath = Join-Path $env:RUNNER_TEMP ('lightwork-customer-state-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $homePath | Out-Null
$marker = Join-Path $homePath 'customer-data-preserved.txt'
[IO.File]::WriteAllText($marker, 'synthetic customer data must survive maintenance')

function Invoke-Msi([string]$Package, [string]$Action, [string[]]$Properties = @(), [switch]$ExpectFailure) {
    # MSI rejects mixed-separator paths that PowerShell accepts (1324/1619).
    # Resolve relative paths against the shell location before native launch.
    $packageFile = Get-Item -LiteralPath $Package -ErrorAction Stop
    if ($packageFile.PSProvider.Name -ne 'FileSystem' -or $packageFile.PSIsContainer) { throw 'The MSI package must be an existing file.' }
    $Package = [IO.Path]::GetFullPath($packageFile.FullName)
    $log = Join-Path $env:RUNNER_TEMP ('lightwork-msi-' + [guid]::NewGuid().ToString('N') + '.log')
    $arguments = @($Action, ('"' + $Package + '"'), '/qn', '/norestart', '/L*v', ('"' + $log + '"')) + $Properties
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/msiexec.exe') -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(240000)) { throw "Windows Installer timed out; log: $log" }
    if ($Action -eq '/x' -and $process.ExitCode -in @(1605, 1614)) { return }
    if ($ExpectFailure) {
        if ($process.ExitCode -ne 1603) { throw "Expected injected MSI failure 1603, got $($process.ExitCode); log: $log" }
        $details = Get-Content -Raw -LiteralPath $log
        $faultMessage = 'Injected upgrade failure for transactional rollback acceptance.'
        $faultAction = '(?m)^Action ended [^\r\n]*: LightworkAcceptanceFailure\. Return value 3\.\s*$'
        if (-not $details.Contains($faultMessage) -or $details -notmatch $faultAction) {
            throw "MSI failed without completing the injected failure action; log: $log"
        }
    } elseif ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        throw "Windows Installer failed ($($process.ExitCode)); log: $log"
    }
}

function Assert-Runtime {
    $runtime = Join-Path $installPath 'maverick.exe'
    $output = & $runtime version
    if ($LASTEXITCODE -ne 0) { throw 'The installed frozen runtime could not start.' }
    foreach ($package in $metadata.packages) {
        $pattern = '(?m)^\s*' + [regex]::Escape($package.distribution) + '\s+' + [regex]::Escape($metadata.cohort_version) + '\s*$'
        if (($output -join "`n") -notmatch $pattern) { throw "Installed runtime lacks $($package.distribution)." }
    }
    & $runtime release-runtime-check
    if ($LASTEXITCODE -ne 0) { throw 'Installed provider runtime is incomplete.' }
    if (-not (Test-Path -LiteralPath (Join-Path $installPath 'maverick-desktop.exe'))) { throw 'Desktop shell was not installed.' }
    if ((Get-Content -Raw -LiteralPath $marker) -ne 'synthetic customer data must survive maintenance') { throw 'Maintenance changed workspace data.' }
}

$acceptanceError = $null
try {
    Invoke-Msi $BaseMsi '/i' @('INSTALLDIR="' + $installPath + '"')
    # These are the only executable search locations used by the customer
    # smoke. No Python, Git, pip, Node, or repository directory is on PATH.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    $env:MAVERICK_HOME = $homePath
    $env:PIP_NO_INDEX = '1'
    Assert-Runtime
    $observed.install_without_developer_path = 'passed'
    $runtimePath = [IO.Path]::GetFullPath((Join-Path $installPath 'maverick.exe'))
    if (-not $runtimePath.StartsWith($installPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime repair target escaped installation.' }
    $initialHash = (Get-FileHash -LiteralPath $runtimePath -Algorithm SHA256).Hash
    Remove-Item -LiteralPath $runtimePath
    Invoke-Msi $BaseMsi '/fa'
    Assert-Runtime
    if ((Get-FileHash -LiteralPath $runtimePath -Algorithm SHA256).Hash -ne $initialHash) { throw 'Repair did not restore the verified runtime bytes.' }
    $observed.repair_missing_runtime = 'passed'
    Invoke-Msi $CandidateMsi '/i' @(('INSTALLDIR="' + $installPath + '"'), 'LIGHTWORK_ACCEPTANCE_FAIL=1') -ExpectFailure
    Assert-Runtime
    if ((Get-FileHash -LiteralPath $runtimePath -Algorithm SHA256).Hash -ne $initialHash) { throw 'Failed upgrade did not preserve the previous runtime.' }
    $observed.failed_upgrade_transaction_rollback = 'passed'
    Invoke-Msi $CandidateMsi '/i' @('INSTALLDIR="' + $installPath + '"')
    Assert-Runtime
    $observed.successful_upgrade = 'passed'
    $stdout = Join-Path $homePath 'dashboard-output.log'
    $stderr = Join-Path $homePath 'dashboard-error.log'
    $dashboard = Start-Process -FilePath $runtimePath -ArgumentList @('dashboard', '--host', '127.0.0.1', '--port', '8765') -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    try {
        $responding = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try { $page = Invoke-WebRequest 'http://127.0.0.1:8765/livez' -UseBasicParsing -TimeoutSec 3; $responding = $page.StatusCode -eq 200; if ($responding) { break } } catch { }
            if ($dashboard.HasExited) { throw "Packaged dashboard exited; inspect $stderr" }
            Start-Sleep -Seconds 1
        }
        if (-not $responding) { throw 'Packaged dashboard never became live.' }
        $page = Invoke-WebRequest 'http://127.0.0.1:8765/start' -UseBasicParsing -TimeoutSec 15
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'Lightwork') { throw 'Packaged setup page did not render.' }
        $observed.packaged_dashboard_setup = 'passed'
    } finally {
        if (-not $dashboard.HasExited) {
            & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $dashboard.Id /T /F | Out-Null
            if (-not $dashboard.WaitForExit(15000)) { throw 'The owned dashboard process did not stop.' }
        }
    }
    $recovery = Start-Process -FilePath $runtimePath -ArgumentList @('dashboard', '--host', '127.0.0.1', '--port', '8765', '--recovery-mode') -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $homePath 'recovery-output.log') -RedirectStandardError (Join-Path $homePath 'recovery-error.log')
    try {
        $responding = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try { $page = Invoke-WebRequest 'http://127.0.0.1:8765/livez' -UseBasicParsing -TimeoutSec 3; $responding = $page.StatusCode -eq 200; if ($responding) { break } } catch { }
            if ($recovery.HasExited) { throw 'Packaged recovery process exited before becoming live.' }
            Start-Sleep -Seconds 1
        }
        if (-not $responding) { throw 'Packaged recovery process never became live.' }
        $blocked = $false
        try { Invoke-WebRequest 'http://127.0.0.1:8765/start' -UseBasicParsing -TimeoutSec 15 | Out-Null } catch {
            $blocked = $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 503
        }
        if (-not $blocked) { throw 'Packaged recovery mode did not block ordinary application work.' }
        $observed.packaged_isolated_recovery = 'passed'
    } finally {
        if (-not $recovery.HasExited) {
            & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $recovery.Id /T /F | Out-Null
            if (-not $recovery.WaitForExit(15000)) { throw 'The owned recovery process did not stop.' }
        }
    }
} catch {
    $acceptanceError = $_
    throw
} finally {
    $env:PATH = $originalPath
    $env:MAVERICK_HOME = $originalHome
    $env:PIP_NO_INDEX = $originalNoIndex
    $cleanupErrors = @()
    try { $observed | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Report -Encoding utf8 } catch { $cleanupErrors += $_; Write-Warning 'Acceptance report could not be saved.' }
    foreach ($package in @($CandidateMsi, $BaseMsi)) {
        try { Invoke-Msi $package '/x' -ExpectFailure:$false } catch {
            $cleanupErrors += $_
            Write-Warning 'Fixture uninstall cleanup failed; installer log retained.'
        }
    }
    if (-not $acceptanceError -and $cleanupErrors.Count) { throw $cleanupErrors[0] }
}
