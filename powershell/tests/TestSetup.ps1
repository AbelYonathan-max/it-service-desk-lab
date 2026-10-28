# Shared set-up for every test file: AD stubs, the module, and the repository's org.json.
. (Join-Path $PSScriptRoot 'Stubs/ActiveDirectory.ps1')

$repoRoot = Split-Path (Split-Path $PSScriptRoot)
$env:SDLAB_SKIP_AD_IMPORT = '1'
$env:SDLAB_CONFIG = Join-Path (Join-Path (Join-Path $repoRoot 'lab') 'config') 'org.json'
$env:SDLAB_UPN_SUFFIX = 'kestrel.example'
$env:SDLAB_HOME_SHARE = ''
$env:SDLAB_KEYVAULT = ''
$env:SDLAB_LOG_DIR = Join-Path ([IO.Path]::GetTempPath()) ('sdlab-tests-' + [guid]::NewGuid().ToString('n'))

Import-Module (Join-Path (Join-Path (Split-Path $PSScriptRoot) 'ServiceDeskLab') 'ServiceDeskLab.psd1') -Force

function New-TestUser {
    # An object shaped like Get-ADUser output.
    param(
        [string]$Sam = 'anya.evans',
        [string]$Department = 'Adult Social Care',
        [string[]]$MemberOf = @(),
        [bool]$Enabled = $true,
        [bool]$LockedOut = $false,
        [string]$Ou,
        [hashtable]$Extra = @{}
    )
    $config = Get-LabConfig
    if (-not $Ou) {
        $dept = @($config.Departments | Where-Object { $_.Name -eq $Department })[0]
        $Ou = if ($dept) { $dept.OuDn } else { $config.Ou.Staff }
    }
    $user = [ordered]@{
        SamAccountName    = $Sam
        UserPrincipalName = "$Sam@kestrel.example"
        DistinguishedName = "CN=$Sam,$Ou"
        Name              = $Sam
        Department        = $Department
        MemberOf          = @($MemberOf | ForEach-Object { "CN=$_,$($config.Ou.Groups)" })
        Enabled           = $Enabled
        LockedOut         = $LockedOut
        EmployeeID        = 'E10003'
    }
    foreach ($key in $Extra.Keys) { $user[$key] = $Extra[$key] }
    [pscustomobject]$user
}
