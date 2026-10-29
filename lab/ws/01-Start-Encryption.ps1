# Stage 1 for the PC: makes sure C: gets the BitLocker encryption that Intune's "KBC - BitLocker"
# policy asks for. The scheduled task "BitLocker MDM policy Refresh" applies the policy and
# encrypts the drive. It runs as INTERACTIVE, in the session of whoever is signed in, and nobody
# signs in to the lab PC: it had never run (0x41303, "has not run yet") hours after the policy
# arrived, and starting it from Run Command did not run it either, so C: stayed unencrypted with
# nothing in the BitLocker log. This stage starts the task, as Microsoft's guide to
# troubleshooting Intune BitLocker policies says. If it still does not run, the stage turns
# BitLocker on itself with the policy's settings: XTS-AES 256 and a TPM protector, with a
# recovery password that is saved in Entra ID before encryption starts. Windows had not taken
# ownership of the PC's virtual TPM either (Get-Tpm: ready, not owned), so BitLocker refused the
# TPM protector (0x80310018) until Initialize-Tpm had run. The recovery password is never
# printed; this output goes to public workflow logs. Safe to run again: once C: is encrypted or
# encrypting it only asks the PC to sync with Intune, so Intune's encryption report catches up.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) { Write-Output ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Message) }

function Sync-Intune {
    # PushLaunch is the task the Intune enrolment created to check in with Intune.
    $push = @(Get-ScheduledTask -TaskPath '\Microsoft\Windows\EnterpriseMgmt\*' -TaskName 'PushLaunch' -ErrorAction SilentlyContinue)
    foreach ($task in $push) { Start-ScheduledTask -InputObject $task }
    Write-Step $(if ($push.Count) { 'Asked the PC to sync with Intune.' } else { 'No Intune sync task found on the PC.' })
}

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
    Write-Step ('C: is {0} ({1}% encrypted), protection {2}.' -f $volume.VolumeStatus, $volume.EncryptionPercentage, $volume.ProtectionStatus)
    Sync-Intune
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

$problem = $null
if ($volume.VolumeStatus -eq 'FullyDecrypted') {
    Write-Step ('The task did not start encryption (last result 0x{0:x}). Turning BitLocker on with the policy''s settings.' -f ($task | Get-ScheduledTaskInfo).LastTaskResult)
    try {
        # Warnings are silenced because Add-BitLockerKeyProtector prints the new recovery password in one.
        if (-not @($volume.KeyProtector | Where-Object KeyProtectorType -EQ 'RecoveryPassword').Count) {
            $null = Add-BitLockerKeyProtector -MountPoint 'C:' -RecoveryPasswordProtector -WarningAction SilentlyContinue 3>$null
        }
        $recovery = (Get-BitLockerVolume -MountPoint 'C:').KeyProtector | Where-Object KeyProtectorType -EQ 'RecoveryPassword' | Select-Object -First 1
        $null = BackupToAAD-BitLockerKeyProtector -MountPoint 'C:' -KeyProtectorId $recovery.KeyProtectorId -WarningAction SilentlyContinue 3>$null
        Write-Step 'Recovery password saved in Entra ID.'
        # BitLocker's WMI provider, which returns BitLocker's own error codes (Enable-BitLocker only says E_FAIL).
        $encryptable = Get-CimInstance -Namespace 'root/CIMV2/Security/MicrosoftVolumeEncryption' -ClassName Win32_EncryptableVolume -Filter "DriveLetter = 'C:'"
        if (-not @((Get-BitLockerVolume -MountPoint 'C:').KeyProtector | Where-Object KeyProtectorType -EQ 'Tpm').Count) {
            $tpm = Get-Tpm
            Write-Step ('TPM: ready {0}, owned {1}, enabled {2}, activated {3}, auto-provisioning {4}, locked out {5}, restart pending {6}' -f
                $tpm.TpmReady, $tpm.TpmOwned, $tpm.TpmEnabled, $tpm.TpmActivated, $tpm.AutoProvisioning, $tpm.LockedOut, $tpm.RestartPending)
            foreach ($entry in Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-TPM-WMI' } -MaxEvents 4 -ErrorAction SilentlyContinue) {
                Write-Step ('TPM log, {0:yyyy-MM-dd HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($entry.Message -split "`n")[0].Trim())
            }
            $added = Invoke-CimMethod -InputObject $encryptable -MethodName ProtectKeyWithTPM
            # 2150694936 is 0x80310018, BitLocker's "the TPM must be initialised first" (written in decimal because
            # Windows PowerShell reads 0x80310018 as a negative number): provision it, never clearing it, and try again.
            if ($added.ReturnValue -eq 2150694936) {
                $initialised = Initialize-Tpm -AllowClear:$false -AllowPhysicalPresence:$false
                Write-Step ('BitLocker says the TPM is not initialised. Initialize-Tpm: ready {0}, restart required {1}, clear required {2}' -f
                    $initialised.TpmReady, $initialised.RestartRequired, $initialised.ClearRequired)
                $added = Invoke-CimMethod -InputObject $encryptable -MethodName ProtectKeyWithTPM
            }
            if ($added.ReturnValue -ne 0) { throw ('Adding the TPM protector failed with 0x{0:x8}.' -f $added.ReturnValue) }
            Write-Step 'TPM protector added.'
        }
        # Encryption method 7 is XTS-AES 256; flag 1 encrypts used space only, as Windows does for new PCs.
        $started = Invoke-CimMethod -InputObject $encryptable -MethodName Encrypt -Arguments @{ EncryptionMethod = [uint32]7; EncryptionFlags = [uint32]1 }
        if ($started.ReturnValue -ne 0) { throw ('Starting encryption failed with 0x{0:x8}.' -f $started.ReturnValue) }
        $volume = Wait-Encryption -Minutes 2
    }
    catch {
        $problem = $_.Exception.Message
        $volume = Get-BitLockerVolume -MountPoint 'C:'
    }
}

Write-Step ('C: {0}, {1}% encrypted, protection {2}, method {3}, key protectors: {4}' -f $volume.VolumeStatus,
    $volume.EncryptionPercentage, $volume.ProtectionStatus, $volume.EncryptionMethod, (@($volume.KeyProtector.KeyProtectorType) -join ', '))
# Event 881, a note about the boot manager's signature logged at every check, is left out.
$logged = Get-WinEvent -LogName 'Microsoft-Windows-BitLocker/BitLocker Management' -FilterXPath '*[System[EventID!=881]]' -MaxEvents 5 -ErrorAction SilentlyContinue
foreach ($entry in $logged) {
    Write-Step ('BitLocker log, {0:HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($entry.Message -split "`n")[0].Trim())
}
if ($problem) { throw $problem }
if ($volume.VolumeStatus -eq 'FullyDecrypted') {
    throw 'Encryption did not start; see the lines above.'
}
Sync-Intune
