BeforeAll {
    . (Join-Path $PSScriptRoot 'TestSetup.ps1')
}

Describe 'Grant-LabShareAccess' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'ryan.ward' -Department 'Housing' -MemberOf 'GG-Dept-Housing', 'DL-FS-Finance-RO' }
        Mock -ModuleName ServiceDeskLab Add-ADGroupMember { }
    }

    It 'adds the user to the share''s modify group' {
        $result = Grant-LabShareAccess -Identity ryan.ward -Share Planning -Access Modify -Reference RITM0010019
        $result.Group | Should -Be 'DL-FS-Planning-RW'
        Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 1 -Exactly -ParameterFilter { $Identity -eq 'DL-FS-Planning-RW' -and $Members -eq 'ryan.ward' }
    }

    It 'does nothing when the access is already there' {
        $result = Grant-LabShareAccess -Identity ryan.ward -Share Finance -Access Read
        $result.Changed | Should -BeFalse
        Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 0 -Exactly
    }

    It 'needs the data owner''s approval for special category data' {
        { Grant-LabShareAccess -Identity ryan.ward -Share HR -Access Read } | Should -Throw '*approval*'
        Grant-LabShareAccess -Identity ryan.ward -Share HR -Access Read -ApprovedBy chloe.bailey | Out-Null
        Should -Invoke -ModuleName ServiceDeskLab Add-ADGroupMember -Times 1 -Exactly -ParameterFilter { $Identity -eq 'DL-FS-HR-RO' }
    }

    It 'refuses an unknown share' {
        { Grant-LabShareAccess -Identity ryan.ward -Share Marketing -Access Read } | Should -Throw '*Unknown shared drive*'
    }
}

Describe 'Revoke-LabShareAccess' {
    It 'removes direct grants and says when department access remains' {
        Mock -ModuleName ServiceDeskLab Get-ADUser { New-TestUser -Sam 'ryan.ward' -Department 'Housing' -MemberOf 'GG-Dept-Housing', 'DL-FS-Housing-RO' }
        Mock -ModuleName ServiceDeskLab Remove-ADGroupMember { }
        $result = Revoke-LabShareAccess -Identity ryan.ward -Share Housing
        $result.GroupsRemoved | Should -Be @('DL-FS-Housing-RO')
        $result.StillHasAccess | Should -BeTrue
        $result.Message | Should -BeLike '*Still has access through GG-Dept-Housing*'
    }
}

Describe 'Get-LabAccessReport' {
    It 'lists people with access and the route they have it through' {
        Mock -ModuleName ServiceDeskLab Get-ADGroupMember -ParameterFilter { $Identity -eq 'DL-FS-HR-RW' } {
            [pscustomobject]@{ objectClass = 'group'; SamAccountName = 'GG-Dept-HRPayroll'; Name = 'GG-Dept-HRPayroll' }
        }
        Mock -ModuleName ServiceDeskLab Get-ADGroupMember -ParameterFilter { $Identity -eq 'DL-FS-HR-RO' } {
            [pscustomobject]@{ objectClass = 'user'; SamAccountName = 'nadia.evans'; Name = 'Nadia Evans' }
        }
        Mock -ModuleName ServiceDeskLab Get-ADGroupMember -ParameterFilter { $Identity -eq 'GG-Dept-HRPayroll' -and $Recursive } {
            [pscustomobject]@{ objectClass = 'user'; SamAccountName = 'chloe.bailey'; Name = 'Chloe Bailey' }
        }
        $rows = @(Get-LabAccessReport -Share HR)
        $rows | Should -HaveCount 2
        ($rows | Where-Object SamAccountName -EQ 'chloe.bailey').Via | Should -Be 'GG-Dept-HRPayroll'
        ($rows | Where-Object SamAccountName -EQ 'nadia.evans').Via | Should -Be 'Direct grant'
        ($rows | Where-Object SamAccountName -EQ 'nadia.evans').Access | Should -Be 'Read'
        $rows[0].Restricted | Should -BeTrue
    }
}

Describe 'Get-LabAccountHygiene' {
    It 'finds each kind of problem' {
        $today = [datetime]'2026-10-28'
        Mock -ModuleName ServiceDeskLab Get-ADUser {
            New-TestUser -Sam 'ok.user' -Extra @{ LastLogonDate = $today.AddDays(-2); whenCreated = $today.AddYears(-1) }
            New-TestUser -Sam 'stale.user' -Extra @{ LastLogonDate = $today.AddDays(-120); whenCreated = $today.AddYears(-2) }
            New-TestUser -Sam 'never.user' -Extra @{ LastLogonDate = $null; whenCreated = $today.AddDays(-45) }
            New-TestUser -Sam 'ending.user' -Extra @{ LastLogonDate = $today.AddDays(-1); whenCreated = $today.AddYears(-1); AccountExpirationDate = $today.AddDays(7); employeeType = 'Temporary' }
            New-TestUser -Sam 'expired.user' -Extra @{ LastLogonDate = $today.AddDays(-20); whenCreated = $today.AddYears(-1); AccountExpirationDate = $today.AddDays(-3) }
            New-TestUser -Sam 'disabled.user' -Enabled $false -Extra @{ LastLogonDate = $today.AddDays(-30); whenCreated = $today.AddYears(-1) }
        }
        $findings = @(Get-LabAccountHygiene -Today $today)
        ($findings | Where-Object SamAccountName -EQ 'stale.user').Category | Should -Be 'Stale'
        ($findings | Where-Object SamAccountName -EQ 'never.user').Category | Should -Be 'NeverSignedIn'
        ($findings | Where-Object SamAccountName -EQ 'ending.user').Category | Should -Be 'ContractEnding'
        ($findings | Where-Object SamAccountName -EQ 'expired.user').Category | Should -Be 'ExpiredButEnabled'
        ($findings | Where-Object SamAccountName -EQ 'disabled.user').Category | Should -Be 'DisabledOutsideLeavers'
        $findings.SamAccountName | Should -Not -Contain 'ok.user'
    }
}
