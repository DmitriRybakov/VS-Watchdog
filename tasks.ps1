<#
.SYNOPSIS
    Task runner for Watchdog on Windows. Mirrors the Makefile targets.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Task,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$extra = @($Rest | Where-Object { $_ })

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Show-Usage {
    Write-Host 'Usage: .\tasks.ps1 <task>'
    Write-Host ''
    Write-Host '  install    Create .venv and install the package with dev extras'
    Write-Host '  lint       ruff check and ruff format --check'
    Write-Host '  typecheck  mypy'
    Write-Host '  test       pytest with coverage'
    Write-Host '  run        uvicorn with reload on port 8000'
    Write-Host '  migrate    alembic upgrade head'
}

# Run a command and stop the script if it fails; $LASTEXITCODE is not checked by ErrorActionPreference.
function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)

    Write-Host "> $Exe $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Assert-Venv {
    if (-not (Test-Path -LiteralPath $py)) {
        Write-Error "No interpreter at $py. Run: .\tasks.ps1 install"
    }
}

if (-not $Task) {
    Show-Usage
    exit 0
}

switch ($Task) {
    'install' {
        Invoke-Step 'python' @('-m', 'venv', '.venv')
        Invoke-Step $py @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-Step $py @('-m', 'pip', 'install', '-e', '.[dev]')
    }
    'lint' {
        Assert-Venv
        Invoke-Step $py @('-m', 'ruff', 'check', '.')
        Invoke-Step $py @('-m', 'ruff', 'format', '--check', '.')
    }
    'typecheck' {
        Assert-Venv
        Invoke-Step $py @('-m', 'mypy')
    }
    'test' {
        Assert-Venv
        Invoke-Step $py (@('-m', 'pytest', '--cov=watchdog', '--cov-report=term-missing') + $extra)
    }
    'run' {
        Assert-Venv
        Invoke-Step $py (@('-m', 'uvicorn', 'watchdog.web.app:app', '--reload', '--port', '8000') + $extra)
    }
    'migrate' {
        Assert-Venv
        Invoke-Step $py (@('-m', 'alembic', 'upgrade', 'head') + $extra)
    }
    default {
        Write-Host "Unknown task: $Task" -ForegroundColor Red
        Write-Host ''
        Show-Usage
        exit 1
    }
}
