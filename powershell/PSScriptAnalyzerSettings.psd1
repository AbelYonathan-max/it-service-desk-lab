# Settings for PSScriptAnalyzer, the PowerShell linter. CI runs it on the module and the lab
# scripts and fails on any warning or error:
#   Invoke-ScriptAnalyzer -Path powershell/ServiceDeskLab -Recurse -Settings powershell/PSScriptAnalyzerSettings.psd1
# Any exception is made in the code itself, with a SuppressMessage attribute and a reason.
@{
    Severity = @('Error', 'Warning')
    Rules    = @{
        # The domain controller runs Windows PowerShell 5.1; CI and admin machines run PowerShell 7.
        PSUseCompatibleSyntax = @{
            Enable         = $true
            TargetVersions = @('5.1', '7.0')
        }
    }
}
