BeforeAll {
    . (Join-Path $PSScriptRoot 'TestSetup.ps1')
    $moduleRoot = Join-Path (Split-Path $PSScriptRoot) 'ServiceDeskLab'
    $repoRoot = Split-Path (Split-Path $PSScriptRoot)
}

Describe 'ServiceDeskLab module' {
    It 'has a valid manifest' {
        { Test-ModuleManifest -Path (Join-Path $moduleRoot 'ServiceDeskLab.psd1') -ErrorAction Stop } | Should -Not -Throw
    }

    It 'exports exactly the functions in Public' {
        $public = (Get-ChildItem (Join-Path $moduleRoot 'Public') -Filter '*.ps1').BaseName | Sort-Object
        $manifest = Import-PowerShellDataFile (Join-Path $moduleRoot 'ServiceDeskLab.psd1')
        @($manifest.FunctionsToExport | Sort-Object) | Should -Be @($public)
        @((Get-Module ServiceDeskLab).ExportedFunctions.Keys | Sort-Object) | Should -Be @($public)
    }

    It 'documents every public function with a synopsis and an example' {
        foreach ($name in (Get-Module ServiceDeskLab).ExportedFunctions.Keys) {
            $help = Get-Help $name -Full
            $help.Synopsis | Should -Not -BeLike "*$name*" -Because "$name needs a .SYNOPSIS"
            if ($name -notin 'Get-LabConfig', 'Remove-LabExpiredLeaver', 'Revoke-LabShareAccess') {
                $help.Examples | Should -Not -BeNullOrEmpty -Because "$name needs an .EXAMPLE"
            }
        }
    }

    It 'calls private module functions from the lab scripts only inside the module' {
        # Import-Module exposes only the public functions; a lab script that needs a private one runs
        # it in the module's scope: & (Get-Module ServiceDeskLab) { ... } or & $module { ... }.
        $private = foreach ($file in Get-ChildItem (Join-Path $moduleRoot 'Private') -Filter '*.ps1') {
            $ast = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$null)
            $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false).Name
        }
        $private | Should -Contain 'Initialize-LabHomeFolder'
        foreach ($script in Get-ChildItem -Path (Join-Path $repoRoot 'lab') -Recurse -Filter '*.ps1') {
            $ast = [Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$null, [ref]$null)
            $calls = $ast.FindAll({
                    param($node)
                    $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $private
                }, $true)
            foreach ($call in $calls) {
                $inModule = $false
                for ($parent = $call.Parent; $parent; $parent = $parent.Parent) {
                    if ($parent -is [Management.Automation.Language.CommandAst] -and $parent.InvocationOperator -eq 'Ampersand' -and
                        $parent.CommandElements[0].Extent.Text -match 'Get-Module ServiceDeskLab|^\$module$') {
                        $inModule = $true
                        break
                    }
                }
                $inModule | Should -BeTrue -Because "$($script.Name) line $($call.Extent.StartLineNumber) calls the private $($call.GetCommandName())"
            }
        }
    }

    It 'has no syntax errors in any PowerShell file' {
        $files = Get-ChildItem -Path $repoRoot -Recurse -Include '*.ps1', '*.psm1', '*.psd1' | Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' }
        foreach ($file in $files) {
            $tokens = $null
            $errors = $null
            [void][Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
            @($errors).Count | Should -Be 0 -Because "$($file.Name): $($errors | Select-Object -First 1)"
        }
    }

    It 'keeps every PowerShell file plain ASCII, which Windows PowerShell 5.1 reads correctly without a BOM' {
        $files = Get-ChildItem -Path $repoRoot -Recurse -Include '*.ps1', '*.psm1', '*.psd1' | Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' }
        $files.Count | Should -BeGreaterThan 10
        foreach ($file in $files) {
            $bytes = [IO.File]::ReadAllBytes($file.FullName)
            @($bytes | Where-Object { $_ -gt 127 }).Count | Should -Be 0 -Because "$($file.Name) must be ASCII"
        }
    }
}
