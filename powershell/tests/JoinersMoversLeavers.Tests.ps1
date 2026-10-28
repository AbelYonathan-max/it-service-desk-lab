BeforeAll {
    . (Join-Path $PSScriptRoot 'TestSetup.ps1')
}

Describe 'New-LabStarter' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Get-ADUser { }
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '*lucas.murphy*' } {
            New-TestUser -Sam 'lucas.murphy' -Department 'Housing'
        }
        Mock -ModuleName ServiceDeskLab New-ADUser { }
        Mock -ModuleName ServiceDeskLab Add-ADGroupMember { }
        $today = (Get-Date).Date
    }

    It 'creates the account in the department OU with the naming convention' {
        $result = New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' `
            -EmployeeId E10101 -Manager lucas.murphy -Reference RITM0010001

        $result.Created | Should -BeTrue
        $result.SamAccountName | Should -Be 'amara.okafor'
        $result.UserPrincipalName | Should -Be 'amara.okafor@kestrel.example'
        $result.TemporaryPassword | Should -BeOfType [securestring]
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -Times 1 -Exactly -ParameterFilter {
            $Path -eq 'OU=Housing,OU=Staff,OU=Kestrel,DC=corp,DC=kestrel,DC=internal' -and
            $SamAccountName -eq 'amara.okafor' -and $EmailAddress -eq 'amara.okafor@kestrel.example' -and
            $Department -eq 'Housing' -and $Company -eq 'Kestrel Borough Council' -and
            $ChangePasswordAtLogon -eq $true -and $Enabled -eq $true -and
            $Manager -like 'CN=lucas.murphy,*' -and $Description -like '*RITM0010001*' -and
            $OtherAttributes.employeeType -eq 'Permanent' -and -not $AccountExpirationDate
        }
    }

    It 'adds the department, all-staff and licence groups' {
        New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department Housing -JobTitle 'Housing Officer' -EmployeeId E10101 | Out-Null
        foreach ($group in 'GG-Dept-Housing', 'GG-All-Staff', 'GG-LIC-M365-BusinessPremium') {
            Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 1 -Exactly -ParameterFilter { $Identity -eq $group -and $Members -eq 'amara.okafor' }
        }
    }

    It 'leaves out the licence group with -NoLicence' {
        New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101 -NoLicence | Out-Null
        Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 0 -Exactly -ParameterFilter { $Identity -eq 'GG-LIC-M365-BusinessPremium' }
    }

    It 'creates a future starter disabled and marked for enabling on the start date' {
        $start = $today.AddDays(10)
        $result = New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101 -StartDate $start
        $result.Enabled | Should -BeFalse
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -ParameterFilter {
            $Enabled -eq $false -and $OtherAttributes.'msDS-cloudExtensionAttribute2' -eq 'PendingStart' -and
            $OtherAttributes.'msDS-cloudExtensionAttribute1' -eq $start.ToString('yyyy-MM-dd')
        }
    }

    It 'makes temporary accounts expire the day after the last working day' {
        $end = $today.AddMonths(6)
        New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101 `
            -ContractType Temporary -EndDate $end | Out-Null
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -ParameterFilter {
            $AccountExpirationDate -eq $end.AddDays(1) -and $OtherAttributes.employeeType -eq 'Temporary'
        }
    }

    It 'refuses a contractor with no end date' {
        { New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101 -ContractType Contractor } |
            Should -Throw '*needs an end date*'
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -Times 0 -Exactly
    }

    It 'refuses an unknown department' {
        { New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department Marketing -JobTitle 'Officer' -EmployeeId E10101 } |
            Should -Throw '*Unknown department*'
    }

    It 'does not create a second account for the same employee ID' {
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -eq '(employeeID=E10101)' } {
            New-TestUser -Sam 'amara.okafor' -Department 'Housing' -MemberOf 'GG-Dept-Housing'
        }
        $result = New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101
        $result.Created | Should -BeFalse
        $result.Message | Should -BeLike '*already exists*'
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -Times 0 -Exactly
    }

    It 'numbers the name when firstname.lastname is taken' {
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '(|(sAMAccountName=james.smith)*' } {
            New-TestUser -Sam 'james.smith'
        }
        $result = New-LabStarter -FirstName 'James' -LastName 'Smith' -Department CS -JobTitle 'Customer Services Advisor' -EmployeeId E10102
        $result.SamAccountName | Should -Be 'james.smith2'
    }

    It 'changes nothing with -WhatIf' {
        New-LabStarter -FirstName 'Amara' -LastName 'Okafor' -Department HSG -JobTitle 'Housing Officer' -EmployeeId E10101 -WhatIf | Out-Null
        Should -Invoke -ModuleName ServiceDeskLab New-ADUser -Times 0 -Exactly
        Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 0 -Exactly
    }
}

Describe 'Set-LabMover' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '*anya.evans*' } {
            New-TestUser -Sam 'anya.evans' -Department 'Adult Social Care' -MemberOf 'GG-Dept-AdultSocialCare', 'GG-All-Staff', 'DL-FS-AdultSocialCare-RW', 'DL-FS-Finance-RO'
        }
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '*arjun.khan*' } {
            New-TestUser -Sam 'arjun.khan' -Department 'Children and Families'
        }
        Mock -ModuleName ServiceDeskLab Set-ADUser { }
        Mock -ModuleName ServiceDeskLab Add-ADGroupMember { }
        Mock -ModuleName ServiceDeskLab Remove-ADGroupMember { }
        Mock -ModuleName ServiceDeskLab Move-ADObject { }
    }

    It 'swaps the department group, removes the old department share and moves the OU' {
        $result = Set-LabMover -Identity anya.evans -Department CFS -JobTitle 'Social Worker' -Manager arjun.khan -Reference RITM0010007

        $result.GroupsAdded | Should -Be @('GG-Dept-ChildrenAndFamilies')
        $result.GroupsRemoved | Should -Contain 'GG-Dept-AdultSocialCare'
        $result.GroupsRemoved | Should -Contain 'DL-FS-AdultSocialCare-RW'
        $result.GroupsRemoved | Should -Not -Contain 'GG-All-Staff'
        # Access granted for another reason (Finance read) is not the move's business.
        $result.GroupsRemoved | Should -Not -Contain 'DL-FS-Finance-RO'
        Should -Invoke -ModuleName ServiceDeskLab Move-ADObject -Times 1 -Exactly -ParameterFilter {
            $TargetPath -eq 'OU=Children and Families,OU=Staff,OU=Kestrel,DC=corp,DC=kestrel,DC=internal'
        }
        Should -Invoke -ModuleName ServiceDeskLab Set-ADUser -ParameterFilter {
            $Department -eq 'Children and Families' -and $Title -eq 'Social Worker' -and $Manager -like 'CN=arjun.khan,*'
        }
    }

    It 'only updates the title when the department does not change' {
        $result = Set-LabMover -Identity anya.evans -Department ASC -JobTitle 'Senior Social Worker'
        $result.GroupsRemoved | Should -BeNullOrEmpty
        $result.GroupsAdded | Should -BeNullOrEmpty
        Should -Invoke -ModuleName ServiceDeskLab Move-ADObject -Times 0 -Exactly
    }
}

Describe 'Disable-LabLeaver' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '*ruby.hall*' } {
            New-TestUser -Sam 'ruby.hall' -Department 'Finance' -MemberOf 'GG-Dept-Finance', 'GG-All-Staff', 'GG-LIC-M365-BusinessPremium', 'Domain Users'
        }
        Mock -ModuleName ServiceDeskLab Disable-ADAccount { }
        Mock -ModuleName ServiceDeskLab Set-ADAccountPassword { }
        Mock -ModuleName ServiceDeskLab Remove-ADGroupMember { }
        Mock -ModuleName ServiceDeskLab Set-ADUser { }
        Mock -ModuleName ServiceDeskLab Move-ADObject { }
    }

    It 'disables, scrambles the password, removes every group and moves to Leavers' {
        $result = Disable-LabLeaver -Identity ruby.hall -LeavingDate '2026-11-27' -Reference RITM0010012

        $result.GroupsRemoved | Should -HaveCount 3
        $result.GroupsRemoved | Should -Not -Contain 'Domain Users'
        Should -Invoke -ModuleName ServiceDeskLab Disable-ADAccount -Times 1 -Exactly
        Should -Invoke -ModuleName ServiceDeskLab Set-ADAccountPassword -Times 1 -Exactly -ParameterFilter { $Reset -and $NewPassword -is [securestring] }
        Should -Invoke -ModuleName ServiceDeskLab Remove-ADGroupMember -Times 3 -Exactly
        Should -Invoke -ModuleName ServiceDeskLab Set-ADUser -ParameterFilter {
            $Replace.'msDS-cloudExtensionAttribute3' -eq '2026-11-27' -and $Replace.info -like '*GG-Dept-Finance; GG-All-Staff; GG-LIC-M365-BusinessPremium*'
        }
        Should -Invoke -ModuleName ServiceDeskLab Move-ADObject -ParameterFilter { $TargetPath -eq 'OU=Leavers,OU=Kestrel,DC=corp,DC=kestrel,DC=internal' }
    }

    It 'does nothing for an account already processed' {
        Mock -ModuleName ServiceDeskLab Get-ADUser -ParameterFilter { $LDAPFilter -like '*ruby.hall*' } {
            New-TestUser -Sam 'ruby.hall' -Enabled $false -Ou 'OU=Leavers,OU=Kestrel,DC=corp,DC=kestrel,DC=internal'
        }
        $result = Disable-LabLeaver -Identity ruby.hall
        $result.Message | Should -BeLike '*already processed*'
        Should -Invoke -ModuleName ServiceDeskLab Disable-ADAccount -Times 0 -Exactly
    }
}

Describe 'Remove-LabExpiredLeaver' {
    It 'deletes only disabled leavers past the retention period' {
        Mock -ModuleName ServiceDeskLab Get-ADUser {
            New-TestUser -Sam 'old.leaver' -Enabled $false -Extra @{ 'msDS-cloudExtensionAttribute3' = '2026-09-01' }
            New-TestUser -Sam 'recent.leaver' -Enabled $false -Extra @{ 'msDS-cloudExtensionAttribute3' = '2026-10-20' }
            New-TestUser -Sam 'enabled.again' -Enabled $true -Extra @{ 'msDS-cloudExtensionAttribute3' = '2026-08-01' }
            New-TestUser -Sam 'no.stamp' -Enabled $false
        }
        Mock -ModuleName ServiceDeskLab Remove-ADUser { }

        $removed = @(Remove-LabExpiredLeaver -Today '2026-10-28' -Confirm:$false)
        $removed.SamAccountName | Should -Be @('old.leaver')
        Should -Invoke -ModuleName ServiceDeskLab Remove-ADUser -Times 1 -Exactly
    }
}

Describe 'Reset-LabPassword' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Set-ADAccountPassword { }
        Mock -ModuleName ServiceDeskLab Set-ADUser { }
        Mock -ModuleName ServiceDeskLab Unlock-ADAccount { }
    }

    It 'resets, forces a change at sign-in and unlocks a locked account' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'priya.sharma' -LockedOut $true }
        $result = Reset-LabPassword -Identity priya.sharma -VerifiedBy 'Called back on the number HR holds' -Reference INC0010023
        $result.Unlocked | Should -BeTrue
        $result.TemporaryPassword | Should -BeOfType [securestring]
        Should -Invoke -ModuleName ServiceDeskLab Set-ADUser -ParameterFilter { $ChangePasswordAtLogon -eq $true }
        Should -Invoke -ModuleName ServiceDeskLab Unlock-ADAccount -Times 1 -Exactly
    }

    It 'insists on a record of how identity was checked' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'priya.sharma' }
        { Reset-LabPassword -Identity priya.sharma -VerifiedBy 'ok' } | Should -Throw
    }

    It 'refuses privileged accounts' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'adm.sofia.hussain' -Extra @{ adminCount = 1 } }
        { Reset-LabPassword -Identity adm.sofia.hussain -VerifiedBy 'Called back on the number HR holds' } | Should -Throw '*privileged*'
        Should -Invoke -ModuleName ServiceDeskLab Set-ADAccountPassword -Times 0 -Exactly
    }

    It 'refuses disabled accounts' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'ruby.hall' -Enabled $false }
        { Reset-LabPassword -Identity ruby.hall -VerifiedBy 'Called back on the number HR holds' } | Should -Throw '*disabled*'
    }
}

Describe 'Unlock-LabAccount' {
    It 'unlocks and names the computer the lockouts came from' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'priya.sharma' -LockedOut $true -Extra @{ badPwdCount = 10 } }
        Mock -ModuleName ServiceDeskLab Get-LabLockoutSource {
            [pscustomobject]@{ Account = 'priya.sharma'; CallerComputer = 'WS01' }
            [pscustomobject]@{ Account = 'priya.sharma'; CallerComputer = 'WS01' }
        }
        Mock -ModuleName ServiceDeskLab Unlock-ADAccount { }
        $result = Unlock-LabAccount -Identity priya.sharma
        $result.WasLockedOut | Should -BeTrue
        $result.LockoutSources | Should -Be @('WS01')
        $result.Message | Should -BeLike '*came from: WS01*'
        Should -Invoke -ModuleName ServiceDeskLab Unlock-ADAccount -Times 1 -Exactly
    }

    It 'says so when the account is not locked' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'priya.sharma' }
        Mock -ModuleName ServiceDeskLab Get-LabLockoutSource { }
        Mock -ModuleName ServiceDeskLab Unlock-ADAccount { }
        (Unlock-LabAccount -Identity priya.sharma).Message | Should -BeLike '*not locked*'
        Should -Invoke -ModuleName ServiceDeskLab Unlock-ADAccount -Times 0 -Exactly
    }
}

Describe 'Get-LabLockoutSource' {
    It 'reads the caller computer from event 4740' {
        Mock -ModuleName ServiceDeskLab Get-ADDomain { [pscustomobject]@{ PDCEmulator = 'DC01.corp.kestrel.internal' } }
        Mock -ModuleName ServiceDeskLab Get-WinEvent {
            $xml = '<Event><EventData><Data Name="TargetUserName">priya.sharma</Data><Data Name="TargetDomainName">WS01</Data></EventData></Event>'
            $record = [pscustomobject]@{ TimeCreated = Get-Date }
            $record | Add-Member -MemberType ScriptMethod -Name ToXml -Value { $xml }.GetNewClosure()
            $record
        }
        $events = @(Get-LabLockoutSource -Identity priya.sharma)
        $events[0].CallerComputer | Should -Be 'WS01'
        $events[0].ReportedBy | Should -Be 'DC01.corp.kestrel.internal'
    }
}
