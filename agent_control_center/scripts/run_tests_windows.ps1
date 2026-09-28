# Прогон тестов центра управления под Windows.
# Правой кнопкой -> "Выполнить с помощью PowerShell".

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Write-Host "Python не найден. Установите Python 3.10+." -ForegroundColor Red; exit 1 }

& $py.Source tests\run_tests.py
