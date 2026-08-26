@echo off
rem Iwfm nyomtato-ugynok automatikus inditasa bejelentkezeskor (rejtett ablak).
rem Futtasd egyszer ezen a PC-n — utana minden bekapcsolaskor magatol indul.
set SCRIPT=%~dp0print_agent.ps1
schtasks /create /f /tn "Iwfm nyomtato-ugynok" /sc onlogon ^
  /tr "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%SCRIPT%\""
if %errorlevel%==0 (
  echo Kesz! Az ugynok mostantol bejelentkezeskor magatol elindul.
  echo Most azonnal el is inditom...
  schtasks /run /tn "Iwfm nyomtato-ugynok"
) else (
  echo HIBA: futtasd rendszergazdakent, vagy inditsd kezzel:
  echo powershell -ExecutionPolicy Bypass -File "%SCRIPT%"
)
pause
