# Runs one build stage on a lab VM. The GitHub Actions workflow sends this file through Azure Run
# Command (as SYSTEM). It downloads the repository at the exact commit being deployed, then runs
# lab\<Role>\<Stage>.ps1 from it, so the VM only ever runs reviewed code.
#
# Parameters arrive as strings from Run Command. Nothing secret is passed: stages read secrets
# from Key Vault with the VM's managed identity. Run Command returns only the last 4 KB of output,
# so the full log stays on the VM and the last line says how the stage went.
param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Ref,
    [Parameter(Mandatory = $true)][ValidatePattern('^\d\d-[A-Za-z-]+$')][string]$Stage,
    [ValidateSet('dc', 'ws')][string]$Role = 'dc',
    [string]$KeyVault = '',
    [string]$UpnSuffix = '',
    [string]$IdentityClientId = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($Ref -notmatch '^[0-9a-f]{40}$') { throw "Ref must be a full commit SHA, not '$Ref'." }
$root = 'C:\ProgramData\ServiceDeskLab\src'
$target = Join-Path $root $Ref
if (-not (Test-Path (Join-Path $target 'lab'))) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $zip = Join-Path $env:TEMP "sdlab-$Ref.zip"
    $unpack = Join-Path $env:TEMP "sdlab-$Ref"
    Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$Repository/zip/$Ref" -OutFile $zip
    if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $unpack -Force
    $inner = Get-ChildItem $unpack -Directory | Select-Object -First 1
    Move-Item -Path $inner.FullName -Destination $target
    Remove-Item $zip, $unpack -Recurse -Force -ErrorAction SilentlyContinue
    # Keep the last three downloads only.
    Get-ChildItem $root -Directory | Sort-Object CreationTime -Descending | Select-Object -Skip 3 |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

if ($IdentityClientId) { $env:SDLAB_IDENTITY_CLIENT_ID = $IdentityClientId }

$logs = 'C:\ProgramData\ServiceDeskLab\logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$transcript = Join-Path $logs ('stage-{0}-{1:yyyyMMdd-HHmmss}.log' -f $Stage, (Get-Date))
Start-Transcript -Path $transcript | Out-Null
try {
    Write-Output "Running stage $Stage from $Repository@$($Ref.Substring(0, 7)) on $env:COMPUTERNAME"
    # Each stage declares only the parameters it uses.
    $stageScript = Join-Path $target "lab\$Role\$Stage.ps1"
    $accepted = (Get-Command $stageScript).Parameters.Keys
    $arguments = @{}
    if ($accepted -contains 'KeyVault') { $arguments.KeyVault = $KeyVault }
    if ($accepted -contains 'UpnSuffix') { $arguments.UpnSuffix = $UpnSuffix }
    & $stageScript @arguments
    $result = 'OK'
}
catch {
    $where = $_.InvocationInfo
    $file = if ($where.ScriptName) { Split-Path -Leaf $where.ScriptName } else { 'script' }
    $result = 'FAILED: {0} ({1}:{2})' -f $_.Exception.Message, $file, $where.ScriptLineNumber
}
finally {
    Stop-Transcript | Out-Null
}
Write-Output "Full log on $env:COMPUTERNAME`: $transcript"
Write-Output "STAGE RESULT: $result"
