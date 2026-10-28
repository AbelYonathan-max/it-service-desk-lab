function Get-LabAccessReport {
    <#
    .SYNOPSIS
    Shows who can reach each shared drive, and how: for access reviews and data protection checks.

    .DESCRIPTION
    For every share (or the one named), lists each person with access, whether it is read or
    modify, and the route: through their department group or granted directly. Data owners
    review the direct grants each quarter; anything without a ticket gets removed. Pipe to
    Export-Csv for the review pack.

    .EXAMPLE
    Get-LabAccessReport -Share HR | Export-Csv hr-access-review.csv -NoTypeInformation
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([string]$Share)

    $config = Get-LabConfig
    $shares = if ($Share) { @(Resolve-LabShare -Share $Share -Config $config) } else { $config.Shares }

    foreach ($target in $shares) {
        foreach ($level in @(@{ Access = 'Modify'; Group = $target.ModifyGroup }, @{ Access = 'Read'; Group = $target.ReadGroup })) {
            foreach ($member in @(Get-ADGroupMember -Identity $level.Group)) {
                if ($member.objectClass -eq 'group') {
                    foreach ($person in @(Get-ADGroupMember -Identity $member.SamAccountName -Recursive)) {
                        if ($person.objectClass -ne 'user') { continue }
                        [pscustomobject]@{
                            Share = $target.Name; Restricted = $target.Restricted; Access = $level.Access
                            SamAccountName = $person.SamAccountName; Name = $person.Name; Via = $member.Name
                        }
                    }
                }
                elseif ($member.objectClass -eq 'user') {
                    [pscustomobject]@{
                        Share = $target.Name; Restricted = $target.Restricted; Access = $level.Access
                        SamAccountName = $member.SamAccountName; Name = $member.Name; Via = 'Direct grant'
                    }
                }
            }
        }
    }
}
