BeforeAll {
    . (Join-Path $PSScriptRoot 'TestSetup.ps1')
}

Describe 'ConvertTo-LabAscii' {
    It 'drops accents, apostrophes and spaces but keeps hyphens' {
        InModuleScope ServiceDeskLab {
            # Built from code points so this file stays ASCII for Windows PowerShell 5.1.
            $zoe = 'Zo' + [char]0x00EB
            $siobhan = 'Siobh' + [char]0x00E1 + 'n'
            $garcia = 'Garc' + [char]0x00ED + 'a Fern' + [char]0x00E1 + 'ndez'
            ConvertTo-LabAscii -Text $zoe | Should -Be 'zoe'
            ConvertTo-LabAscii -Text $siobhan | Should -Be 'siobhan'
            ConvertTo-LabAscii -Text "O'Brien" | Should -Be 'obrien'
            ConvertTo-LabAscii -Text $garcia | Should -Be 'garciafernandez'
            ConvertTo-LabAscii -Text 'Smith-Jones' | Should -Be 'smith-jones'
            ConvertTo-LabAscii -Text ('S' + [char]0x00F8 + 'ren') | Should -Be 'soren'
        }
    }
}

Describe 'New-LabAccountName' {
    It 'uses firstname.lastname' {
        InModuleScope ServiceDeskLab {
            $name = New-LabAccountName -FirstName 'Amara' -LastName 'Okafor' -Exists { $false }
            $name.SamAccountName | Should -Be 'amara.okafor'
            $name.UpnPrefix | Should -Be 'amara.okafor'
        }
    }

    It 'numbers a clash, starting at 2' {
        InModuleScope ServiceDeskLab {
            $taken = @('james.smith')
            $name = New-LabAccountName -FirstName 'James' -LastName 'Smith' -Exists { param($sam, $upn) $taken -contains $sam -or $taken -contains $upn }
            $name.SamAccountName | Should -Be 'james.smith2'
            $name.UpnPrefix | Should -Be 'james.smith2'
        }
    }

    It 'keeps the pre-Windows 2000 name within 20 characters, and the UPN in full' {
        InModuleScope ServiceDeskLab {
            $name = New-LabAccountName -FirstName 'Christopher' -LastName 'Montgomery-Wellington' -Exists { $false }
            $name.SamAccountName | Should -Be 'c.montgomery-welling'
            $name.SamAccountName.Length | Should -BeLessOrEqual 20
            $name.UpnPrefix | Should -Be 'christopher.montgomery-wellington'
        }
    }

    It 'still fits 20 characters when a long name clashes' {
        InModuleScope ServiceDeskLab {
            $taken = @('c.montgomery-welling')
            $name = New-LabAccountName -FirstName 'Christopher' -LastName 'Montgomery-Wellington' -Exists { param($sam) $taken -contains $sam }
            $name.SamAccountName | Should -Be 'c.montgomery-wellin2'
        }
    }

    It 'refuses a name with no usable letters' {
        InModuleScope ServiceDeskLab {
            { New-LabAccountName -FirstName '!!' -LastName 'Smith' -Exists { $false } } | Should -Throw '*Cannot make an account name*'
        }
    }

    It 'gives every person in people.csv a unique, valid account name' {
        $people = Import-Csv -LiteralPath (Join-Path (Split-Path $env:SDLAB_CONFIG) 'people.csv') -Encoding UTF8
        $people.Count | Should -BeGreaterThan 50
        InModuleScope ServiceDeskLab -Parameters @{ People = $people } {
            param($People)
            $sams = New-Object System.Collections.Generic.List[string]
            $upns = New-Object System.Collections.Generic.List[string]
            foreach ($person in $People) {
                $name = New-LabAccountName -FirstName $person.FirstName -LastName $person.LastName -Exists {
                    param($sam, $upn) $sams.Contains($sam) -or $upns.Contains($upn)
                }
                $sams.Add($name.SamAccountName)
                $upns.Add($name.UpnPrefix)
            }
            ($sams | Select-Object -Unique).Count | Should -Be $People.Count
            $sams | ForEach-Object { $_ | Should -Match '^[a-z0-9][a-z0-9.-]{0,19}$' }
            $sams | Should -Contain 'james.smith2'
        }
    }
}

Describe 'New-LabPassword' {
    It 'makes long passphrases that meet the AD complexity rules' {
        InModuleScope ServiceDeskLab {
            1..50 | ForEach-Object {
                $password = New-LabPassword
                $password.Length | Should -BeGreaterOrEqual 14
                $password | Should -MatchExactly '[A-Z]'
                $password | Should -MatchExactly '[a-z]'
                $password | Should -Match '[0-9]'
                $password | Should -Match '[^A-Za-z0-9]'
            }
        }
    }

    It 'does not repeat itself' {
        InModuleScope ServiceDeskLab {
            $many = 1..200 | ForEach-Object { New-LabPassword }
            ($many | Select-Object -Unique).Count | Should -BeGreaterThan 195
        }
    }

    It 'can hand the passphrase over as a SecureString' {
        InModuleScope ServiceDeskLab {
            $secure = New-LabPassword -Words 4 -AsSecureString
            $secure | Should -BeOfType [securestring]
            (ConvertFrom-LabSecureString -SecureString $secure).Split('-').Count | Should -Be 5
        }
    }
}

Describe 'Escaping' {
    It 'escapes LDAP filter values' {
        InModuleScope ServiceDeskLab {
            ConvertTo-LabLdapFilterValue -Value 'a*b(c)\d' | Should -Be 'a\2ab\28c\29\5cd'
            ConvertTo-LabLdapFilterValue -Value "O'Brien" | Should -Be "O'Brien"
        }
    }

    It 'escapes distinguished name values' {
        InModuleScope ServiceDeskLab {
            ConvertTo-LabDnValue -Value 'Smith, John' | Should -Be 'Smith\, John'
            ConvertTo-LabDnValue -Value '#1 Team' | Should -Be '\#1 Team'
        }
    }

    It 'reads group names back out of MemberOf, including escaped commas' {
        InModuleScope ServiceDeskLab {
            $names = Get-LabGroupName -MemberOf @('CN=GG-All-Staff,OU=Groups,DC=x', 'CN=Team\, North,OU=Groups,DC=x')
            $names | Should -Be @('GG-All-Staff', 'Team, North')
        }
    }
}

Describe 'Get-LabConfig' {
    It 'works out the OUs from the domain name' {
        $config = Get-LabConfig -Force
        $config.DomainDn | Should -Be 'DC=corp,DC=kestrel,DC=internal'
        $config.Ou.Staff | Should -Be 'OU=Staff,OU=Kestrel,DC=corp,DC=kestrel,DC=internal'
        $config.Ou.ResourceGroups | Should -Be 'OU=Resource,OU=Groups,OU=Kestrel,DC=corp,DC=kestrel,DC=internal'
    }

    It 'gives each department an OU and a group, and each share two groups' {
        $config = Get-LabConfig
        $housing = $config.Departments | Where-Object Code -EQ 'HSG'
        $housing.OuDn | Should -Be 'OU=Housing,OU=Staff,OU=Kestrel,DC=corp,DC=kestrel,DC=internal'
        $housing.Group | Should -Be 'GG-Dept-Housing'
        $hr = $config.Shares | Where-Object Name -EQ 'HR'
        $hr.ReadGroup | Should -Be 'DL-FS-HR-RO'
        $hr.ModifyGroup | Should -Be 'DL-FS-HR-RW'
        $hr.Restricted | Should -BeTrue
    }

    It 'takes the UPN suffix from the environment' {
        (Get-LabConfig).UpnSuffix | Should -Be 'kestrel.example'
    }

    It 'has a department, owner and editor group that all exist for every share' {
        $config = Get-LabConfig
        $people = Import-Csv -LiteralPath (Join-Path (Split-Path $env:SDLAB_CONFIG) 'people.csv') -Encoding UTF8
        foreach ($share in $config.Shares) {
            $people.EmployeeId | Should -Contain $share.OwnerEmployeeId
            foreach ($group in $share.Editors) { $config.Departments.Group | Should -Contain $group }
        }
        foreach ($dept in $config.Departments) { $config.Shares.Name | Should -Contain $dept.Share }
        foreach ($person in $people) { $config.Departments.Code | Should -Contain $person.DepartmentCode }
    }
}
