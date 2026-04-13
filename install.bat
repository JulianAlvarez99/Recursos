@echo off
setlocal
cd /d %~dp0

:: Check for Admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Por favor ejecuta este archivo como Administrador para poder crear el servicio.
    pause
    exit /b
)

:: Revisar si existe .env
if not exist ".env" (
    echo [ERROR] No se encontro el archivo .env.
    echo Por favor crea el archivo .env y configura tus credenciales antes de continuar.
    pause
    exit /b
)

echo [1/5] Creando Entorno Virtual (venv)...
if not exist "venv\" (
    python -m venv venv
)
if %errorlevel% neq 0 (
    echo [ERROR] No se pudo crear el entorno virtual. Prueba instalando Python 3.10+.
    pause
    exit /b
)

echo [2/5] Instalando dependencias necesarias (psycopg2, pythonnet, dotenv)...
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\pip install -r requirements.txt

echo [3/5] Inicializando Tablas Maestras (Componente, Sensor) y extension TimescaleDB...
.\venv\Scripts\python.exe init_master_tables.py
if %errorlevel% neq 0 (
    echo [ERROR] No se pudieron inicializar las tablas maestras. Revisa la conexion a la BD.
    pause
    exit /b
)

echo [4/5] Creando Hypertable para el cliente y politica de retencion...
.\venv\Scripts\python.exe db_setup.py
if %errorlevel% neq 0 (
    echo [ERROR] La configuracion de la base de datos fallo. Revisa el .env.
    pause
    exit /b
)

echo [5/5] Registrando el tarea de Windows via PowerShell...
powershell -ExecutionPolicy Bypass -File .\service_setup.ps1

echo.
echo ====================================================
echo   [OK] INSTALACION COMPLETADA EXITOSAMENTE
echo ====================================================
echo La tarea programada "LHM_Capture_Task" 
echo ha sido registrada y esta lista para ser iniciada.
echo Recuerda iniciarlo desde "Task Scheduler" como ADMINISTRADOR.
pause
