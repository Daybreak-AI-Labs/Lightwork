# Runs only the acceptance functions with synthetic processes/channel responses.
# Never invokes Windows Installer, a native executable, or the acceptance body.
param([Parameter(Mandatory=$true)][string]$ScriptDirectory)
$ErrorActionPreference = 'Stop'

function Import-Function([string]$File,[string]$Name) {
    $tokens=$null; $errors=$null
    $tree=[Management.Automation.Language.Parser]::ParseFile($File,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw "Acceptance script syntax errors: $errors" }
    $found=@($tree.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name},$true))
    if ($found.Count -ne 1) { throw "Expected one acceptance function: $Name" }
    # Dot sourcing into the caller scope is unnecessary: export only the reviewed
    # function's body, never the script's installation entry point.
    Invoke-Expression ($found[0].Extent.Text -replace '^function\s+', 'function script:')
}

Import-Function (Join-Path $ScriptDirectory 'test-customer.ps1') 'Invoke-Msi'
$script:fixtureExit=1603
$script:fixtureLog=''
$script:called=0
function Start-Process {
    param($FilePath,$ArgumentList,[switch]$PassThru,$WindowStyle)
    $script:called++
    if ($FilePath -notlike '*msiexec.exe') { throw 'Unexpected mocked executable.' }
    $index=[Array]::IndexOf($ArgumentList,'/L*v')
    [IO.File]::WriteAllText($ArgumentList[$index+1].Trim('"'),$script:fixtureLog)
    $process=[pscustomobject]@{ExitCode=$script:fixtureExit}
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {param($timeout) return $true}
    return $process
}
$originalTemp=$env:RUNNER_TEMP
$originalSystemRoot=$env:SystemRoot
if (-not $env:SystemRoot) { $env:SystemRoot=[IO.Path]::GetTempPath() }
$env:RUNNER_TEMP=Join-Path ([IO.Path]::GetTempPath()) ('lw-acceptance-guards-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $env:RUNNER_TEMP | Out-Null
try {
    [xml]$fragment=Get-Content -Raw -LiteralPath (Join-Path $ScriptDirectory 'acceptance-failure.wxs')
    $fault=$fragment.SelectSingleNode('//*[local-name()="CustomAction"]')
    $errorMessage=$fault.GetAttribute('Error')
    $action=$fault.GetAttribute('Id')
    $completed="Action ended 12:34:56: $action. Return value 3."
    $valid="MSI (s) (00:00) [12:34:56:000]: Product: Lightwork -- $errorMessage`r`n$completed`r`n"
    foreach ($case in @(
        @{code=1618;log=$valid},
        @{code=0;log=$valid},
        @{code=3010;log=$valid},
        @{code=1603;log='Windows Installer failed before InstallFiles.'},
        @{code=1603;log=$errorMessage},
        @{code=1603;log=$completed},
        @{code=1603;log="$errorMessage`nAction ended 12:34:56: UnrelatedAction. Return value 3."}
    )) {
        $script:fixtureExit=$case.code; $script:fixtureLog=$case.log
        $rejected=$false
        try { Invoke-Msi 'fixture.msi' '/i' -ExpectFailure | Out-Null } catch { $rejected=$true }
        if (-not $rejected) { throw "Accepted an unproven injected failure: $($case.code), $($case.log)" }
    }
    $script:fixtureExit=1603; $script:fixtureLog=$valid
    Invoke-Msi 'fixture.msi' '/i' -ExpectFailure | Out-Null
    if ($script:called -ne 8) { throw 'MSI guard checks did not exercise the real acceptance function.' }

    Import-Function (Join-Path $ScriptDirectory 'test-completed-rollback.ps1') 'Assert-InstalledChannel'
    function Read-InstalledChannel { return $script:fixtureChannel }
    $expectedPublicKey='fixture-public-key'
    $script:fixtureChannel=@{version='1.2.3';public_key=$expectedPublicKey;packaged_runtime=$true}
    if ((Assert-InstalledChannel '1.2.3') -ne '1.2.3') { throw 'Exact installed version was not returned.' }
    foreach ($channel in @(
        @{version='1.2.3';public_key=$expectedPublicKey;packaged_runtime=$true},
        @{version='1.2.4';public_key='different-key';packaged_runtime=$true},
        @{version='1.2.4';public_key=$expectedPublicKey;packaged_runtime=$false},
        @{public_key=$expectedPublicKey;packaged_runtime=$true}
    )) {
        $script:fixtureChannel=$channel; $rejected=$false
        try { Assert-InstalledChannel '1.2.4' | Out-Null } catch { $rejected=$true }
        if (-not $rejected) { throw 'Accepted an incorrect installed native version/channel.' }
    }
    Write-Output 'Acceptance guards: 8 MSI failure cases and 5 native channel cases passed.'
} finally {
    # Only flat log files written by the mocked process above.
    Get-ChildItem -LiteralPath $env:RUNNER_TEMP -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
    Remove-Item -LiteralPath $env:RUNNER_TEMP
    $env:RUNNER_TEMP=$originalTemp
    $env:SystemRoot=$originalSystemRoot
}
