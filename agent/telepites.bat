@echo off
rem Iwfm nyomtato-ugynok automatikus inditasa + orszem (onallo ujraelesztes).
rem Futtasd egyszer ezen a PC-n — utana:
rem   - minden bejelentkezeskor magatol indul,
rem   - es 5 percenkent egy orszem-feladat ellenorzi: ha az ugynok barmiert
rem     kihalt (lefagyas, hiba, frissites), magatol ujrainditja. Ha mar fut,
rem     az uj peldany a beepitett zar miatt csendben kilep — dupla nyomtatas
rem     nem lehetseges.
set SCRIPT=%~dp0print_agent.ps1

schtasks /create /f /tn "Iwfm nyomtato-ugynok" /sc onlogon ^
  /tr "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%SCRIPT%\""
if not %errorlevel%==0 goto :hiba

schtasks /create /f /tn "Iwfm nyomtato-ugynok orszem" /sc minute /mo 5 ^
  /tr "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%SCRIPT%\""
if not %errorlevel%==0 goto :hiba

echo Kesz! Az ugynok bejelentkezeskor magatol indul, es az orszem 5 percenkent
echo ujraeleszti, ha kihalna. Most azonnal el is inditom...
schtasks /run /tn "Iwfm nyomtato-ugynok"
goto :vege

:hiba
echo HIBA: futtasd rendszergazdakent, vagy inditsd kezzel:
echo powershell -ExecutionPolicy Bypass -File "%SCRIPT%"

:vege
pause
