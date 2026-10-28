# Runs one build stage on the domain controller. The GitHub Actions workflow sends this file
# through Azure Run Command (as SYSTEM). It downloads the repository at the exact commit being
# deployed, then runs lab\dc\<Stage>.ps1 from it, so the server always runs reviewed code.
#
# Parameters arrive as strings from Run Command. Nothing secret is passed: stages read secrets
# from Key Vault with the VM's managed identity.
param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Ref,
    [Parameter(Mandatory = $true)][ValidatePattern('^\d\d-[A-Za-z-]+$')][string]$Stage,
    [Parameter(Mandatory = $true)][string]$KeyVault,
    [string]$UpnSuffix = '',
    [string]$IdentityClientId = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($Ref -notmatch '^[0-9a-f]{40}$') { throw "Ref must be a full commit SHA, not '$Ref'." }
$root = 'C:\ProgramData\ServiceDeskLab\src'
$target = Join-Path $root $Ref
if (-not (Test-Path (Join-Path $target 'lab\dc'))) {
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
Write-Output "Running stage $Stage from $Repository@$($Ref.Substring(0, 7)) on $env:COMPUTERNAME"
# Each stage declares only the parameters it uses.
$script = Join-Path $target "lab\dc\$Stage.ps1"
$accepted = (Get-Command $script).Parameters.Keys
$arguments = @{}
if ($accepted -contains 'KeyVault') { $arguments.KeyVault = $KeyVault }
if ($accepted -contains 'UpnSuffix') { $arguments.UpnSuffix = $UpnSuffix }
& $script @arguments
