# Stage 2: builds the council's directory from lab\config (org.json and people.csv).
#
# - OU tree, AD Recycle Bin, a DNS forwarder to Azure DNS.
# - Password policy following NCSC guidance: 12+ characters, no forced expiry, lockout after 10
#   bad attempts for 15 minutes. Privileged accounts get a stricter fine-grained policy.
# - Groups: department (GG-Dept-*), role (GG-Role-*), licence (GG-LIC-*) and one read and one
#   modify group per shared drive (DL-FS-*), nested the AGDLP way.
# - Staff accounts, created with the same New-LabStarter function the service desk uses, then
#   line managers and role groups.
# - Delegation: the service desk can reset passwords and unlock accounts in the Staff OU and do
#   nothing else; the fulfilment agent's group managed service account can manage staff accounts
#   and group membership, and cannot touch admin accounts.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
    Justification = 'Helpers inside a build stage; the stage is the unit of change and is safe to run again.')]
param([string]$UpnSuffix)
. (Join-Path $PSScriptRoot 'Common.ps1')

Write-Step 'Waiting for Active Directory Web Services'
Import-Module ActiveDirectory
$domain = Wait-Until -What 'Active Directory' -Minutes 20 -Condition { Get-ADDomain }
$netbios = $domain.NetBIOSName

if ($UpnSuffix) { $env:SDLAB_UPN_SUFFIX = $UpnSuffix }
$env:SDLAB_HOME_SHARE = "\\$env:COMPUTERNAME\Home$"
Import-LabModule
$config = Get-LabConfig -Force

Write-Step 'DNS forwarder, Recycle Bin and UPN suffix'
if (-not ((Get-DnsServerForwarder).IPAddress.IPAddressToString -contains '168.63.129.16')) {
    Add-DnsServerForwarder -IPAddress 168.63.129.16
}
$forest = Get-ADForest
if (-not (Get-ADOptionalFeature -Filter "Name -eq 'Recycle Bin Feature'").EnabledScopes) {
    Enable-ADOptionalFeature -Identity 'Recycle Bin Feature' -Scope ForestOrConfigurationSet -Target $forest.Name -Confirm:$false
}
if ($UpnSuffix -and $forest.UPNSuffixes -notcontains $UpnSuffix) {
    Set-ADForest -Identity $forest.Name -UPNSuffixes @{ Add = $UpnSuffix }
}

Write-Step 'Password and lockout policy (NCSC guidance)'
Set-ADDefaultDomainPasswordPolicy -Identity $domain.DNSRoot -MinPasswordLength 12 -ComplexityEnabled $true `
    -MaxPasswordAge ([TimeSpan]::Zero) -MinPasswordAge ([TimeSpan]::FromDays(1)) -PasswordHistoryCount 24 `
    -LockoutThreshold 10 -LockoutDuration '00:15:00' -LockoutObservationWindow '00:15:00'
if (-not (Get-ADFineGrainedPasswordPolicy -Filter "Name -eq 'PSO-Privileged'")) {
    New-ADFineGrainedPasswordPolicy -Name 'PSO-Privileged' -Precedence 10 -MinPasswordLength 15 -ComplexityEnabled $true `
        -PasswordHistoryCount 24 -MaxPasswordAge '365.00:00:00' -MinPasswordAge '1.00:00:00' `
        -LockoutThreshold 5 -LockoutDuration '00:30:00' -LockoutObservationWindow '00:30:00' -ReversibleEncryptionEnabled $false
    Add-ADFineGrainedPasswordPolicySubject -Identity 'PSO-Privileged' -Subjects 'Domain Admins'
}

Write-Step 'Organisational units'
function Test-AdObject([string]$Dn) {
    try { [bool](Get-ADObject -Identity $Dn) } catch { $false }
}
function New-OuIfMissing([string]$Dn) {
    if (Test-AdObject $Dn) { return }
    $name, $parent = $Dn -split '(?<!\\),', 2
    New-ADOrganizationalUnit -Name ($name -replace '^OU=', '' -replace '\\(.)', '$1') -Path $parent -ProtectedFromAccidentalDeletion $true
}
# Parents before children: fewer commas first.
$ous = @($config.Ou.PSObject.Properties.Value) + @($config.Departments.OuDn) | Sort-Object { ($_ -split '(?<!\\),').Count }
foreach ($dn in $ous) { New-OuIfMissing $dn }

Write-Step 'Groups'
function New-GroupIfMissing([string]$Name, [string]$Path, [string]$Scope, [string]$Description) {
    if (Get-ADGroup -LDAPFilter "(sAMAccountName=$Name)") { return }
    New-ADGroup -Name $Name -SamAccountName $Name -GroupCategory Security -GroupScope $Scope -Path $Path -Description $Description
}
foreach ($dept in $config.Departments) {
    New-GroupIfMissing $dept.Group $config.Ou.DepartmentGroups Global "Everyone in $($dept.Name)"
}
New-GroupIfMissing $config.Groups.allStaff $config.Ou.RoleGroups Global 'All staff'
New-GroupIfMissing $config.Groups.managers $config.Ou.RoleGroups Global 'Heads of service and directors'
New-GroupIfMissing $config.Groups.serviceDesk $config.Ou.RoleGroups Global 'Service desk analysts: reset passwords and unlock staff accounts'
New-GroupIfMissing $config.Groups.desktopSupport $config.Ou.RoleGroups Global 'Desktop support engineers'
New-GroupIfMissing $config.Groups.infrastructure $config.Ou.RoleGroups Global 'Infrastructure engineers'
New-GroupIfMissing $config.Groups.fulfilmentAgents $config.Ou.RoleGroups Global 'Service accounts that carry out approved ServiceNow requests'
New-GroupIfMissing $config.Groups.licence $config.Ou.LicenceGroups Global 'Microsoft 365 Business Premium, assigned by group-based licensing in Entra ID'
foreach ($share in $config.Shares) {
    New-GroupIfMissing $share.ReadGroup $config.Ou.ResourceGroups DomainLocal "Read access to the $($share.Name) share. $($share.Description)."
    New-GroupIfMissing $share.ModifyGroup $config.Ou.ResourceGroups DomainLocal "Modify access to the $($share.Name) share. $($share.Description)."
    # AGDLP: department (global) groups go into the share's domain local groups.
    foreach ($member in $share.Editors) { Add-ADGroupMember -Identity $share.ModifyGroup -Members $member }
    foreach ($member in $share.Readers) { Add-ADGroupMember -Identity $share.ReadGroup -Members $member }
}

Write-Step 'Staff accounts'
$people = Import-Csv (Join-Path $script:RepoRoot 'lab\config\people.csv') -Encoding UTF8
$ceo = ($people | Where-Object { -not $_.ManagerEmployeeId }).EmployeeId
$created = 0
foreach ($person in $people) {
    $isManager = $person.ManagerEmployeeId -eq $ceo -or $person.EmployeeId -eq $ceo
    # 25 trial licences: IT staff and managers get one; the rest show how licensing is controlled.
    $licensed = $person.DepartmentCode -eq 'IT' -or $isManager
    $starter = @{
        FirstName    = $person.FirstName
        LastName     = $person.LastName
        Department   = $person.DepartmentCode
        JobTitle     = $person.JobTitle
        EmployeeId   = $person.EmployeeId
        Office       = $person.Office
        ContractType = $person.ContractType
        StartDate    = [datetime]::ParseExact($person.StartDate, 'yyyy-MM-dd', $null)
        Reference    = 'Initial load'
        NoLicence    = -not $licensed
    }
    if ($person.EndDate) { $starter.EndDate = [datetime]::ParseExact($person.EndDate, 'yyyy-MM-dd', $null) }
    $result = New-LabStarter @starter
    if ($result.Created) { $created++ }
}
Write-Step "Created $created account(s); $($people.Count - $created) already existed."

Write-Step 'Line managers and role groups'
$byEmployee = @{}
Get-ADUser -SearchBase $config.Ou.Staff -LDAPFilter '(employeeID=*)' -Properties EmployeeID, Manager, Title |
    ForEach-Object { $byEmployee[$_.EmployeeID] = $_ }
foreach ($person in $people) {
    $user = $byEmployee[$person.EmployeeId]
    if (-not $user) { continue }
    if ($person.ManagerEmployeeId) {
        $manager = $byEmployee[$person.ManagerEmployeeId]
        if ($manager -and $user.Manager -ne $manager.DistinguishedName) { Set-ADUser -Identity $user -Manager $manager }
    }
    $roles = @()
    if ($person.ManagerEmployeeId -eq $ceo -or $person.EmployeeId -eq $ceo) { $roles += $config.Groups.managers }
    switch ($person.JobTitle) {
        'Service Desk Analyst' { $roles += $config.Groups.serviceDesk }
        'Desktop Support Engineer' { $roles += $config.Groups.desktopSupport }
        'Infrastructure Engineer' { $roles += $config.Groups.infrastructure }
    }
    foreach ($role in $roles) { Add-ADGroupMember -Identity $role -Members $user }
}

if ($UpnSuffix) {
    Write-Step "Moving sign-in names to @$UpnSuffix for Microsoft 365"
    Get-ADUser -SearchBase $config.Ou.Staff -LDAPFilter '(employeeID=*)' | Where-Object { $_.UserPrincipalName -notlike "*@$UpnSuffix" } | ForEach-Object {
        $prefix = $_.UserPrincipalName.Split('@')[0]
        Set-ADUser -Identity $_ -UserPrincipalName "$prefix@$UpnSuffix" -EmailAddress "$prefix@$UpnSuffix"
    }
}

Write-Step 'Group managed service account for the fulfilment agent'
if (-not (Get-KdsRootKey)) {
    # Backdated so it works straight away in a single-DC lab. In production, wait the 10 hours.
    Add-KdsRootKey -EffectiveTime ((Get-Date).AddHours(-10)) | Out-Null
}
if (-not (Get-ADServiceAccount -Filter "Name -eq 'gmsa-sdagent'")) {
    New-ADServiceAccount -Name 'gmsa-sdagent' -DNSHostName "gmsa-sdagent.$($domain.DNSRoot)" `
        -PrincipalsAllowedToRetrieveManagedPassword "$env:COMPUTERNAME$" -Path $config.Ou.ServiceAccounts `
        -Description 'Runs the ServiceNow fulfilment agent on the domain controller'
}
Add-ADGroupMember -Identity $config.Groups.fulfilmentAgents -Members (Get-ADServiceAccount 'gmsa-sdagent')

Write-Step 'Delegated permissions'
function Grant-Delegation([string]$Ou, [string]$Inheritance, [string]$Ace) {
    $output = & dsacls.exe $Ou $Inheritance '/G' $Ace 2>&1
    if ($LASTEXITCODE -ne 0) { throw "dsacls failed for $Ace on ${Ou}: $output" }
}
$desk = "$netbios\$($config.Groups.serviceDesk)"
Grant-Delegation $config.Ou.Staff '/I:S' "${desk}:CA;Reset Password;user"
Grant-Delegation $config.Ou.Staff '/I:S' "${desk}:RPWP;lockoutTime;user"
Grant-Delegation $config.Ou.Staff '/I:S' "${desk}:RPWP;pwdLastSet;user"
$agents = "$netbios\$($config.Groups.fulfilmentAgents)"
foreach ($ou in @($config.Ou.Staff, $config.Ou.Leavers)) {
    Grant-Delegation $ou '/I:T' "${agents}:CCDC;user"
    Grant-Delegation $ou '/I:S' "${agents}:GA;;user"
}
Grant-Delegation $config.Ou.Groups '/I:S' "${agents}:RPWP;member;group"

$count = @(Get-ADUser -SearchBase $config.Ou.Staff -LDAPFilter '(employeeID=*)').Count
Write-Step "Directory ready: $count staff accounts, $(@($config.Departments).Count) departments, $(@($config.Shares).Count) shared drives."
