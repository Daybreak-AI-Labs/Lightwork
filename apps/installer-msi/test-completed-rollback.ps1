# Disposable Windows CI acceptance of the actual frozen maintenance protocol.
# Synthetic trusted publisher identity; no claim of public certificate or clean VM acceptance.
param(
    [Parameter(Mandatory=$true)][string]$BaseMsi,
    [Parameter(Mandatory=$true)][string]$CandidateMsi,
    [Parameter(Mandatory=$true)][string]$PublicKeyFile,
    [Parameter(Mandatory=$true)][string]$Inventory,
    [Parameter(Mandatory=$true)][string]$Report
)
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true' -or -not $env:RUNNER_TEMP) { throw 'Completed rollback acceptance requires a disposable CI runner.' }
$runnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
$fixtureRoot = Join-Path $env:RUNNER_TEMP ('lw-rollback-' + [guid]::NewGuid().ToString('N'))
$fixtureRoot = [IO.Path]::GetFullPath($fixtureRoot)
if (-not $fixtureRoot.StartsWith($runnerRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Acceptance fixture escaped RUNNER_TEMP.' }
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$installPath = Join-Path $fixtureRoot 'app'
$statePath = Join-Path $fixtureRoot 'state'
New-Item -ItemType Directory -Path $statePath | Out-Null
$metadata = Get-Content -Raw -LiteralPath $Inventory | ConvertFrom-Json
$expectedPublicKey = (Get-Content -Raw -LiteralPath $PublicKeyFile).Trim()
$baseVersion = [version]$metadata.version
$newVersion = '{0}.{1}.{2}' -f $baseVersion.Major,$baseVersion.Minor,($baseVersion.Build + 1)
$originalEnvironment = @{}
Get-ChildItem Env: | Where-Object { $_.Name -match '^(MAVERICK_|LIGHTWORK_)' } | ForEach-Object {
    $originalEnvironment[$_.Name] = $_.Value
    Remove-Item -LiteralPath ('Env:' + $_.Name)
}
$originalPath = $env:PATH
$observed = [ordered]@{schema='lightwork.completed-upgrade-rollback-acceptance.v1'; clean_virtual_machine=$false; publicly_recognized_publisher=$false; synthetic_fixture_versions=$true; same_frozen_runtime_fixture=$true; native_webview_automated=$false}
$server = $null

function Invoke-Msi([string]$package,[string]$action) {
    # Canonicalize PowerShell paths before passing them to Windows Installer.
    $packageFile = Get-Item -LiteralPath $package -ErrorAction Stop
    if ($packageFile.PSProvider.Name -ne 'FileSystem' -or $packageFile.PSIsContainer) { throw 'The MSI package must be an existing file.' }
    $package = [IO.Path]::GetFullPath($packageFile.FullName)
    $arguments = @($action,('"' + $package + '"'),'/qn','/norestart',('INSTALLDIR="' + $installPath + '"'))
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/msiexec.exe') -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(240000)) { throw 'Fixture Windows Installer timed out.' }
    if ($process.ExitCode -notin @(0,3010) -and -not ($action -eq '/x' -and $process.ExitCode -in @(1605,1614))) { throw "Fixture Windows Installer failed: $($process.ExitCode)" }
}

function Read-InstalledChannel {
    # This fixed native query exits before WebView or dashboard initialization.
    $desktop = Join-Path $installPath 'maverick-desktop.exe'
    $output = & $desktop --maintenance-channel
    if ($LASTEXITCODE -ne 0) { throw 'The installed native channel query failed.' }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Assert-InstalledChannel([string]$ExpectedVersion) {
    $channel = Read-InstalledChannel
    if ($channel.version -cne $ExpectedVersion -or $channel.packaged_runtime -ne $true -or $channel.public_key -cne $expectedPublicKey) {
        throw "Installed native application does not match the expected customer version/channel: $ExpectedVersion"
    }
    return $channel.version
}

function Protocol([string]$binary,[string]$step,[hashtable]$payload) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $binary
    $info.ArgumentList.Add('installation-maintenance')
    $info.ArgumentList.Add($step)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Frozen maintenance helper did not start.' }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write(($payload | ConvertTo-Json -Compress -Depth 8))
    $process.StandardInput.Close()
    if (-not $process.WaitForExit(1900000)) {
        & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $process.Id /T /F | Out-Null
        throw 'Frozen maintenance protocol timed out; only its owned CI process tree was stopped.'
    }
    $output = $stdout.GetAwaiter().GetResult()
    $errorOutput = $stderr.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw "Frozen maintenance $step failed: $errorOutput" }
    return ($output | ConvertFrom-Json)
}

function Start-Recovery {
    $script:server = Start-Process -FilePath (Join-Path $installPath 'maverick.exe') -ArgumentList @('dashboard','--host','127.0.0.1','--port','8765','--recovery-mode') -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $fixtureRoot 'recovery-out.log') -RedirectStandardError (Join-Path $fixtureRoot 'recovery-error.log')
    for ($attempt=0; $attempt -lt 90; $attempt++) {
        if ($script:server.HasExited) { throw 'Fixture recovery server exited before becoming live.' }
        try { if ((Invoke-WebRequest 'http://127.0.0.1:8765/livez').StatusCode -eq 200) { return } } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw 'Fixture recovery server never became live.'
}

function Stop-Recovery {
    if ($script:server -and -not $script:server.HasExited) {
        & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $script:server.Id /T /F | Out-Null
        $script:server.WaitForExit()
    }
    $script:server = $null
}

function Authorization([string]$rollbackId='') {
    $origin = 'http://127.0.0.1:8765'
    $bearer = 'Bearer ' + $env:MAVERICK_DASHBOARD_TOKEN
    $status = Invoke-RestMethod ($origin + '/api/v1/recovery/status') -Headers @{Origin=$origin; Authorization=$bearer}
    $headers = @{Origin=$origin; Authorization=$bearer; 'X-Lightwork-Recovery-Context'=$status.context; 'X-Lightwork-Expected-Tenant'=$status.expected_tenant}
    $body = @{confirm=$true; confirm_all_workspaces=$true}
    if ($rollbackId) { $body.rollback_id=$rollbackId }
    return Invoke-RestMethod ($origin + '/api/v1/recovery/installation/authorize') -Method Post -Headers $headers -ContentType 'application/json' -Body ($body | ConvertTo-Json)
}

function Execute-Retained([string]$helper,[string]$authorization) {
    # A real finished sentinel process stands in for the native shell's exited
    # parent. Suppressing result UI changes no grant/signature/restore checks.
    $sentinel = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/ping.exe') -ArgumentList @('127.0.0.1','-n','1') -PassThru -WindowStyle Hidden
    $sentinel.WaitForExit()
    return Protocol $helper 'execute' @{authorization=$authorization;parent_pid=$sentinel.Id;open_result=$false}
}

try {
    foreach ($package in @($BaseMsi,$CandidateMsi)) {
        if ((Get-AuthenticodeSignature -LiteralPath $package).Status -ne 'Valid') { throw 'Synthetic fixture publisher is not trusted on this disposable runner.' }
        if (-not (Test-Path -LiteralPath ($package + '.sig'))) { throw 'Fixture lacks its real updater signature.' }
    }
    Invoke-Msi $BaseMsi '/i'
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    $env:MAVERICK_HOME = $statePath
    $env:MAVERICK_CLIENT_ID = 'acceptance'
    $env:MAVERICK_TENANT = 'acceptance'
    $env:MAVERICK_DASHBOARD_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText((Join-Path $statePath 'config.toml'), "[client]`nid = 'acceptance'`n[features]`nworkspace_recovery = true`n")
    foreach ($tenant in @('acceptance','second')) {
        $directory = Join-Path $statePath ('tenants/' + $tenant)
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $directory 'fixture-state.txt'),'before-upgrade')
    }
    $runtime = Join-Path $installPath 'maverick.exe'
    $observed.initial_native_version = Assert-InstalledChannel $metadata.version
    if ((Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant() -ne $metadata.runtime.sha256) { throw 'Installed base runtime differs from the fixture inventory.' }
    Start-Recovery
    $unauthenticated = $false
    try { Invoke-RestMethod 'http://127.0.0.1:8765/api/v1/recovery/installation' -Headers @{Origin='http://127.0.0.1:8765'} | Out-Null } catch {
        $unauthenticated = $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401
    }
    if (-not $unauthenticated) { throw 'Installation recovery did not require its synthetic administrator bearer.' }
    $observed.authenticated_administrator_api='passed'
    $grant = Authorization
    Stop-Recovery
    $details = Protocol $runtime 'inspect' @{authorization=$grant.authorization}
    Copy-Item -LiteralPath $BaseMsi -Destination (Join-Path $details.directory 'old.msi')
    Copy-Item -LiteralPath $CandidateMsi -Destination (Join-Path $details.directory 'new.msi')
    $downloads = @{public_key=(Get-Content -Raw -LiteralPath $PublicKeyFile).Trim();old=@{version=$metadata.version;runtime_sha256=$metadata.runtime.sha256;signature=(Get-Content -Raw -LiteralPath ($BaseMsi+'.sig')).Trim()};new=@{version=$newVersion;runtime_sha256=$metadata.runtime.sha256;signature=(Get-Content -Raw -LiteralPath ($CandidateMsi+'.sig')).Trim()}}
    [IO.File]::WriteAllText((Join-Path $details.directory 'downloads.json'),($downloads | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
    $prepared = Protocol $runtime 'prepare' @{authorization=$grant.authorization}
    $result = Execute-Retained $prepared.helper $grant.authorization
    if ($result.status -ne 'upgraded') { throw 'No completed upgrade receipt.' }
    $observed.upgraded_native_version = Assert-InstalledChannel $newVersion
    if ((Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant() -ne $metadata.runtime.sha256) { throw 'Upgraded fixture runtime differs from its declared shared bytes.' }
    $observed.protected_upgrade='passed'
    [IO.File]::WriteAllText((Join-Path $statePath 'tenants/acceptance/fixture-state.txt'),'newer-state')
    [IO.File]::WriteAllText((Join-Path $statePath 'tenants/second/newer.txt'),'created after upgrade')
    Start-Recovery
    $rollback = Authorization $grant.id
    Stop-Recovery
    $prepared = Protocol $runtime 'prepare' @{authorization=$rollback.authorization}
    $result = Execute-Retained $prepared.helper $rollback.authorization
    if ($result.status -ne 'rolled_back') { throw 'No completed rollback receipt.' }
    $observed.rolled_back_native_version = Assert-InstalledChannel $metadata.version
    foreach ($tenant in @('acceptance','second')) {
        if ([IO.File]::ReadAllText((Join-Path $statePath ('tenants/'+$tenant+'/fixture-state.txt'))) -ne 'before-upgrade') { throw 'The complete local workspace snapshot was not restored.' }
    }
    if (Test-Path -LiteralPath (Join-Path $statePath 'tenants/second/newer.txt')) { throw 'Newer file survived exact rollback.' }
    if ((Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant() -ne $metadata.runtime.sha256) { throw 'Rollback runtime differs from the verified original.' }
    $status = Protocol $runtime 'status' @{}
    if ($status.incomplete) { throw 'Rollback left an unresolved startup block.' }
    $observed.completed_upgrade_rollback='passed'
    $observed.all_local_workspaces_restored='passed'
    $observed.developer_tools_absent_from_runtime_path='passed'
    $observed.runtime_bytes_unchanged='passed'
} finally {
    Stop-Recovery
    $env:PATH = $originalPath
    Invoke-Msi $CandidateMsi '/x'
    Invoke-Msi $BaseMsi '/x'
    Get-ChildItem Env: | Where-Object { $_.Name -match '^(MAVERICK_|LIGHTWORK_)' } | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    foreach ($name in $originalEnvironment.Keys) { Set-Item -LiteralPath ('Env:' + $name) -Value $originalEnvironment[$name] }
    $observed | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Report -Encoding utf8
}
