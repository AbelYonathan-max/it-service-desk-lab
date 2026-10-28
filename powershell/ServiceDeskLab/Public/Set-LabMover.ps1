function Set-LabMover {
    <#
    .SYNOPSIS
    Moves a member of staff to another department or role: the "mover" in joiners, movers and leavers.

    .DESCRIPTION
    Moves the account to the new department's OU, swaps the department group, and removes access
    that came with the old department (its shared drive groups), so people do not build up access
    as they move around. Job title and line manager are updated when given. Returns what was
    added and removed, for the ticket's work notes.

    .EXAMPLE
    Set-LabMover -Identity anya.evans -Department CFS -JobTitle 'Social Worker' -Manager arjun.khan -Reference RITM0010007
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [Parameter(Mandatory = $true)][string]$Department,
        [string]$JobTitle,
        [string]$Manager,
        [string]$Reference
    )

    $config = Get-LabConfig
    $user = Resolve-LabUser -Identity $Identity
    $newDept = Resolve-LabDepartment -Department $Department -Config $config
    $oldDept = @($config.Departments | Where-Object { $_.Name -eq $user.Department })[0]

    $current = @(Get-LabGroupName -MemberOf $user.MemberOf)
    $toRemove = @()
    if ($oldDept -and $oldDept.Code -ne $newDept.Code) {
        $toRemove += $oldDept.Group
        $oldShare = @($config.Shares | Where-Object { $_.Name -eq $oldDept.Share })[0]
        # The Corporate share is everyone's, so it is never taken away on a move.
        if ($oldShare -and $oldShare.Name -ne $newDept.Share -and $oldShare.Readers -notcontains $config.Groups.allStaff) {
            $toRemove += $oldShare.ReadGroup, $oldShare.ModifyGroup
        }
    }
    $toRemove = @($toRemove | Where-Object { $current -contains $_ } | Select-Object -Unique)
    $toAdd = @($newDept.Group | Where-Object { $current -notcontains $_ })

    $managerUser = $null
    if ($Manager) { $managerUser = Resolve-LabUser -Identity $Manager }

    $moved = $false
    $targetDn = $user.DistinguishedName
    if ($user.DistinguishedName -notlike "*,$($newDept.OuDn)") {
        $rdn = ($user.DistinguishedName -split '(?<!\\),', 2)[0]
        $targetDn = '{0},{1}' -f $rdn, $newDept.OuDn
        $moved = $true
    }

    if ($PSCmdlet.ShouldProcess($user.SamAccountName, "Move to $($newDept.Name)")) {
        $set = @{ Identity = $user.DistinguishedName; Department = $newDept.Name }
        if ($JobTitle) { $set.Title = $JobTitle }
        if ($managerUser) { $set.Manager = $managerUser.DistinguishedName }
        Set-ADUser @set
        foreach ($group in $toRemove) { Remove-ADGroupMember -Identity $group -Members $user.SamAccountName -Confirm:$false }
        foreach ($group in $toAdd) { Add-ADGroupMember -Identity $group -Members $user.SamAccountName }
        if ($moved) { Move-ADObject -Identity $user.DistinguishedName -TargetPath $newDept.OuDn }
        Write-LabAudit -Action 'MoverProcessed' -Target $user.SamAccountName -Reference $Reference -Detail @{
            from = $user.Department; to = $newDept.Name; added = $toAdd; removed = $toRemove
        }
    }

    [pscustomobject]@{
        SamAccountName    = $user.SamAccountName
        FromDepartment    = $user.Department
        ToDepartment      = $newDept.Name
        DistinguishedName = $targetDn
        GroupsAdded       = $toAdd
        GroupsRemoved     = $toRemove
        Message           = "Moved $($user.SamAccountName) from $($user.Department) to $($newDept.Name). Added: $(if ($toAdd) { $toAdd -join ', ' } else { 'none' }). Removed: $(if ($toRemove) { $toRemove -join ', ' } else { 'none' })."
    }
}
