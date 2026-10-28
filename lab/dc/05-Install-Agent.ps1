# Stage 5: installs the ServiceDeskLab module and the fulfilment agent's scheduled tasks.
#
# Three tasks run as the group managed service account gmsa-sdagent$ (its password is managed by
# AD; nobody knows it and it rotates itself):
#   ServiceDeskLab Fulfilment   every 2 minutes: approved ServiceNow requests into AD
#   ServiceDeskLab User Sync    every 15 minutes: AD users into ServiceNow's user list
#   ServiceDeskLab Clean-up     daily at 02:00: deletes leavers past the 30-day retention
# Until ServiceNow is connected (its secrets are in Key Vault), the tasks exit straight away.
param(
    [Parameter(Mandatory = $true)][string]$KeyVault,
    [string]$UpnSuffix
)
. (Join-Path $PSScriptRoot 'Common.ps1')
Import-Module ActiveDirectory

Write-Step 'Installing the module'
$version = (Import-PowerShellDataFile (Join-Path $script:RepoRoot 'powershell\ServiceDeskLab\ServiceDeskLab.psd1')).ModuleVersion
$moduleRoot = 'C:\Program Files\WindowsPowerShell\Modules\ServiceDeskLab'
$target = Join-Path $moduleRoot $version
if (Test-Path $moduleRoot) { Remove-Item $moduleRoot -Recurse -Force }
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -Path (Join-Path $script:RepoRoot 'powershell\ServiceDeskLab\*') -Destination $target -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $target 'config') -Force | Out-Null
Copy-Item -Path (Join-Path $script:RepoRoot 'lab\config\*') -Destination (Join-Path $target 'config') -Force

Write-Step 'Agent settings, logs and event source'
$data = 'C:\ProgramData\ServiceDeskLab'
$logs = Join-Path $data 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$settings = [ordered]@{
    keyVault  = $KeyVault
    upnSuffix = $UpnSuffix
    homeShare = "\\$env:COMPUTERNAME\Home$"
    logDir    = $logs
}
$settings | ConvertTo-Json | Set-Content -Path (Join-Path $data 'agent.json') -Encoding UTF8
Copy-Item -Path (Join-Path $PSScriptRoot 'Start-LabAgent.ps1') -Destination (Join-Path $data 'Start-LabAgent.ps1') -Force
if (-not [Diagnostics.EventLog]::SourceExists('ServiceDeskLab')) {
    New-EventLog -LogName Application -Source ServiceDeskLab
}

# The agent account may write logs and read its settings; nothing else on disk.
$gmsa = "$((Get-ADDomain).NetBIOSName)\gmsa-sdagent$"
$acl = Get-Acl $logs
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($gmsa, 'Modify', 'ContainerInherit, ObjectInherit', 'None', 'Allow')))
Set-Acl -Path $logs -AclObject $acl
# The home drive root, so the agent can create a starter's H: folder.
$homeRoot = (Get-Content (Join-Path $script:RepoRoot 'lab\config\org.json') -Raw | ConvertFrom-Json).fileServer.homeRoot
if (Test-Path $homeRoot) {
    $acl = Get-Acl $homeRoot
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($gmsa, 'Modify', 'ContainerInherit, ObjectInherit', 'None', 'Allow')))
    Set-Acl -Path $homeRoot -AclObject $acl
}

Write-Step 'Group managed service account'
Install-ADServiceAccount -Identity 'gmsa-sdagent'
if (-not (Test-ADServiceAccount -Identity 'gmsa-sdagent')) { throw 'This server cannot use gmsa-sdagent.' }

Write-Step 'Log on as a batch job, for the agent group, on domain controllers'
# Scheduled tasks need this right. On domain controllers it comes from the Default Domain
# Controllers Policy, so the agent group is added there (after the defaults: Administrators,
# Backup Operators, Performance Log Users) and the policy's version is raised so it applies.
$dns = (Get-ADDomain).DNSRoot
$ddcpGuid = '{6AC1786C-016F-11D2-945F-00C04fB984F9}'
$inf = "\\$dns\SYSVOL\$dns\Policies\$ddcpGuid\MACHINE\Microsoft\Windows NT\SecEdit\GptTmpl.inf"
$sid = (Get-ADGroup (Get-Content (Join-Path $script:RepoRoot 'lab\config\org.json') -Raw | ConvertFrom-Json).groups.fulfilmentAgents).SID.Value
$lines = [Collections.Generic.List[string]](Get-Content -Path $inf -Encoding Unicode)
$index = $lines.FindIndex([Predicate[string]] { param($l) $l -match '^SeBatchLogonRight\s*=' })
$changed = $false
if ($index -ge 0 -and $lines[$index] -notmatch [regex]::Escape($sid)) {
    $lines[$index] = $lines[$index] + ",*$sid"
    $changed = $true
}
elseif ($index -lt 0) {
    $section = $lines.IndexOf('[Privilege Rights]')
    if ($section -lt 0) { $lines.Add('[Privilege Rights]'); $section = $lines.Count - 1 }
    $lines.Insert($section + 1, "SeBatchLogonRight = *S-1-5-32-544,*S-1-5-32-551,*S-1-5-32-559,*$sid")
    $changed = $true
}
if ($changed) {
    Set-Content -Path $inf -Value $lines -Encoding Unicode
    $gpoDn = "CN=$ddcpGuid,CN=Policies,CN=System,$((Get-ADDomain).DistinguishedName)"
    $version = [int](Get-ADObject $gpoDn -Properties versionNumber).versionNumber + 1
    Set-ADObject $gpoDn -Replace @{ versionNumber = $version }
    $gptIni = "\\$dns\SYSVOL\$dns\Policies\$ddcpGuid\GPT.INI"
    (Get-Content $gptIni) -replace '^Version=\d+', "Version=$version" | Set-Content $gptIni -Encoding ASCII
    & gpupdate.exe /target:computer /force | Out-Null
}

Write-Step 'Scheduled tasks'
$principal = New-ScheduledTaskPrincipal -UserId $gmsa -LogonType Password -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -StartWhenAvailable
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$jobs = @(
    @{ Name = 'ServiceDeskLab Fulfilment'; Job = 'Fulfilment'; Trigger = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 2)) }
    @{ Name = 'ServiceDeskLab User Sync'; Job = 'Sync'; Trigger = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)) }
    @{ Name = 'ServiceDeskLab Clean-up'; Job = 'Cleanup'; Trigger = (New-ScheduledTaskTrigger -Daily -At '02:00') }
)
foreach ($job in $jobs) {
    $action = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$data\Start-LabAgent.ps1`" -Job $($job.Job)"
    Register-ScheduledTask -TaskName $job.Name -TaskPath '\ServiceDeskLab\' -Action $action -Trigger $job.Trigger `
        -Principal $principal -Settings $taskSettings -Force | Out-Null
}

$configured = [bool](Get-VaultSecret -VaultName $KeyVault -Name 'servicenow-instance')
Write-Step "Agent installed (module $version). ServiceNow connected: $configured."
