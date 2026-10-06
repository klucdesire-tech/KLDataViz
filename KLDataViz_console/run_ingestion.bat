@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo  Utilisation : run_ingestion.bat "chemin\fichier.xlsx" [--sheet NOM] [--name nom_logique]
  echo  Autres modes : run_ingestion.bat --list   ^|   --summary   ^|   --versions DS-XXXX
  echo.
  ".venv\Scripts\python.exe" -m ingestion --summary
  pause
  exit /b 0
)
".venv\Scripts\python.exe" -m ingestion %*
pause
