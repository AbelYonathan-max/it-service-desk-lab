# Handlers for each automated catalogue item. Each takes the requested item (RITM) and its
# variables and returns @{ Done = $true; Note = '...' }, or @{ Done = $false; Due = <date> } when
# the work is dated in the future and must wait.

function Invoke-LabStarterTask {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Item, [Parameter(Mandatory = $true)][hashtable]$Variables)

    $v = $Variables
    $starter = @{
        FirstName    = $v['first_name']
        LastName     = $v['last_name']
        Department   = $v['department']
        JobTitle     = $v['job_title']
        EmployeeId   = $v['employee_id']
        ContractType = if ($v['contract_type']) { (Get-Culture).TextInfo.ToTitleCase($v['contract_type']) } else { 'Permanent' }
        StartDate    = ConvertFrom-LabSnDate $v['start_date']
        Reference    = $Item.number
    }
    if ($v['end_date']) { $starter.EndDate = ConvertFrom-LabSnDate $v['end_date'] }
    if ($v['office']) { $starter.Office = $v['office'] }
    if ($v['line_manager']) { $starter.Manager = Get-LabSnUserName -SysId $v['line_manager'] }
    if (-not $starter.StartDate) { $starter.StartDate = (Get-Date).Date }

    $result = New-LabStarter @starter
    $note = $result.Message
    if ($result.Created) {
        $homeFolder = Initialize-LabHomeFolder -SamAccountName $result.SamAccountName
        if ($homeFolder) { $note += " Home folder created ($homeFolder)." }
        if ($env:SDLAB_KEYVAULT) {
            $secretName = ConvertTo-LabSecretName -Prefix 'starter' -SamAccountName $result.SamAccountName
            $expires = Set-LabKeyVaultSecret -VaultName $env:SDLAB_KEYVAULT -Name $secretName -Value $result.TemporaryPassword `
                -ExpiresInDays 14 -Tags @{ ticket = $Item.number; account = $result.SamAccountName }
            $note += " Temporary password saved in Key Vault as '$secretName' until $($expires.ToString('dd MMM yyyy')). Give it to the line manager after checking who they are; it must be changed at first sign-in."
        }
        $note += " Groups: $($result.Groups -join ', ')."
    }
    @{ Done = $true; Note = $note }
}

function Invoke-LabLeaverTask {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Item, [Parameter(Mandatory = $true)][hashtable]$Variables, [datetime]$Now = (Get-Date))

    $lastDay = ConvertFrom-LabSnDate $Variables['last_working_day']
    if (-not $lastDay) { $lastDay = $Now.Date }
    # Access ends at the close of the last working day, not before.
    $due = $lastDay.Date.AddHours(17).AddMinutes(30)
    if ($Now -lt $due) { return @{ Done = $false; Due = $due } }

    $identity = Get-LabSnUserName -SysId $Variables['leaver']
    $result = Disable-LabLeaver -Identity $identity -LeavingDate $lastDay -Reference $Item.number
    @{ Done = $true; Note = $result.Message }
}

function Invoke-LabMoverTask {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Item, [Parameter(Mandatory = $true)][hashtable]$Variables, [datetime]$Now = (Get-Date))

    $effective = ConvertFrom-LabSnDate $Variables['effective_date']
    if ($effective -and $Now.Date -lt $effective) { return @{ Done = $false; Due = $effective } }

    $move = @{
        Identity   = Get-LabSnUserName -SysId $Variables['employee']
        Department = $Variables['new_department']
        Reference  = $Item.number
    }
    if ($Variables['new_job_title']) { $move.JobTitle = $Variables['new_job_title'] }
    if ($Variables['new_line_manager']) { $move.Manager = Get-LabSnUserName -SysId $Variables['new_line_manager'] }
    $result = Set-LabMover @move
    @{ Done = $true; Note = $result.Message }
}

function Invoke-LabShareAccessTask {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Item, [Parameter(Mandatory = $true)][hashtable]$Variables)

    # The data owner's approval, recorded in ServiceNow before the task reached the queue.
    $approvals = @(Invoke-LabSnApi -Path '/api/now/table/sysapproval_approver' -Query @{
            sysparm_query                  = "sysapproval=$($Item.sys_id)^state=approved"
            sysparm_fields                 = 'approver.user_name,sys_updated_on'
            sysparm_exclude_reference_link = 'true'
        })
    $approvedBy = ($approvals | ForEach-Object { $_.'approver.user_name' } | Where-Object { $_ }) -join ', '

    $grant = @{
        Identity  = Get-LabSnUserName -SysId $Variables['user']
        Share     = $Variables['share']
        Access    = if ($Variables['access_level'] -eq 'modify') { 'Modify' } else { 'Read' }
        Reference = $Item.number
    }
    if ($approvedBy) { $grant.ApprovedBy = $approvedBy }
    $result = Grant-LabShareAccess @grant
    $note = $result.Message
    if ($approvedBy) { $note += " Approved by $approvedBy." }
    @{ Done = $true; Note = $note }
}
