function Revoke-LabShareAccess {
    <#
    .SYNOPSIS
    Takes away a user's direct access to a shared drive (both read and modify groups).

    .DESCRIPTION
    Removes the user from the share's DL-FS groups. Access that comes through the department
    group is left alone: change the department with Set-LabMover instead. The result says if the
    user still has access through another group, so an access review is not closed too early.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [Parameter(Mandatory = $true)][string]$Share,
        [string]$Reference
    )

    $config = Get-LabConfig
    $user = Resolve-LabUser -Identity $Identity
    $target = Resolve-LabShare -Share $Share -Config $config
    $current = @(Get-LabGroupName -MemberOf $user.MemberOf)
    $removed = @(@($target.ReadGroup, $target.ModifyGroup) | Where-Object { $current -contains $_ })

    if ($removed -and $PSCmdlet.ShouldProcess($user.SamAccountName, "Remove from $($removed -join ', ')")) {
        foreach ($group in $removed) { Remove-ADGroupMember -Identity $group -Members $user.SamAccountName -Confirm:$false }
        Write-LabAudit -Action 'ShareAccessRevoked' -Target $user.SamAccountName -Reference $Reference -Detail @{
            share = $target.Name; groups = $removed
        }
    }

    $inherited = @($target.Editors + $target.Readers | Where-Object { $current -contains $_ })
    $message = if ($removed) { "Removed $($user.SamAccountName) from $($removed -join ', ')." } else { "$($user.SamAccountName) had no direct access to $($target.Name)." }
    if ($inherited) { $message += " Still has access through $($inherited -join ', ')." }

    [pscustomobject]@{
        SamAccountName = $user.SamAccountName
        Share          = $target.Name
        GroupsRemoved  = $removed
        StillHasAccess = [bool]$inherited
        Message        = $message
    }
}
