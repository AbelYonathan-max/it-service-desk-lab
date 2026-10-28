function Invoke-LabFulfilment {
    <#
    .SYNOPSIS
    The fulfilment agent: carries out approved ServiceNow requests in Active Directory.

    .DESCRIPTION
    Runs every two minutes on the domain controller as a scheduled task, under a group managed
    service account that has delegated rights on the staff OUs and nothing else. Each run:
    1. Enables future starters whose start date has arrived.
    2. Reads open catalogue tasks assigned to "Identity and Access Automation" whose request has
       been approved, oldest first.
    3. Does the work (starter, leaver, mover or shared drive access), writes what it did into the
       task's work notes and closes it.
    4. Leaves dated work (a leaver's last day, a move's effective date) open until it is due.
    5. Hands anything that fails to the Identity and Access team with the error, so nothing is
       lost and a person picks it up within the request's SLA.
    Every step is safe to repeat, so a task interrupted half way is finished on the next run.

    .EXAMPLE
    Invoke-LabFulfilment -Verbose
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param(
        [ValidateRange(1, 100)][int]$MaxTasks = 25,
        [datetime]$Now = (Get-Date)
    )

    $config = Get-LabConfig
    foreach ($enabled in @(Enable-LabDueStarter -Today $Now.Date)) {
        [pscustomobject]@{ Task = $null; Request = $null; Kind = 'StarterEnabled'; Outcome = 'Done'; Note = "Enabled $($enabled.SamAccountName) on the start date." }
    }

    $group = $config.ServiceNow.automationGroup
    $tasks = @(Invoke-LabSnApi -Path '/api/now/table/sc_task' -Query @{
            sysparm_query                  = "assignment_group.name=$group^active=true^stateIN1,2^request_item.approval=approved^ORDERBYsys_created_on"
            sysparm_fields                 = 'sys_id,number,state,due_date,request_item'
            sysparm_exclude_reference_link = 'true'
            sysparm_limit                  = [string]$MaxTasks
        })
    Write-Verbose "$($tasks.Count) task(s) waiting."

    foreach ($task in $tasks) {
        $item = Invoke-LabSnApi -Path "/api/now/table/sc_req_item/$($task.request_item)" -Query @{
            sysparm_fields                 = 'sys_id,number,cat_item.name'
            sysparm_exclude_reference_link = 'true'
        }
        $catalogItem = $item.'cat_item.name'
        $kind = $config.ServiceNow.catalogItems.$catalogItem

        if (-not $PSCmdlet.ShouldProcess("$($task.number) ($catalogItem)", 'Fulfil')) { continue }
        try {
            if (-not $kind) { throw "No automation exists for '$catalogItem'." }
            $variables = Get-LabSnRequestVariable -RequestItemSysId $item.sys_id
            $outcome = switch ($kind) {
                'Starter' { Invoke-LabStarterTask -Item $item -Variables $variables }
                'Leaver' { Invoke-LabLeaverTask -Item $item -Variables $variables -Now $Now }
                'Mover' { Invoke-LabMoverTask -Item $item -Variables $variables -Now $Now }
                'ShareAccess' { Invoke-LabShareAccessTask -Item $item -Variables $variables }
            }

            if (-not $outcome.Done) {
                if (-not $task.due_date) {
                    $null = Update-LabSnTask -SysId $task.sys_id -Fields @{
                        due_date   = $outcome.Due.ToString('yyyy-MM-dd HH:mm:ss')
                        work_notes = "Scheduled: the fulfilment agent will do this on $($outcome.Due.ToString('dddd d MMMM yyyy, HH:mm'))."
                    }
                }
                [pscustomobject]@{ Task = $task.number; Request = $item.number; Kind = $kind; Outcome = 'Waiting'; Note = "Due $($outcome.Due.ToString('yyyy-MM-dd HH:mm'))" }
                continue
            }

            $null = Update-LabSnTask -SysId $task.sys_id -Fields @{
                state       = '3'
                work_notes  = "Fulfilment agent ($env:COMPUTERNAME): $($outcome.Note)"
                close_notes = $outcome.Note
            }
            [pscustomobject]@{ Task = $task.number; Request = $item.number; Kind = $kind; Outcome = 'Done'; Note = $outcome.Note }
        }
        catch {
            $reason = $_.Exception.Message
            Write-LabAudit -Action 'FulfilmentFailed' -Target $task.number -Reference $item.number -Level Error -Detail @{ error = $reason }
            try {
                $null = Update-LabSnTask -SysId $task.sys_id -ByDisplayValue -Fields @{
                    assignment_group = $config.ServiceNow.fallbackGroup
                    state            = 'Open'
                    work_notes       = "The fulfilment agent could not complete this: $reason Passed to $($config.ServiceNow.fallbackGroup) to finish by hand."
                }
            }
            catch {
                Write-Warning "Could not hand $($task.number) over: $($_.Exception.Message)"
            }
            [pscustomobject]@{ Task = $task.number; Request = $item.number; Kind = $kind; Outcome = 'HandedOver'; Note = $reason }
        }
    }
}
