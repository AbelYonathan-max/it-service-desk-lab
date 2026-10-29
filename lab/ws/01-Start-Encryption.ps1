# Stage 1 for the PC: makes sure C: gets the BitLocker encryption that Intune's "KBC - BitLocker"
# policy asks for. The scheduled task "BitLocker MDM policy Refresh" applies the policy and
# encrypts the drive, and it should run when the PC syncs with Intune. On the lab PC it had still
# not run (0x41303, "has not run yet") hours after the policy arrived, and starting it from Run
# Command did not run it either, so C: stayed unencrypted with nothing in the BitLocker log.
# This stage starts the task, as Microsoft's guide to troubleshooting Intune BitLocker policies
# says. If the task still does not run, the stage turns BitLocker on itself with the policy's
# settings: XTS-AES 256 and a TPM protector, with a recovery password that is saved in Entra ID
# before encryption starts. The recovery password is never printed; this output goes to public
# workflow logs. Safe to run again: it does nothing once C: is encrypted or encrypting.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) { Write-Output ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Message) }

function Wait-Encryption([int]$Minutes) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    do {
        Start-Sleep -Seconds 15
        $current = Get-BitLockerVolume -MountPoint 'C:'
    } until ($current.VolumeStatus -ne 'FullyDecrypted' -or (Get-Date) -gt $deadline)
    $current
}

$volume = Get-BitLockerVolume -MountPoint 'C:'
if ($volume.VolumeStatus -ne 'FullyDecrypted') {
    Write-Step ('C: is {0} ({1}% encrypted); nothing to do.' -f $volume.VolumeStatus, $volume.EncryptionPercentage)
    return
}
$policy = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\BitLocker' -ErrorAction SilentlyContinue
if (-not $policy -or $policy.RequireDeviceEncryption -ne 1) {
    Write-Step 'The BitLocker policy from Intune has not arrived yet. Run this stage again after the PC has synced with Intune.'
    return
}

$task = Get-ScheduledTask -TaskPath '\Microsoft\Windows\BitLocker\' -TaskName 'BitLocker MDM policy Refresh'
$action = $task.Actions | Select-Object -First 1
Write-Step ('Task "{0}": {1}, runs as {2}{3} (logon type {4}), triggers: {5}, action: {6}{7} {8}' -f $task.TaskName, $task.State,
    $task.Principal.UserId, $task.Principal.GroupId, $task.Principal.LogonType,
    ((@($task.Triggers) | ForEach-Object { $_.CimClass.CimClassName -replace '^MSFT_Task' }) -join ', '),
    $action.Execute, $action.ClassId, $action.Arguments)
Start-ScheduledTask -InputObject $task
$volume = Wait-Encryption -Minutes 2

if ($volume.VolumeStatus -eq 'FullyDecrypted') {
    Write-Step ('The task did not start encryption (last result 0x{0:x}). Turning BitLocker on with the policy''s settings.' -f ($task | Get-ScheduledTaskInfo).LastTaskResult)
    # Warnings are silenced because Add-BitLockerKeyProtector prints the new recovery password in one.
    if (-not @($volume.KeyProtector | Where-Object KeyProtectorType -EQ 'RecoveryPassword').Count) {
        $null = Add-BitLockerKeyProtector -MountPoint 'C:' -RecoveryPasswordProtector -WarningAction SilentlyContinue 3>$null
    }
    $recovery = (Get-BitLockerVolume -MountPoint 'C:').KeyProtector | Where-Object KeyProtectorType -EQ 'RecoveryPassword' | Select-Object -First 1
    $null = BackupToAAD-BitLockerKeyProtector -MountPoint 'C:' -KeyProtectorId $recovery.KeyProtectorId -WarningAction SilentlyContinue 3>$null
    Write-Step 'Recovery password saved in Entra ID.'
    $null = Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 -TpmProtector -UsedSpaceOnly -SkipHardwareTest -WarningAction SilentlyContinue 3>$null
    $volume = Wait-Encryption -Minutes 2
}

Write-Step ('C: {0}, {1}% encrypted, protection {2}, method {3}, key protectors: {4}' -f $volume.VolumeStatus,
    $volume.EncryptionPercentage, $volume.ProtectionStatus, $volume.EncryptionMethod, (@($volume.KeyProtector.KeyProtectorType) -join ', '))
# Event 881, a note about the boot manager's signature logged at every check, is left out.
$logged = Get-WinEvent -LogName 'Microsoft-Windows-BitLocker/BitLocker Management' -FilterXPath '*[System[EventID!=881]]' -MaxEvents 5 -ErrorAction SilentlyContinue
foreach ($entry in $logged) {
    Write-Step ('BitLocker log, {0:HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($entry.Message -split "`n")[0].Trim())
}
if ($volume.VolumeStatus -eq 'FullyDecrypted') {
    throw 'Encryption did not start; see the lines above.'
}
