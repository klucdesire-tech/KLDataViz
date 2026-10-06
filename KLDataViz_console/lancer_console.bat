@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  KLDataViz Console
echo  Demarrage sur http://localhost:8642
echo  Laissez cette fenetre ouverte (fermer = arreter le serveur).
echo.
start "" cmd /c "timeout /t 3 /nobreak >nul & start "" http://localhost:8642"
".venv\Scripts\python.exe" server.py
pause
