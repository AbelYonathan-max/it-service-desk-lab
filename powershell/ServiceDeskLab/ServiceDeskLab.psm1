# Works in Windows PowerShell 5.1 (what the domain controller runs) and PowerShell 7 (what CI runs).
# The ActiveDirectory module only exists on Windows with RSAT or the AD DS role. Tests replace its
# cmdlets with stubs, so a missing module is not an error at import time, and the tests set
# SDLAB_SKIP_AD_IMPORT so a machine that happens to have RSAT still uses the stubs.
if ($env:SDLAB_SKIP_AD_IMPORT -ne '1' -and (Get-Module -ListAvailable -Name ActiveDirectory)) {
    Import-Module ActiveDirectory -ErrorAction Stop -Verbose:$false
}

$script:ModuleRoot = $PSScriptRoot

foreach ($folder in 'Private', 'Public') {
    foreach ($file in Get-ChildItem -Path (Join-Path $PSScriptRoot $folder) -Filter '*.ps1') {
        . $file.FullName
    }
}

Export-ModuleMember -Function (Get-ChildItem -Path (Join-Path $PSScriptRoot 'Public') -Filter '*.ps1').BaseName
