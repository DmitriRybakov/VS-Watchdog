<#
.SYNOPSIS
    Load the hosted deployment settings into this PowerShell session only.

.DESCRIPTION
    Dot-source this script - note the leading dot and space - so that the values
    land in the session you are typing in:

        . .\deploy\Use-DeployEnv.ps1

    Environment variables set this way beat anything in .env, so the next
    `alembic`, `watchdog ingest` or `watchdog screen` in this window runs against
    the hosted database. Nothing on disk changes, and closing the window undoes
    it. Your local .env is never read, written or moved.

    No value is ever printed. Only the names of the settings that were loaded.

.PARAMETER Path
    The file to read. Defaults to deploy\.env.render, which is git-ignored.

.PARAMETER Clear
    Remove the settings from this session again instead of loading them.

.EXAMPLE
    . .\deploy\Use-DeployEnv.ps1
    .\.venv\Scripts\python.exe -m alembic upgrade head

.EXAMPLE
    . .\deploy\Use-DeployEnv.ps1 -Clear
#>
[CmdletBinding()]
param(
    [string]$Path = (Join-Path $PSScriptRoot '.env.render'),
    [switch]$Clear
)

$ErrorActionPreference = 'Stop'

$names = @('DATABASE_URL', 'AUTH_USERNAME', 'AUTH_PASSWORD', 'SESSION_SECRET', 'LLM_API_KEY')

if ($Clear) {
    foreach ($name in $names) {
        Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
    }
    Write-Host 'Deployment settings removed from this session. Commands now use .env again.'
    return
}

if (-not (Test-Path -LiteralPath $Path)) {
    Write-Error ("No such file: $Path`n" +
        'Copy deploy\render.env.example to deploy\.env.render and fill it in. ' +
        'That file is git-ignored; never commit it.')
}

$loaded = @()

foreach ($line in Get-Content -LiteralPath $Path) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith('#')) { continue }

    $split = $text.IndexOf('=')
    if ($split -lt 1) { continue }

    $name = $text.Substring(0, $split).Trim()
    $value = $text.Substring($split + 1).Trim()

    # A value may be quoted so that trailing spaces survive an editor.
    if ($value.Length -ge 2 -and
        (($value.StartsWith('"') -and $value.EndsWith('"')) -or
         ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    if (-not $value) { continue }

    # ENVIRONMENT is deliberately not loadable: this session is a laptop running
    # commands against a hosted database, not a hosted process.
    if ($name -eq 'ENVIRONMENT') {
        Write-Warning 'ENVIRONMENT is ignored here. It belongs in the Render dashboard.'
        continue
    }

    Set-Item -Path ("Env:" + $name) -Value $value
    $loaded += $name
}

if (-not $loaded) {
    Write-Error "Nothing was loaded from $Path. Every line was blank, a comment, or had no value."
}

Write-Host ("Loaded into this session only: " + ($loaded -join ', '))
Write-Host 'Commands in this window now run against the hosted database.'
Write-Host 'Close the window, or run this script with -Clear, to go back to local.'
