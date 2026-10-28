function New-LabStarter {
    <#
    .SYNOPSIS
    Creates a new starter's account: the "joiner" in joiners, movers and leavers.

    .DESCRIPTION
    - Account name follows the naming convention (see New-LabAccountName); the UPN and email use
      the lab's UPN suffix, so Entra Cloud Sync can match the account to Microsoft 365.
    - Goes in the department's OU and gets the department group, the all-staff group and the
      Microsoft 365 licence group (group-based licensing does the rest in Entra ID).
    - Temporary and contract staff must have an end date; the account then expires by itself.
    - Future starters are created disabled; Invoke-LabFulfilment enables them on the start date.
    - The temporary password is returned as a SecureString, never written to logs or tickets.
    - Running it twice for the same employee ID does not create a second account.

    .EXAMPLE
    New-LabStarter -FirstName Amara -LastName Okafor -Department HSG -JobTitle 'Housing Officer' `
        -EmployeeId E10101 -Manager lucas.murphy -StartDate 2026-11-02 -Reference RITM0010001
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$FirstName,
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$LastName,
        [Parameter(Mandatory = $true)][string]$Department,
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$JobTitle,
        [Parameter(Mandatory = $true)][ValidatePattern('^E\d{5}$')][string]$EmployeeId,
        [string]$Manager,
        [string]$Office,
        [ValidateSet('Permanent', 'Temporary', 'Contractor')][string]$ContractType = 'Permanent',
        [datetime]$StartDate = (Get-Date).Date,
        [Nullable[datetime]]$EndDate,
        [string]$Reference,
        [switch]$NoLicence
    )

    $config = Get-LabConfig
    if ($ContractType -ne 'Permanent' -and -not $EndDate) {
        throw "A $($ContractType.ToLower()) starter needs an end date, so the account expires on its own."
    }
    if ($EndDate -and $EndDate.Date -le $StartDate.Date) {
        throw 'The end date must be after the start date.'
    }
    $dept = Resolve-LabDepartment -Department $Department -Config $config

    $existing = @(Get-ADUser -LDAPFilter "(employeeID=$(ConvertTo-LabLdapFilterValue $EmployeeId))" -Properties MemberOf, Enabled)
    if ($existing.Count -gt 0) {
        $user = $existing[0]
        return [pscustomobject]@{
            SamAccountName    = $user.SamAccountName
            UserPrincipalName = $user.UserPrincipalName
            DistinguishedName = $user.DistinguishedName
            Department        = $dept.Name
            Groups            = @(Get-LabGroupName -MemberOf $user.MemberOf)
            Enabled           = [bool]$user.Enabled
            StartDate         = $StartDate.Date
            EndDate           = $EndDate
            TemporaryPassword = $null
            Created           = $false
            Message           = "An account for employee $EmployeeId already exists ($($user.SamAccountName)), so nothing was created."
        }
    }

    $managerUser = $null
    if ($Manager) { $managerUser = Resolve-LabUser -Identity $Manager }

    $exists = {
        param($sam, $upn)
        $samValue = ConvertTo-LabLdapFilterValue $sam
        $upnValue = ConvertTo-LabLdapFilterValue ('{0}@{1}' -f $upn, $config.UpnSuffix)
        [bool](@(Get-ADUser -LDAPFilter "(|(sAMAccountName=$samValue)(userPrincipalName=$upnValue)(proxyAddresses=smtp:$upnValue))").Count)
    }
    $names = New-LabAccountName -FirstName $FirstName -LastName $LastName -MaxLength $config.MaxSamLength -Exists $exists
    $upn = '{0}@{1}' -f $names.UpnPrefix, $config.UpnSuffix

    $displayName = '{0} {1}' -f $FirstName.Trim(), $LastName.Trim()
    $cn = $displayName
    $sameName = @(Get-ADUser -LDAPFilter "(cn=$(ConvertTo-LabLdapFilterValue $cn))" -SearchBase $dept.OuDn -SearchScope OneLevel)
    if ($sameName.Count -gt 0) { $cn = '{0} ({1})' -f $displayName, $names.SamAccountName }

    $startsNow = $StartDate.Date -le (Get-Date).Date
    $description = if ($startsNow) { "$JobTitle. Joined $($StartDate.ToString('yyyy-MM-dd'))" } else { "$JobTitle. Starts $($StartDate.ToString('yyyy-MM-dd'))" }
    if ($Reference) { $description += " ($Reference)" }

    $secure = New-LabPassword -AsSecureString

    $params = @{
        Name                  = $cn
        DisplayName           = $displayName
        GivenName             = $FirstName.Trim()
        Surname               = $LastName.Trim()
        SamAccountName        = $names.SamAccountName
        UserPrincipalName     = $upn
        EmailAddress          = $upn
        Path                  = $dept.OuDn
        Department            = $dept.Name
        Title                 = $JobTitle
        Company               = $config.Organisation
        EmployeeID            = $EmployeeId
        Description           = $description
        AccountPassword       = $secure
        ChangePasswordAtLogon = $true
        Enabled               = $startsNow
        OtherAttributes       = @{
            employeeType                    = $ContractType
            'msDS-cloudExtensionAttribute1' = $StartDate.ToString('yyyy-MM-dd')
        }
    }
    # Marks a future starter, so the fulfilment agent knows to enable the account on the start date.
    if (-not $startsNow) { $params.OtherAttributes['msDS-cloudExtensionAttribute2'] = 'PendingStart' }
    if ($Office) { $params.Office = $Office }
    if ($managerUser) { $params.Manager = $managerUser.DistinguishedName }
    # AD expires an account at the start of the given day, so the day after the last working day.
    if ($EndDate) { $params.AccountExpirationDate = $EndDate.Date.AddDays(1) }
    if ($config.HomeShare) {
        $params.HomeDirectory = '{0}\{1}' -f $config.HomeShare.TrimEnd('\'), $names.SamAccountName
        $params.HomeDrive = $config.FileServer.homeDrive
    }

    $groups = @($dept.Group, $config.Groups.allStaff)
    if (-not $NoLicence) { $groups += $config.Groups.licence }

    $dn = 'CN={0},{1}' -f (ConvertTo-LabDnValue $cn), $dept.OuDn
    if ($PSCmdlet.ShouldProcess($upn, "Create account in $($dept.Name)")) {
        New-ADUser @params
        foreach ($group in $groups) {
            Add-ADGroupMember -Identity $group -Members $names.SamAccountName
        }
        Write-LabAudit -Action 'StarterCreated' -Target $names.SamAccountName -Reference $Reference -Detail @{
            employeeId = $EmployeeId; department = $dept.Code; groups = $groups; enabled = $startsNow
            endDate    = if ($EndDate) { $EndDate.ToString('yyyy-MM-dd') } else { $null }
        }
    }

    [pscustomobject]@{
        SamAccountName    = $names.SamAccountName
        UserPrincipalName = $upn
        DistinguishedName = $dn
        Department        = $dept.Name
        Groups            = $groups
        Enabled           = $startsNow
        StartDate         = $StartDate.Date
        EndDate           = $EndDate
        TemporaryPassword = $secure
        Created           = $true
        Message           = if ($startsNow) { "Created $upn in $($dept.Name)." } else { "Created $upn in $($dept.Name), disabled until $($StartDate.ToString('dd MMM yyyy'))." }
    }
}
