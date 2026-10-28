function Get-LabAccountHygiene {
    <#
    .SYNOPSIS
    Finds accounts that need attention: the monthly account hygiene check.

    .DESCRIPTION
    Each finding has a category, the account and what to do:
    - Stale: enabled but no sign-in for 90 days (org.json staleAccountDays). Check with the manager.
    - NeverSignedIn: enabled, created over 30 days ago, never used. Likely a starter who did not
      start, or a duplicate.
    - ContractEnding: a temporary or contract account that expires within 14 days. Ask the
      manager whether to extend it, before it stops working on the person's last day.
    - ExpiredButEnabled: the end date has passed. The account no longer signs in, but should go
      through the leaver process.
    - DisabledOutsideLeavers: disabled but never processed as a leaver, so the groups are still there.

    .EXAMPLE
    Get-LabAccountHygiene | Sort-Object Category | Format-Table -AutoSize
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([datetime]$Today = (Get-Date))

    $config = Get-LabConfig
    $properties = 'LastLogonDate', 'whenCreated', 'AccountExpirationDate', 'Enabled', 'employeeType', 'Department'
    $users = @(Get-ADUser -SearchBase $config.Ou.Staff -SearchScope Subtree -LDAPFilter '(&(objectCategory=person)(objectClass=user))' -Properties $properties)

    foreach ($user in $users) {
        $finding = {
            param($category, $detail, $action)
            [pscustomobject]@{
                Category = $category; SamAccountName = $user.SamAccountName; Department = $user.Department
                Detail = $detail; Action = $action
            }
        }
        $expires = $user.AccountExpirationDate

        if (-not $user.Enabled) {
            & $finding 'DisabledOutsideLeavers' 'Disabled in a staff OU with its groups intact.' 'Run the leaver process or re-enable with a ticket.'
            continue
        }
        if ($expires -and $expires -le $Today) {
            & $finding 'ExpiredButEnabled' "Expired on $($expires.AddDays(-1).ToString('yyyy-MM-dd'))." 'Process as a leaver.'
            continue
        }
        if ($expires -and $expires -le $Today.AddDays(14)) {
            & $finding 'ContractEnding' "Expires after $($expires.AddDays(-1).ToString('yyyy-MM-dd')) ($($user.employeeType))." 'Ask the line manager to extend or confirm the leaving date.'
        }
        if ($user.LastLogonDate) {
            if ($user.LastLogonDate -lt $Today.AddDays(-$config.StaleAccountDays)) {
                & $finding 'Stale' "Last sign-in $($user.LastLogonDate.ToString('yyyy-MM-dd'))." 'Check with the line manager; disable if no longer needed.'
            }
        }
        elseif ($user.whenCreated -and $user.whenCreated -lt $Today.AddDays(-30)) {
            & $finding 'NeverSignedIn' "Created $($user.whenCreated.ToString('yyyy-MM-dd')), never used." 'Confirm the person started; otherwise disable.'
        }
    }
}
