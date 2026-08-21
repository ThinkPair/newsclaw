param(
    [int]$Port = 8431,
    [string]$RuntimeRoot = '',
    [string]$SourceConfig = '',
    [string]$PythonExecutable = 'python'
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pluginRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '..\..'))
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $pluginRoot 'runtime-v2'
}
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$configPath = Join-Path $RuntimeRoot 'config.toml'

if (-not (Test-Path -LiteralPath $configPath)) {
    if (-not $SourceConfig) {
        throw 'SourceConfig is required the first time this runtime is prepared.'
    }
    & $PythonExecutable (Join-Path $PSScriptRoot 'prepare_newsclaw_runtime.py') `
        --template (Join-Path $repoRoot 'config.example.toml') `
        --source-config $SourceConfig `
        --runtime-root $RuntimeRoot `
        --port $Port
}

$env:OPENBILICLAW_PROJECT_ROOT = $RuntimeRoot
$env:NEWSCLAW_MODE = '1'
$env:PYTHONPATH = (Join-Path $repoRoot 'src')
& $PythonExecutable -m uvicorn 'openbiliclaw.api.app:create_app' --factory --host 127.0.0.1 --port $Port
