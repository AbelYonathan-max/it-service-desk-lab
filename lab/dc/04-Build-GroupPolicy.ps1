# Stage 4: Group Policy.
#
# - "KBC - Workstation Baseline" (computers, linked to the Workstations OU): sign-in banner,
#   screen lock after 15 minutes idle, AutoRun off, no writing to USB storage, SMB signing,
#   Windows Update installs automatically.
# - "KBC - User Baseline" (users, linked to the Staff OU): password-protected screen saver after
#   10 minutes, Edge home page set to the intranet.
# The lab's Windows 11 PC is cloud-native (Entra joined, managed by Intune), so these GPOs are
# also exported as XML for Intune's Group Policy analytics, which shows how each setting moves to
# an Intune policy. Reports are written to F:\GPOReports.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
    Justification = 'Helpers inside a build stage; the stage is the unit of change and is safe to run again.')]
param()
. (Join-Path $PSScriptRoot 'Common.ps1')
Import-Module ActiveDirectory
Import-Module GroupPolicy
Import-LabModule
$config = Get-LabConfig -Force

function Get-OrNewGpo([string]$Name, [string]$Comment) {
    $gpo = Get-GPO -Name $Name -ErrorAction SilentlyContinue
    if (-not $gpo) { $gpo = New-GPO -Name $Name -Comment $Comment }
    $gpo
}
function Set-Value([string]$Gpo, [string]$Key, [string]$Name, $Value, [string]$Type = 'DWord') {
    Set-GPRegistryValue -Name $Gpo -Key $Key -ValueName $Name -Value $Value -Type $Type | Out-Null
}
function Set-Link([string]$Gpo, [string]$Target) {
    $links = (Get-GPInheritance -Target $Target).GpoLinks.DisplayName
    if ($links -notcontains $Gpo) { New-GPLink -Name $Gpo -Target $Target -LinkEnabled Yes | Out-Null }
}

Write-Step 'KBC - Workstation Baseline'
$ws = 'KBC - Workstation Baseline'
$null = Get-OrNewGpo $ws 'Security and usability settings for council PCs.'
$system = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'
Set-Value $ws $system 'LegalNoticeCaption' 'Kestrel Borough Council' 'String'
Set-Value $ws $system 'LegalNoticeText' 'This system is for authorised council use only. Activity is monitored and logged. Report suspected misuse to the IT service desk.' 'String'
Set-Value $ws $system 'InactivityTimeoutSecs' 900
Set-Value $ws 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer' 'NoDriveTypeAutoRun' 255
Set-Value $ws 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer' 'NoAutorun' 1
Set-Value $ws 'HKLM\Software\Policies\Microsoft\Windows\RemovableStorageDevices\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}' 'Deny_Write' 1
Set-Value $ws 'HKLM\System\CurrentControlSet\Services\LanmanWorkstation\Parameters' 'RequireSecuritySignature' 1
Set-Value $ws 'HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoUpdate' 0
Set-Value $ws 'HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU' 'AUOptions' 4
Set-Value $ws 'HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU' 'ScheduledInstallDay' 0
Set-Value $ws 'HKLM\Software\Policies\Microsoft\Windows\WindowsUpdate\AU' 'ScheduledInstallTime' 12
Set-Link $ws $config.Ou.Workstations

Write-Step 'KBC - User Baseline'
$user = 'KBC - User Baseline'
$null = Get-OrNewGpo $user 'Desktop settings for council staff.'
$desktop = 'HKCU\Software\Policies\Microsoft\Windows\Control Panel\Desktop'
Set-Value $user $desktop 'ScreenSaveActive' '1' 'String'
Set-Value $user $desktop 'ScreenSaverIsSecure' '1' 'String'
Set-Value $user $desktop 'ScreenSaveTimeOut' '600' 'String'
Set-Value $user 'HKCU\Software\Policies\Microsoft\Edge' 'HomepageLocation' 'https://intranet.kestrel.example' 'String'
Set-Value $user 'HKCU\Software\Policies\Microsoft\Edge' 'RestoreOnStartup' 4
Set-Link $user $config.Ou.Staff

Write-Step 'Reports and backups'
$reports = 'F:\GPOReports'
$backups = 'F:\GPOBackups'
foreach ($folder in $reports, $backups) { if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null } }
foreach ($name in $ws, $user) {
    $safe = $name -replace '[^A-Za-z0-9-]', ''
    Get-GPOReport -Name $name -ReportType Xml -Path (Join-Path $reports "$safe.xml")
    Get-GPOReport -Name $name -ReportType Html -Path (Join-Path $reports "$safe.html")
    Backup-GPO -Name $name -Path $backups | Out-Null
}
Get-GPO -All | Sort-Object DisplayName | ForEach-Object { Write-Step "GPO: $($_.DisplayName) (modified $($_.ModificationTime))" }
