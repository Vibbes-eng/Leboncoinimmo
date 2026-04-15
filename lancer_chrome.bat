@echo off
:: ──────────────────────────────────────────────────────────────────
::  lancer_chrome.bat — Ouvre Chrome avec CDP sur le port 9222
::  Profil dédié : %USERPROFILE%\.leboncoin_chrome_profile
::  (login LeBonCoin mémorisé entre les sessions)
:: ──────────────────────────────────────────────────────────────────

set CHROME=
for %%P in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if exist %%P (
  if not defined CHROME set CHROME=%%P
)

if not defined CHROME (
  echo ERREUR : Chrome introuvable. Installez Google Chrome.
  pause
  exit /b 1
)

set PROFILE_DIR=%USERPROFILE%\.leboncoin_chrome_profile

echo Lancement de Chrome avec CDP sur le port 9222...
echo Profil : %PROFILE_DIR%
echo.
echo Une fois Chrome ouvert :
echo   1. Connectez-vous a votre compte LeBonCoin (si souhaite)
echo   2. Naviguez vers vos recherches sauvegardees OU une page de resultats
echo   3. Lancez : python leboncoin_analyzer.py
echo.

start "" %CHROME% ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%PROFILE_DIR%" ^
  "https://www.leboncoin.fr/mes-favoris/recherches"
