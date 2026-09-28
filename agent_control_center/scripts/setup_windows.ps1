# Установка центра управления под Windows.
# Как запустить: в проводнике зайдите в папку agent_control_center,
# правой кнопкой по этому файлу -> "Выполнить с помощью PowerShell".
# Скрипт НЕ запускает Telegram и НЕ спрашивает токен.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # папка agent_control_center
Set-Location $root

function Get-Python {
    $c = Get-Command python -ErrorAction SilentlyContinue
    if (-not $c) { $c = Get-Command py -ErrorAction SilentlyContinue }
    return $c
}

Write-Host "1) Проверяю Python..." -ForegroundColor Cyan
$py = Get-Python
if (-not $py) {
    Write-Host "Python не найден. Установите Python 3.10 или новее с python.org," -ForegroundColor Red
    Write-Host "при установке отметьте галочку 'Add Python to PATH', затем запустите этот файл снова." -ForegroundColor Red
    exit 1
}
& $py.Source --version

Write-Host "2) Готовлю конфиг..." -ForegroundColor Cyan
$cfg = Join-Path $root "config\control_center.json"
$example = Join-Path $root "config\control_center.example.json"
if (-not (Test-Path $cfg)) {
    Copy-Item $example $cfg
    Write-Host "Создан config\control_center.json. Откройте его и впишите свой Telegram user_id в allowed_user_ids."
} else {
    Write-Host "config\control_center.json уже есть, не меняю."
}

Write-Host "3) Создаю папки домиков..." -ForegroundColor Cyan
& $py.Source scripts\init_homes.py

Write-Host "4) Прогоняю тесты..." -ForegroundColor Cyan
& $py.Source tests\run_tests.py

Write-Host ""
Write-Host "Готово. Следующее (когда сами решите): впишите user_id в config\control_center.json." -ForegroundColor Green
Write-Host "Telegram пока не включаем." -ForegroundColor Green
