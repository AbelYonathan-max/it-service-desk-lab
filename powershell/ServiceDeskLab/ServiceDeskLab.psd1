@{
    RootModule        = 'ServiceDeskLab.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '5d0f5d3e-6a51-4d55-9f43-2b8d0a7e1c61'
    Author            = 'AbelYonathan-max'
    Description       = 'Service desk automation for the Kestrel Borough Council lab: joiners, movers and leavers, password resets, account unlocks, shared drive access, account hygiene, and ServiceNow request fulfilment.'
    PowerShellVersion = '5.1'
    FunctionsToExport = @(
        'Disable-LabLeaver'
        'Get-LabAccessReport'
        'Get-LabAccountHygiene'
        'Get-LabConfig'
        'Get-LabLockoutSource'
        'Grant-LabShareAccess'
        'Invoke-LabFulfilment'
        'New-LabStarter'
        'Remove-LabExpiredLeaver'
        'Reset-LabPassword'
        'Revoke-LabShareAccess'
        'Set-LabMover'
        'Sync-LabServiceNowUser'
        'Unlock-LabAccount'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags       = @('ActiveDirectory', 'ServiceDesk', 'ITIL', 'ServiceNow', 'JoinersMoversLeavers')
            ProjectUri = 'https://github.com/AbelYonathan-max/it-service-desk-lab'
        }
    }
}
