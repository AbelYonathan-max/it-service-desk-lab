# Stand-ins for the ActiveDirectory module's cmdlets, so the tests run on Linux and on CI runners
# without a domain. Each test mocks the ones it needs; calling an unmocked stub fails the test.
# Parameters match the real cmdlets for the ones this module uses.

function global:Get-ADUser {
    [CmdletBinding()]
    param($Identity, $Filter, $LDAPFilter, $Properties, $SearchBase, $SearchScope, $Server)
    throw 'Get-ADUser was called without a mock.'
}

function global:New-ADUser {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Name, $DisplayName, $GivenName, $Surname, $SamAccountName, $UserPrincipalName, $EmailAddress, $Path,
        $Department, $Title, $Company, $EmployeeID, $Description, $AccountPassword, $ChangePasswordAtLogon, $Enabled,
        $OtherAttributes, $Office, $Manager, $AccountExpirationDate, $HomeDirectory, $HomeDrive, [switch]$PassThru)
    throw 'New-ADUser was called without a mock.'
}

function global:Set-ADUser {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity, $Department, $Title, $Manager, $Description, $Replace, $Clear, $ChangePasswordAtLogon)
    throw 'Set-ADUser was called without a mock.'
}

function global:Add-ADGroupMember {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity, $Members)
    throw 'Add-ADGroupMember was called without a mock.'
}

function global:Remove-ADGroupMember {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity, $Members)
    throw 'Remove-ADGroupMember was called without a mock.'
}

function global:Get-ADGroupMember {
    [CmdletBinding()]
    param($Identity, [switch]$Recursive)
    throw 'Get-ADGroupMember was called without a mock.'
}

function global:Move-ADObject {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity, $TargetPath)
    throw 'Move-ADObject was called without a mock.'
}

function global:Disable-ADAccount {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity)
    throw 'Disable-ADAccount was called without a mock.'
}

function global:Enable-ADAccount {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity)
    throw 'Enable-ADAccount was called without a mock.'
}

function global:Unlock-ADAccount {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity)
    throw 'Unlock-ADAccount was called without a mock.'
}

function global:Set-ADAccountPassword {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity, [switch]$Reset, $NewPassword)
    throw 'Set-ADAccountPassword was called without a mock.'
}

function global:Remove-ADUser {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param($Identity)
    throw 'Remove-ADUser was called without a mock.'
}

function global:Get-ADDomain {
    [CmdletBinding()]
    param()
    throw 'Get-ADDomain was called without a mock.'
}

function global:Get-WinEvent {
    [CmdletBinding()]
    param($ComputerName, $FilterHashtable)
    throw 'Get-WinEvent was called without a mock.'
}
