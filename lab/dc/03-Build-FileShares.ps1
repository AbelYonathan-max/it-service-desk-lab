# Stage 3: department shared drives and home drives on F:.
#
# - One folder and SMB share per department share in org.json. Inheritance is off; access is
#   Administrators and SYSTEM (full), the share's DL-FS-*-RW group (modify) and DL-FS-*-RO
#   group (read). Nobody is ever given access by name on a folder.
# - Access-based enumeration: people only see folders they can open.
# - Home$ (hidden) holds one folder per person (the H: drive), each readable only by its owner.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
    Justification = 'Helpers inside a build stage; the stage is the unit of change and is safe to run again.')]
param()
. (Join-Path $PSScriptRoot 'Common.ps1')
Import-Module ActiveDirectory
$env:SDLAB_HOME_SHARE = "\\$env:COMPUTERNAME\Home$"
Import-LabModule
$config = Get-LabConfig -Force
$netbios = $config.NetbiosName

function New-Acl([string[]]$FullControl, [hashtable]$Groups) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    foreach ($principal in $FullControl) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($principal, 'FullControl', $inherit, 'None', 'Allow')))
    }
    foreach ($principal in $Groups.Keys) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($principal, $Groups[$principal], $inherit, 'None', 'Allow')))
    }
    $acl
}

Write-Step 'Department shared drives'
$admins = @('BUILTIN\Administrators', 'NT AUTHORITY\SYSTEM')
foreach ($share in $config.Shares) {
    if (-not (Test-Path $share.Path)) { New-Item -ItemType Directory -Path $share.Path -Force | Out-Null }
    $acl = New-Acl -FullControl $admins -Groups @{
        "$netbios\$($share.ModifyGroup)" = 'Modify'
        "$netbios\$($share.ReadGroup)"   = 'ReadAndExecute'
    }
    Set-Acl -Path $share.Path -AclObject $acl
    if (-not (Get-SmbShare -Name $share.Name -ErrorAction SilentlyContinue)) {
        New-SmbShare -Name $share.Name -Path $share.Path -Description $share.Description `
            -FullAccess 'BUILTIN\Administrators' -ChangeAccess 'NT AUTHORITY\Authenticated Users' `
            -FolderEnumerationMode AccessBased -CachingMode None | Out-Null
    }
    # A readme so the drive is not empty in screenshots.
    $readme = Join-Path $share.Path 'README.txt'
    if (-not (Test-Path $readme)) {
        Set-Content -Path $readme -Value "$($share.Name) shared drive. $($share.Description). Owner: employee $($share.OwnerEmployeeId). Request access through the IT service portal." -Encoding UTF8
    }
}

Write-Step 'Home drives'
$homeRoot = $config.FileServer.homeRoot
if (-not (Test-Path $homeRoot)) { New-Item -ItemType Directory -Path $homeRoot -Force | Out-Null }
# The root: admins full; everyone may only see it, not each other's folders.
$rootAcl = New-Object Security.AccessControl.DirectorySecurity
$rootAcl.SetAccessRuleProtection($true, $false)
foreach ($principal in $admins) {
    $rootAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($principal, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')))
}
$rootAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('NT AUTHORITY\Authenticated Users', 'ReadData, ReadAttributes, Synchronize', 'None', 'None', 'Allow')))
Set-Acl -Path $homeRoot -AclObject $rootAcl
if (-not (Get-SmbShare -Name $config.FileServer.homeShareName -ErrorAction SilentlyContinue)) {
    New-SmbShare -Name $config.FileServer.homeShareName -Path $homeRoot -Description 'Home drives (H:)' `
        -FullAccess 'BUILTIN\Administrators' -ChangeAccess 'NT AUTHORITY\Authenticated Users' `
        -FolderEnumerationMode AccessBased -CachingMode None | Out-Null
}
$made = 0
$module = Get-Module ServiceDeskLab
Get-ADUser -SearchBase $config.Ou.Staff -LDAPFilter '(employeeID=*)' | ForEach-Object {
    if (-not (Test-Path (Join-Path $homeRoot $_.SamAccountName))) { $made++ }
    # The same helper the joiner process uses; it is private to the module, so it runs inside it.
    $null = & $module { param($Name) Initialize-LabHomeFolder -SamAccountName $Name } $_.SamAccountName
}
Write-Step "Shares ready: $(@($config.Shares).Count) department drives, $made new home folder(s)."
