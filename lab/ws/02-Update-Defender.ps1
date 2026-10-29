# Stage 2 for the PC: brings Microsoft Defender's security intelligence up to date, which Intune's
# "KBC - Windows compliance" policy requires (signatureOutOfDate). The lab PC's Azure image came with
# security intelligence from 17 September 2025, still in place on 29 October 2026 (407 days old):
# Defender had not updated it by itself, and its log showed no attempt to. The stage prints
# Defender's last update attempts, updates from Microsoft Update (or from Microsoft's malware
# protection centre if that fails), and asks the PC to sync with Intune so the compliance check sees
# the new version. Safe to run again.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) { Write-Output ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Message) }

function Write-Intelligence($Status) {
    Write-Step ('Security intelligence {0}, updated {1:yyyy-MM-dd HH:mm} ({2} day(s) old), out of date {3}, engine {4}' -f
        $Status.AntivirusSignatureVersion, $Status.AntivirusSignatureLastUpdated, $Status.AntivirusSignatureAge,
        $Status.DefenderSignaturesOutOfDate, $Status.AMEngineVersion)
}

function Test-OutOfDate($Status) { $Status.DefenderSignaturesOutOfDate -or $Status.AntivirusSignatureAge -gt 1 }

function Sync-Intune {
    # PushLaunch is the task the Intune enrolment created to check in with Intune.
    $push = @(Get-ScheduledTask -TaskPath '\Microsoft\Windows\EnterpriseMgmt\*' -TaskName 'PushLaunch' -ErrorAction SilentlyContinue)
    foreach ($task in $push) { Start-ScheduledTask -InputObject $task }
    Write-Step $(if ($push.Count) { 'Asked the PC to sync with Intune.' } else { 'No Intune sync task found on the PC.' })
}

$status = Get-MpComputerStatus
Write-Intelligence $status
# Event 2000 is a security intelligence update that worked, 2001 one that failed.
$attempts = Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Windows Defender/Operational'; Id = 2000, 2001 } -MaxEvents 3 -ErrorAction SilentlyContinue
if (-not $attempts) { Write-Step 'Defender log: no security intelligence update attempts.' }
foreach ($entry in $attempts) {
    $detail = foreach ($field in 'Update source', 'Error code', 'Error description') {
        if ($entry.Message -match "$field\s*:\s*(.+)") { '{0}: {1}' -f $field, $Matches[1].Trim() }
    }
    Write-Step ('Defender log, {0:yyyy-MM-dd HH:mm} event {1}: {2}' -f $entry.TimeCreated, $entry.Id, ($detail -join '; '))
}

if (Test-OutOfDate $status) {
    foreach ($source in 'MicrosoftUpdateServer', 'MMPC') {
        try {
            Update-MpSignature -UpdateSource $source
            Write-Step "Updated from $source."
            break
        }
        catch {
            Write-Step ('Updating from {0} failed: {1}' -f $source, $_.Exception.Message)
        }
    }
    $status = Get-MpComputerStatus
    Write-Intelligence $status
    if (Test-OutOfDate $status) { throw 'Security intelligence is still out of date; see the lines above.' }
}
Sync-Intune
