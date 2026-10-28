function Disable-LabLeaver {
    <#
    .SYNOPSIS
    Closes a leaver's access: the "leaver" in joiners, movers and leavers.

    .DESCRIPTION
    In this order, so access is gone before anything else happens:
    1. Disables the account and sets a random password nobody knows.
    2. Removes every group membership (except Domain Users) and records them in the account's
       notes, so access can be restored if the leaver comes back.
    3. Moves the account to the Leavers OU and stamps the leaving date. Remove-LabExpiredLeaver
       deletes it after the retention period (30 days); the AD Recycle Bin keeps it 180 more.
    Removing the licence group also takes the Microsoft 365 licence back through group-based
    licensing, and the next Cloud Sync run disables the Entra ID account, ending new sign-ins.

    .EXAMPLE
    Disable-LabLeaver -Identity ruby.hall -Reference RITM0010012
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [datetime]$LeavingDate = (Get-Date).Date,
        [string]$Reference
    )

    $config = Get-LabConfig
    $user = Resolve-LabUser -Identity $Identity -Properties info
    if ($user.DistinguishedName -like "*,$($config.Ou.Leavers)" -and -not $user.Enabled) {
        return [pscustomobject]@{
            SamAccountName = $user.SamAccountName
            GroupsRemoved  = @()
            Message        = "$($user.SamAccountName) was already processed as a leaver."
        }
    }

    $groups = @(Get-LabGroupName -MemberOf $user.MemberOf | Where-Object { $_ -ne 'Domain Users' })
    $stamp = $LeavingDate.ToString('yyyy-MM-dd')
    $note = "Leaver $stamp$(if ($Reference) { " ($Reference)" }). Groups removed: $(if ($groups) { $groups -join '; ' } else { 'none' })."
    if ($user.info) { $note = "$($user.info)`r`n$note" }

    if ($PSCmdlet.ShouldProcess($user.SamAccountName, 'Disable and remove access')) {
        Disable-ADAccount -Identity $user.DistinguishedName
        $scramble = New-LabPassword -Words 6 -AsSecureString
        Set-ADAccountPassword -Identity $user.DistinguishedName -Reset -NewPassword $scramble
        foreach ($group in $groups) {
            Remove-ADGroupMember -Identity $group -Members $user.SamAccountName -Confirm:$false
        }
        Set-ADUser -Identity $user.DistinguishedName -Description "Leaver $stamp$(if ($Reference) { " ($Reference)" })" -Replace @{
            info                            = $note
            'msDS-cloudExtensionAttribute3' = $stamp
        }
        Move-ADObject -Identity $user.DistinguishedName -TargetPath $config.Ou.Leavers
        Write-LabAudit -Action 'LeaverProcessed' -Target $user.SamAccountName -Reference $Reference -Detail @{
            leavingDate = $stamp; groupsRemoved = $groups
        }
    }

    [pscustomobject]@{
        SamAccountName = $user.SamAccountName
        GroupsRemoved  = $groups
        Message        = "Disabled $($user.SamAccountName), reset the password, removed $($groups.Count) group(s) ($(if ($groups) { $groups -join ', ' } else { 'none' })) and moved the account to Leavers. It will be deleted after $($config.LeaverRetentionDays) days."
    }
}
