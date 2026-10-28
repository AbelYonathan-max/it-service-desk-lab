function Sync-LabServiceNowUser {
    <#
    .SYNOPSIS
    Keeps ServiceNow's user list in step with Active Directory.

    .DESCRIPTION
    The service desk needs every caller in ServiceNow with the right department, job title and
    line manager: approvals go to the manager, and reports group tickets by department. Real
    organisations do this with ServiceNow's LDAP integration through a MID Server; this does the
    same job in one PowerShell function the lab can run every 15 minutes.

    Matching is by employee ID, so a name change in AD updates the same ServiceNow record. Users
    who leave are set inactive, never deleted, so their old tickets keep their caller.

    .EXAMPLE
    Sync-LabServiceNowUser -Verbose
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param()

    $config = Get-LabConfig
    $adUsers = @(Get-ADUser -SearchBase $config.Ou.Root -SearchScope Subtree `
            -LDAPFilter '(&(objectCategory=person)(objectClass=user)(employeeID=*))' `
            -Properties GivenName, Surname, EmailAddress, Title, Department, Manager, EmployeeID, Enabled, Office, DistinguishedName)
    $snUsers = @(Invoke-LabSnApi -Path '/api/now/table/sys_user' -Query @{
            sysparm_query                  = 'employee_numberISNOTEMPTY'
            sysparm_fields                 = 'sys_id,user_name,first_name,last_name,email,title,employee_number,active,manager,department.name,location.name'
            sysparm_exclude_reference_link = 'true'
            sysparm_limit                  = '5000'
        })

    $byEmployee = @{}
    foreach ($sn in $snUsers) { $byEmployee[$sn.employee_number] = $sn }
    $created = 0; $updated = 0; $unchanged = 0

    foreach ($ad in $adUsers) {
        $inLeavers = $ad.DistinguishedName -like "*,$($config.Ou.Leavers)"
        $wanted = [ordered]@{
            user_name       = $ad.SamAccountName
            first_name      = $ad.GivenName
            last_name       = $ad.Surname
            email           = $ad.EmailAddress
            title           = $ad.Title
            employee_number = $ad.EmployeeID
            active          = if ($ad.Enabled -and -not $inLeavers) { 'true' } else { 'false' }
            department      = $ad.Department
            location        = $ad.Office
        }
        $current = $byEmployee[$ad.EmployeeID]
        if (-not $current) {
            if ($PSCmdlet.ShouldProcess($ad.SamAccountName, 'Create ServiceNow user')) {
                $new = Invoke-LabSnApi -Method POST -Path '/api/now/table/sys_user' -Query @{ sysparm_input_display_value = 'true'; sysparm_fields = 'sys_id,employee_number' } -Body $wanted
                $byEmployee[$ad.EmployeeID] = [pscustomobject]@{ sys_id = $new.sys_id; employee_number = $ad.EmployeeID; manager = '' }
            }
            $created++
            continue
        }

        $changes = @{}
        foreach ($field in $wanted.Keys) {
            $have = switch ($field) {
                'department' { $current.'department.name' }
                'location' { $current.'location.name' }
                default { $current.$field }
            }
            if ([string]$have -ne [string]$wanted[$field]) { $changes[$field] = $wanted[$field] }
        }
        if ($changes.Count) {
            if ($PSCmdlet.ShouldProcess($ad.SamAccountName, "Update $($changes.Keys -join ', ')")) {
                $null = Invoke-LabSnApi -Method PATCH -Path "/api/now/table/sys_user/$($current.sys_id)" -Query @{ sysparm_input_display_value = 'true'; sysparm_fields = 'sys_id' } -Body $changes
            }
            $updated++
        }
        else { $unchanged++ }
    }

    # Line managers second, once every manager has a ServiceNow record to point at.
    $byDn = @{}
    foreach ($ad in $adUsers) { $byDn[$ad.DistinguishedName] = $ad.EmployeeID }
    $managersSet = 0
    foreach ($ad in $adUsers) {
        $record = $byEmployee[$ad.EmployeeID]
        if (-not $record -or -not $record.sys_id) { continue }
        $managerId = if ($ad.Manager) { $byDn[$ad.Manager] } else { $null }
        $managerSysId = if ($managerId -and $byEmployee[$managerId]) { $byEmployee[$managerId].sys_id } else { '' }
        if ([string]$record.manager -ne [string]$managerSysId) {
            if ($PSCmdlet.ShouldProcess($ad.SamAccountName, 'Set line manager')) {
                $null = Invoke-LabSnApi -Method PATCH -Path "/api/now/table/sys_user/$($record.sys_id)" -Query @{ sysparm_fields = 'sys_id' } -Body @{ manager = $managerSysId }
            }
            $managersSet++
        }
    }

    Write-LabAudit -Action 'ServiceNowUserSync' -Target 'sys_user' -Detail @{
        created = $created; updated = $updated; unchanged = $unchanged; managersSet = $managersSet
    }
    [pscustomobject]@{ Created = $created; Updated = $updated; Unchanged = $unchanged; ManagersSet = $managersSet }
}
