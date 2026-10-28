function Grant-LabShareAccess {
    <#
    .SYNOPSIS
    Gives a user read or modify access to a department shared drive.

    .DESCRIPTION
    Access is granted by group membership (DL-FS-<share>-RO or -RW), never by editing folder
    permissions, so it is visible in AD, easy to review and easy to take away. Shares holding
    special category data (social care, children's case files, HR, legal) need the data owner's
    approval, passed as -ApprovedBy; ServiceNow records that approval before the request reaches
    the fulfilment agent.

    .EXAMPLE
    Grant-LabShareAccess -Identity ryan.ward -Share Finance -Access Read -Reference RITM0010019
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [Parameter(Mandatory = $true)][string]$Share,
        [Parameter(Mandatory = $true)][ValidateSet('Read', 'Modify')][string]$Access,
        [string]$ApprovedBy,
        [string]$Reference
    )

    $config = Get-LabConfig
    $user = Resolve-LabUser -Identity $Identity
    $target = Resolve-LabShare -Share $Share -Config $config
    if ($target.Restricted -and -not $ApprovedBy) {
        throw "The $($target.Name) share holds special category data. Access needs the data owner's approval (-ApprovedBy)."
    }
    if (-not $user.Enabled) { throw "$($user.SamAccountName) is disabled." }

    $group = if ($Access -eq 'Modify') { $target.ModifyGroup } else { $target.ReadGroup }
    $current = @(Get-LabGroupName -MemberOf $user.MemberOf)
    if ($current -contains $group) {
        return [pscustomobject]@{
            SamAccountName = $user.SamAccountName; Share = $target.Name; Group = $group; Changed = $false
            Message = "$($user.SamAccountName) already has $($Access.ToLower()) access to $($target.Name) ($group)."
        }
    }

    if ($PSCmdlet.ShouldProcess($user.SamAccountName, "Add to $group")) {
        Add-ADGroupMember -Identity $group -Members $user.SamAccountName
        Write-LabAudit -Action 'ShareAccessGranted' -Target $user.SamAccountName -Reference $Reference -Detail @{
            share = $target.Name; access = $Access; group = $group; approvedBy = $ApprovedBy
        }
    }

    [pscustomobject]@{
        SamAccountName = $user.SamAccountName
        Share          = $target.Name
        Group          = $group
        Changed        = $true
        Message        = "Added $($user.SamAccountName) to $group ($($Access.ToLower()) access to the $($target.Name) shared drive). Takes effect at next sign-in."
    }
}
