BeforeAll {
    . (Join-Path $PSScriptRoot 'TestSetup.ps1')
    $env:SDLAB_SN_INSTANCE = 'dev00000'
    $env:SDLAB_SN_USERNAME = 'svc.fulfilment'
    $env:SDLAB_SN_PASSWORD = 'not-a-real-password'
    # No test may reach the network: a web call that is not mocked fails here instead.
    Mock -ModuleName ServiceDeskLab Invoke-RestMethod { throw "Unmocked web call to $Uri" }
}

AfterAll {
    Remove-Item Env:SDLAB_SN_INSTANCE, Env:SDLAB_SN_USERNAME, Env:SDLAB_SN_PASSWORD -ErrorAction SilentlyContinue
}

Describe 'Invoke-LabSnApi' {
    BeforeEach {
        InModuleScope ServiceDeskLab { $script:SnConnection = $null }
        Mock -ModuleName ServiceDeskLab Start-Sleep { }
    }

    It 'builds the URL, signs in with basic auth and returns the result' {
        Mock -ModuleName ServiceDeskLab Invoke-RestMethod { [pscustomobject]@{ result = @([pscustomobject]@{ number = 'SCTASK0010001' }) } }
        $result = InModuleScope ServiceDeskLab { Invoke-LabSnApi -Path '/api/now/table/sc_task' -Query @{ sysparm_query = 'state=1^active=true'; sysparm_limit = '5' } }
        $result.number | Should -Be 'SCTASK0010001'
        Should -Invoke -ModuleName ServiceDeskLab Invoke-RestMethod -Times 1 -Exactly -ParameterFilter {
            $Uri -eq 'https://dev00000.service-now.com/api/now/table/sc_task?sysparm_limit=5&sysparm_query=state%3D1%5Eactive%3Dtrue' -and
            $Headers.Authorization -eq ('Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('svc.fulfilment:not-a-real-password')))
        }
    }

    It 'explains a sleeping developer instance' {
        Mock -ModuleName ServiceDeskLab Invoke-RestMethod { '<html><body>Your instance is hibernating</body></html>' }
        { InModuleScope ServiceDeskLab { Invoke-LabSnApi -Path '/api/now/table/sys_user' } } | Should -Throw '*asleep*'
    }

    It 'sends JSON bodies as UTF-8' {
        Mock -ModuleName ServiceDeskLab Invoke-RestMethod { [pscustomobject]@{ result = $null } }
        InModuleScope ServiceDeskLab { $null = Invoke-LabSnApi -Method PATCH -Path '/api/now/table/sc_task/abc' -Body @{ work_notes = 'Done' } }
        Should -Invoke -ModuleName ServiceDeskLab Invoke-RestMethod -ParameterFilter {
            $Method -eq 'PATCH' -and [Text.Encoding]::UTF8.GetString($Body) -eq '{"work_notes":"Done"}'
        }
    }
}

Describe 'Get-LabSnRequestVariable' {
    It 'turns the variable rows into a hashtable' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi {
            [pscustomobject]@{ 'sc_item_option.item_option_new.name' = 'first_name'; 'sc_item_option.value' = 'Amara' }
            [pscustomobject]@{ 'sc_item_option.item_option_new.name' = 'department'; 'sc_item_option.value' = 'HSG' }
        }
        $variables = InModuleScope ServiceDeskLab { Get-LabSnRequestVariable -RequestItemSysId 'ritm1' }
        $variables.first_name | Should -Be 'Amara'
        $variables.department | Should -Be 'HSG'
    }
}

Describe 'Invoke-LabFulfilment' {
    BeforeEach {
        Mock -ModuleName ServiceDeskLab Enable-LabDueStarter { }
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_task' } {
            [pscustomobject]@{ sys_id = 'task1'; number = 'SCTASK0010001'; state = '1'; due_date = ''; request_item = 'ritm1' }
        }
        Mock -ModuleName ServiceDeskLab Update-LabSnTask { }
    }

    It 'creates a starter and closes the task with what it did' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010001'; 'cat_item.name' = 'New starter' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnRequestVariable {
            @{ first_name = 'Amara'; last_name = 'Okafor'; department = 'HSG'; job_title = 'Housing Officer'; employee_id = 'E10101'
                start_date = '2026-11-02'; contract_type = 'permanent'; line_manager = 'mgr1' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnUserName { 'lucas.murphy' }
        Mock -ModuleName ServiceDeskLab New-LabStarter {
            [pscustomobject]@{ Created = $true; SamAccountName = 'amara.okafor'; Groups = @('GG-Dept-Housing'); Message = 'Created amara.okafor@kestrel.example in Housing.'; TemporaryPassword = (ConvertTo-SecureString 'x' -AsPlainText -Force) }
        }

        $outcome = @(Invoke-LabFulfilment -Now '2026-10-28 10:00')
        $outcome[0].Outcome | Should -Be 'Done'
        Should -Invoke -ModuleName ServiceDeskLab New-LabStarter -ParameterFilter {
            $Department -eq 'HSG' -and $Manager -eq 'lucas.murphy' -and $StartDate -eq [datetime]'2026-11-02' -and $Reference -eq 'RITM0010001' -and $ContractType -eq 'Permanent'
        }
        Should -Invoke -ModuleName ServiceDeskLab Update-LabSnTask -Times 1 -Exactly -ParameterFilter {
            $SysId -eq 'task1' -and $Fields.state -eq '3' -and $Fields.work_notes -like '*Created amara.okafor*'
        }
    }

    It 'leaves a leaver alone until the end of their last day, and says when it will run' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010012'; 'cat_item.name' = 'Leaver' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnRequestVariable { @{ leaver = 'user1'; last_working_day = '2026-11-27' } }
        Mock -ModuleName ServiceDeskLab Disable-LabLeaver { }

        $outcome = @(Invoke-LabFulfilment -Now '2026-11-27 12:00')
        $outcome[0].Outcome | Should -Be 'Waiting'
        Should -Invoke -ModuleName ServiceDeskLab Disable-LabLeaver -Times 0 -Exactly
        Should -Invoke -ModuleName ServiceDeskLab Update-LabSnTask -ParameterFilter { $Fields.due_date -eq '2026-11-27 17:30:00' }
    }

    It 'processes a leaver after 17:30 on the last day' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010012'; 'cat_item.name' = 'Leaver' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnRequestVariable { @{ leaver = 'user1'; last_working_day = '2026-11-27' } }
        Mock -ModuleName ServiceDeskLab Get-LabSnUserName { 'ruby.hall' }
        Mock -ModuleName ServiceDeskLab Disable-LabLeaver { [pscustomobject]@{ Message = 'Disabled ruby.hall.' } }

        $outcome = @(Invoke-LabFulfilment -Now '2026-11-27 17:31')
        $outcome[0].Outcome | Should -Be 'Done'
        Should -Invoke -ModuleName ServiceDeskLab Disable-LabLeaver -ParameterFilter { $Identity -eq 'ruby.hall' -and $LeavingDate -eq [datetime]'2026-11-27' }
    }

    It 'passes the data owner''s approval to a shared drive request' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010019'; 'cat_item.name' = 'Shared drive access' }
        }
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sysapproval_approver' } {
            [pscustomobject]@{ 'approver.user_name' = 'chloe.bailey' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnRequestVariable { @{ user = 'user1'; share = 'HR'; access_level = 'read' } }
        Mock -ModuleName ServiceDeskLab Get-LabSnUserName { 'ryan.ward' }
        Mock -ModuleName ServiceDeskLab Grant-LabShareAccess { [pscustomobject]@{ Message = 'Added ryan.ward to DL-FS-HR-RO.' } }

        Invoke-LabFulfilment | Out-Null
        Should -Invoke -ModuleName ServiceDeskLab Grant-LabShareAccess -ParameterFilter { $ApprovedBy -eq 'chloe.bailey' -and $Access -eq 'Read' -and $Share -eq 'HR' }
        Should -Invoke -ModuleName ServiceDeskLab Update-LabSnTask -ParameterFilter { $Fields.work_notes -like '*Approved by chloe.bailey*' }
    }

    It 'hands a failed task to people, with the reason' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010001'; 'cat_item.name' = 'New starter' }
        }
        Mock -ModuleName ServiceDeskLab Get-LabSnRequestVariable { @{ first_name = 'Amara'; last_name = 'Okafor'; department = 'Marketing'; job_title = 'x'; employee_id = 'E10101' } }

        $outcome = @(Invoke-LabFulfilment)
        $outcome[0].Outcome | Should -Be 'HandedOver'
        $outcome[0].Note | Should -BeLike '*Unknown department*'
        Should -Invoke -ModuleName ServiceDeskLab Update-LabSnTask -ParameterFilter {
            $ByDisplayValue -and $Fields.assignment_group -eq 'Identity and Access' -and $Fields.work_notes -like '*could not complete*Unknown department*'
        }
    }

    It 'hands over catalogue items it has no automation for' {
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Path -eq '/api/now/table/sc_req_item/ritm1' } {
            [pscustomobject]@{ sys_id = 'ritm1'; number = 'RITM0010030'; 'cat_item.name' = 'New laptop' }
        }
        $outcome = @(Invoke-LabFulfilment)
        $outcome[0].Outcome | Should -Be 'HandedOver'
        $outcome[0].Note | Should -BeLike "*No automation exists for 'New laptop'*"
    }
}

Describe 'Sync-LabServiceNowUser' {
    It 'creates missing users, updates changed ones and sets line managers' {
        Mock -ModuleName ServiceDeskLab Get-ADUser {
            $config = Get-LabConfig
            [pscustomobject]@{ SamAccountName = 'lucas.murphy'; GivenName = 'Lucas'; Surname = 'Murphy'; EmailAddress = 'lucas.murphy@kestrel.example'; Title = 'Head of Housing'
                Department = 'Housing'; Manager = $null; EmployeeID = 'E10015'; Enabled = $true; Office = 'Town Hall'; DistinguishedName = "CN=Lucas Murphy,OU=Housing,$($config.Ou.Staff)" }
            [pscustomobject]@{ SamAccountName = 'amara.okafor'; GivenName = 'Amara'; Surname = 'Okafor'; EmailAddress = 'amara.okafor@kestrel.example'; Title = 'Housing Officer'
                Department = 'Housing'; Manager = "CN=Lucas Murphy,OU=Housing,$($config.Ou.Staff)"; EmployeeID = 'E10101'; Enabled = $true; Office = 'Civic Centre'; DistinguishedName = "CN=Amara Okafor,OU=Housing,$($config.Ou.Staff)" }
        }
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { -not $Method -or $Method -eq 'GET' } {
            [pscustomobject]@{ sys_id = 'sn-lucas'; user_name = 'lucas.murphy'; first_name = 'Lucas'; last_name = 'Murphy'; email = 'lucas.murphy@kestrel.example'
                title = 'Housing Manager'; employee_number = 'E10015'; active = 'true'; manager = ''; 'department.name' = 'Housing'; 'location.name' = 'Town Hall' }
        }
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Method -eq 'POST' } { [pscustomobject]@{ sys_id = 'sn-amara' } }
        Mock -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Method -eq 'PATCH' } { }

        $summary = Sync-LabServiceNowUser
        $summary.Created | Should -Be 1
        $summary.Updated | Should -Be 1
        $summary.ManagersSet | Should -Be 1
        Should -Invoke -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Method -eq 'POST' -and $Body.user_name -eq 'amara.okafor' -and $Body.department -eq 'Housing' }
        Should -Invoke -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Method -eq 'PATCH' -and $Path -like '*sn-lucas' -and $Body.title -eq 'Head of Housing' -and $Body.Count -eq 1 }
        Should -Invoke -ModuleName ServiceDeskLab Invoke-LabSnApi -ParameterFilter { $Method -eq 'PATCH' -and $Path -like '*sn-amara' -and $Body.manager -eq 'sn-lucas' }
    }
}
