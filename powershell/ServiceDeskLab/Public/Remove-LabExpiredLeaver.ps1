function Remove-LabExpiredLeaver {
    <#
    .SYNOPSIS
    Deletes leaver accounts once the retention period has passed.

    .DESCRIPTION
    Only touches disabled accounts in the Leavers OU whose leaving date stamp is older than the
    retention period in org.json (30 days). Supports -WhatIf; the fulfilment agent runs it daily.
    Deleted accounts stay in the AD Recycle Bin for 180 days.
    #>
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param([datetime]$Today = (Get-Date).Date)

    $config = Get-LabConfig
    $cutoff = $Today.AddDays(-$config.LeaverRetentionDays)
    $leavers = @(Get-ADUser -SearchBase $config.Ou.Leavers -SearchScope OneLevel -LDAPFilter '(objectClass=user)' -Properties 'msDS-cloudExtensionAttribute3', Enabled)
    foreach ($user in $leavers) {
        $stamp = $user.'msDS-cloudExtensionAttribute3'
        $left = [datetime]::MinValue
        if ($user.Enabled -or -not [datetime]::TryParseExact([string]$stamp, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$left)) {
            continue
        }
        if ($left -lt $cutoff -and $PSCmdlet.ShouldProcess($user.SamAccountName, "Delete (left $stamp)")) {
            Remove-ADUser -Identity $user.DistinguishedName -Confirm:$false
            Write-LabAudit -Action 'LeaverDeleted' -Target $user.SamAccountName -Detail @{ leavingDate = $stamp }
            [pscustomobject]@{ SamAccountName = $user.SamAccountName; LeavingDate = $left }
        }
    }
}
