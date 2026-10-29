# Status of the lab's Windows 11 PC: Microsoft Entra join, Intune enrolment, BitLocker, Windows LAPS
# and the policies Intune has applied. Read-only, for the lab workflow's deploy and doctor actions.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Write-Section([string]$Title) { "`n== $Title" }

Write-Section 'Windows'
$os = Get-CimInstance Win32_OperatingSystem
$release = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').DisplayVersion
'{0} {1} (build {2}), time zone {3}' -f $os.Caption, $release, $os.BuildNumber, (Get-TimeZone).Id

Write-Section 'Microsoft Entra ID'
$status = @(& dsregcmd.exe /status)
foreach ($key in 'AzureAdJoined', 'DomainJoined', 'DeviceId', 'TenantName', 'MdmUrl') {
    $line = $status | Where-Object { $_ -match "^\s*$key\s*:" } | Select-Object -First 1
    if ($line) { $line.Trim() }
}

Write-Section 'Intune'
$enrolments = @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Enrollments' -ErrorAction SilentlyContinue |
        Get-ItemProperty | Where-Object { $_.ProviderID -eq 'MS DM Server' })
if ($enrolments.Count) {
    foreach ($enrolment in $enrolments) { 'Enrolled in Intune (enrolment type {0}, user {1})' -f $enrolment.EnrollmentType, $enrolment.UPN }
    $sync = Get-ScheduledTask -TaskPath '\Microsoft\Windows\EnterpriseMgmt\*' -ErrorAction SilentlyContinue |
        Get-ScheduledTaskInfo | Sort-Object LastRunTime -Descending | Select-Object -First 1
    if ($sync) { 'Last check-in: {0:yyyy-MM-dd HH:mm}' -f $sync.LastRunTime }
}
else {
    'Not enrolled.'
}
$areas = @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device' -ErrorAction SilentlyContinue).PSChildName
'Policy areas set by MDM: {0}' -f $(if ($areas.Count) { $areas -join ', ' } else { 'none yet' })

Write-Section 'BitLocker'
$volume = Get-BitLockerVolume -MountPoint 'C:' -ErrorAction SilentlyContinue
if ($volume) {
    'C: {0}, protection {1}, {2}% encrypted' -f $volume.VolumeStatus, $volume.ProtectionStatus, $volume.EncryptionPercentage
}
else {
    'BitLocker status not available.'
}
# Until C: is encrypted: what silent encryption needs (a ready TPM, the Windows Recovery
# Environment), what BitLocker last logged, and any BitLocker setting the PC refused.
if (-not $volume -or $volume.VolumeStatus -ne 'FullyEncrypted') {
    $tpm = Get-Tpm -ErrorAction SilentlyContinue
    if ($tpm) { 'TPM: present {0}, ready {1}' -f $tpm.TpmPresent, $tpm.TpmReady }
    $winre = @(& reagentc.exe /info 2>&1) | Where-Object { "$_" -match 'Windows RE status' } | Select-Object -First 1
    if ($winre) { "$winre".Trim() }
    $received = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\BitLocker' -ErrorAction SilentlyContinue
    if ($received) {
        # Run Command keeps only the last 4 KB of output, so long values are cut short.
        $values = foreach ($property in $received.PSObject.Properties) {
            if ($property.Name -like 'PS*' -or $property.Name -like '*_ProviderSet' -or $property.Name -like '*_WinningProvider') { continue }
            $value = "$($property.Value)" -replace '\s+', ' '
            '{0}={1}' -f $property.Name, $value.Substring(0, [Math]::Min(60, $value.Length))
        }
        'BitLocker settings from Intune: {0}' -f ($values -join '; ')
    }
    $tasks = Get-ScheduledTask -TaskPath '\Microsoft\Windows\BitLocker\' -ErrorAction SilentlyContinue | Get-ScheduledTaskInfo
    foreach ($task in $tasks) { '{0}: last run {1:yyyy-MM-dd HH:mm}, result 0x{2:x}' -f $task.TaskName, $task.LastRunTime, $task.LastTaskResult }
    # Event 881, a note about the boot manager's signature logged at every check, is left out.
    $logged = @(Get-WinEvent -LogName 'Microsoft-Windows-BitLocker/BitLocker Management' -FilterXPath '*[System[EventID!=881]]' -MaxEvents 6 -ErrorAction SilentlyContinue)
    if (-not $logged.Count) { 'BitLocker log: nothing apart from event 881.' }
    foreach ($entry in $logged) {
        '{0:yyyy-MM-dd HH:mm} {1} {2}: {3}' -f $entry.TimeCreated, $entry.LevelDisplayName, $entry.Id, ($entry.Message -split "`n")[0].Trim()
    }
    $refused = @(Get-WinEvent -LogName 'Microsoft-Windows-DeviceManagement-Enterprise-Diagnostics-Provider/Admin' -FilterXPath '*[System[Level=2]]' -MaxEvents 50 -ErrorAction SilentlyContinue |
            Where-Object { $_.Message -match 'CSP URI: \(([^)]*BitLocker[^)]*)\)' })
    foreach ($entry in $refused | Select-Object -First 4) {
        $null = $entry.Message -match 'CSP URI: \(([^)]*)\)'
        $uri = $Matches[1]
        $result = if ($entry.Message -match 'Result: \((.*)\)') { $Matches[1] } else { 'no result given' }
        '{0:yyyy-MM-dd HH:mm} setting refused: {1}: {2}' -f $entry.TimeCreated, $uri, $result
    }
}

Write-Section 'Windows LAPS'
$laps = Get-WinEvent -LogName 'Microsoft-Windows-LAPS/Operational' -MaxEvents 3 -ErrorAction SilentlyContinue
if ($laps) {
    foreach ($entry in $laps) { '{0:yyyy-MM-dd HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($entry.Message -split "`n")[0] }
}
else {
    'No LAPS activity yet (the policy arrives from Intune).'
}
