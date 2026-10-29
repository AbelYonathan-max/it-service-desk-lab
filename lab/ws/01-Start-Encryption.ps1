# Stage 1 for the PC: starts the silent BitLocker encryption that Intune's "KBC - BitLocker" policy
# asks for. The scheduled task "BitLocker MDM policy Refresh" is what applies the policy and
# encrypts the drive, and it should run when the PC syncs with Intune. On the lab PC it had still
# not run (0x41303, "has not run yet") hours after the policy arrived, leaving C: unencrypted with
# nothing in the BitLocker log. Microsoft's guide to troubleshooting Intune BitLocker policies says
# to run the task by hand, so this stage does that once the policy has arrived, then waits for
# encryption to start. Safe to run again: it does nothing once C: is encrypted or encrypting.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) { Write-Output ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Message) }

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
Write-Step ('Running "{0}" ({1}, last result 0x{2:x})' -f $task.TaskName, $task.State, ($task | Get-ScheduledTaskInfo).LastTaskResult)
Start-ScheduledTask -InputObject $task

$deadline = (Get-Date).AddMinutes(5)
do {
    Start-Sleep -Seconds 15
    $volume = Get-BitLockerVolume -MountPoint 'C:'
} until ($volume.VolumeStatus -ne 'FullyDecrypted' -or (Get-Date) -gt $deadline)
Write-Step ('Task result 0x{0:x}. C: {1}, {2}% encrypted, protection {3}, key protectors: {4}' -f `
        ($task | Get-ScheduledTaskInfo).LastTaskResult, $volume.VolumeStatus, $volume.EncryptionPercentage,
    $volume.ProtectionStatus, (@($volume.KeyProtector.KeyProtectorType) -join ', '))
# Event 881, a note about the boot manager's signature logged at every check, is left out.
$logged = Get-WinEvent -LogName 'Microsoft-Windows-BitLocker/BitLocker Management' -FilterXPath '*[System[EventID!=881]]' -MaxEvents 5 -ErrorAction SilentlyContinue
foreach ($entry in $logged) {
    Write-Step ('BitLocker log, {0:HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($entry.Message -split "`n")[0].Trim())
}
if ($volume.VolumeStatus -eq 'FullyDecrypted') {
    throw 'Encryption did not start within 5 minutes; see the BitLocker log lines above.'
}
