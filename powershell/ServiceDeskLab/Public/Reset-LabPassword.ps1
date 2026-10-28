function Reset-LabPassword {
    <#
    .SYNOPSIS
    Resets a user's password after their identity has been checked, and unlocks the account.

    .DESCRIPTION
    Password resets are the most common service desk call and the favourite route for social
    engineering, so -VerifiedBy is mandatory: record how the caller proved who they are (for
    example "Called back on the number held by HR" or "Manager confirmed on Teams"). The new
    password is temporary, must be changed at next sign-in, and is returned as a SecureString
    to read out to the caller. Privileged accounts are refused: those go to the identity team.

    .EXAMPLE
    $r = Reset-LabPassword -Identity zoe.smith-jones -VerifiedBy 'Called back on HR number' -Reference INC0010023
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [Parameter(Mandatory = $true)][ValidateLength(10, 500)][string]$VerifiedBy,
        [string]$Reference,
        [switch]$NoUnlock
    )

    $config = Get-LabConfig
    $user = Resolve-LabUser -Identity $Identity -Properties adminCount
    if ($user.adminCount -eq 1 -or $user.DistinguishedName -like "*,$($config.Ou.Admin)") {
        throw "$($user.SamAccountName) is a privileged account. Its password is reset by the identity team, not the service desk."
    }
    if (-not $user.Enabled) {
        throw "$($user.SamAccountName) is disabled. Check for a leaver request before doing anything else."
    }

    $temporary = New-LabPassword -AsSecureString
    $unlocked = $false
    if ($PSCmdlet.ShouldProcess($user.SamAccountName, 'Reset password')) {
        Set-ADAccountPassword -Identity $user.DistinguishedName -Reset -NewPassword $temporary
        Set-ADUser -Identity $user.DistinguishedName -ChangePasswordAtLogon $true
        if (-not $NoUnlock -and $user.LockedOut) {
            Unlock-ADAccount -Identity $user.DistinguishedName
            $unlocked = $true
        }
        Write-LabAudit -Action 'PasswordReset' -Target $user.SamAccountName -Reference $Reference -Detail @{
            verifiedBy = $VerifiedBy; unlocked = $unlocked
        }
    }

    [pscustomobject]@{
        SamAccountName    = $user.SamAccountName
        TemporaryPassword = $temporary
        Unlocked          = $unlocked
        Message           = "Password reset for $($user.SamAccountName) (identity verified: $VerifiedBy). Must be changed at next sign-in.$(if ($unlocked) { ' Account unlocked.' })"
    }
}
