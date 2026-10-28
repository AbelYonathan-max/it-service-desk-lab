function Unlock-LabAccount {
    <#
    .SYNOPSIS
    Unlocks an account and says where the bad passwords came from, so it does not lock again.

    .DESCRIPTION
    A lockout that keeps coming back is nearly always an old password saved somewhere: a phone's
    mail app, a mapped drive, a scheduled task. Unlocking alone fixes the symptom; the lockout
    source (event 4740 on the PDC emulator) points to the cause. Both go in the result for the
    ticket, together with the bad password count.

    .EXAMPLE
    Unlock-LabAccount -Identity priya.sharma -Reference INC0010031
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [string]$Reference,
        [int]$LookBackHours = 24
    )

    $user = Resolve-LabUser -Identity $Identity -Properties badPwdCount, lockoutTime, LastBadPasswordAttempt
    $sources = @()
    try {
        $sources = @(Get-LabLockoutSource -Identity $user.SamAccountName -LookBackHours $LookBackHours)
    }
    catch {
        Write-Warning "Could not read lockout events: $($_.Exception.Message)"
    }
    $callers = @($sources | Select-Object -ExpandProperty CallerComputer -Unique | Where-Object { $_ })

    $wasLocked = [bool]$user.LockedOut
    if ($wasLocked -and $PSCmdlet.ShouldProcess($user.SamAccountName, 'Unlock')) {
        Unlock-ADAccount -Identity $user.DistinguishedName
        Write-LabAudit -Action 'AccountUnlocked' -Target $user.SamAccountName -Reference $Reference -Detail @{
            badPwdCount = $user.badPwdCount; lockoutSources = $callers
        }
    }

    $message = if (-not $wasLocked) {
        "$($user.SamAccountName) is not locked out."
    }
    elseif ($callers) {
        "Unlocked $($user.SamAccountName). Lockouts in the last $LookBackHours hours came from: $($callers -join ', '). Check for a saved old password there."
    }
    else {
        "Unlocked $($user.SamAccountName). No lockout events in the last $LookBackHours hours."
    }

    [pscustomobject]@{
        SamAccountName  = $user.SamAccountName
        WasLockedOut    = $wasLocked
        BadPasswordCount = $user.badPwdCount
        LockoutSources  = $callers
        Message         = $message
    }
}
