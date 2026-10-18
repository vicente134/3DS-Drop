@echo off
title Instalador Directo de 3DS-Drop para Windows
echo ==========================================================
echo        Instalando 3DS-Drop para Windows (ElVichoPo)
echo ==========================================================
echo.

cd /d "%~dp0\pc-client"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Python 3 no esta instalado. Descargalo desde https://www.python.org/downloads/
    echo     Asegurate de marcar la casilla "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/3] Creando entorno de la aplicacion...
if not exist "venv" (
    python -m venv venv
)

echo [2/3] Instalando interfaz grafica PySide6...
call venv\Scripts\pip.exe install --upgrade pip PySide6 >nul

echo [3/3] Creando acceso directo "3DS-Drop" en tu Escritorio (sin consola)...
set "APP_DIR=%~dp0pc-client"
set "PYTHONW=%APP_DIR%\venv\Scripts\pythonw.exe"
set "MAIN_PY=%APP_DIR%\main.py"
set "SHORTCUT=%USERPROFILE%\Desktop\3DS-Drop.lnk"

powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%');$s.TargetPath='%PYTHONW%';$s.Arguments='\"%MAIN_PY%\"';$s.WorkingDirectory='%APP_DIR%';$s.Description='3DS-Drop - Transferencia PC a Nintendo 3DS / 2DS';$s.Save()"

echo.
echo ==========================================================
echo  Listo! Ya tienes el icono "3DS-Drop" en tu Escritorio.
echo  Abriendo 3DS-Drop...
echo ==========================================================
start "" "%PYTHONW%" "%MAIN_PY%"
