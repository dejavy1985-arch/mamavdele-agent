# ЗАПУСК TELEGRAM-ЦЕНТРА ПОД WINDOWS. Это шаг НА ПОТОМ.
# Пока Telegram не включаем, этот файл трогать не нужно.
#
# Когда решите включить:
#   1) создайте бота у @BotFather и получите токен;
#   2) создайте файл config\telegram_token.txt и вставьте туда токен одной строкой
#      (файл в git не попадает, это ваш секрет);
#   3) впишите свой Telegram user_id в config\control_center.json;
#   4) запустите этот файл правой кнопкой -> "Выполнить с помощью PowerShell".

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Write-Host "Python не найден. Установите Python 3.10+." -ForegroundColor Red; exit 1 }

$tokenFile = Join-Path $root "config\telegram_token.txt"
if (-not (Test-Path $tokenFile)) {
    Write-Host "Нет файла config\telegram_token.txt." -ForegroundColor Yellow
    Write-Host "Создайте его и вставьте токен бота одной строкой, затем запустите снова." -ForegroundColor Yellow
    exit 1
}

$cfg = Join-Path $root "config\control_center.json"
if (-not (Test-Path $cfg)) {
    Write-Host "Нет config\control_center.json. Сначала запустите setup_windows.ps1." -ForegroundColor Yellow
    exit 1
}

Write-Host "Запускаю центр управления. Остановить: закройте это окно или нажмите Ctrl+C." -ForegroundColor Green
& $py.Source -m acc.telegram_bot "config\control_center.json"
