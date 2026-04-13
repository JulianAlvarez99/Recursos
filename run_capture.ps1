# Este script es el "Payload" que ejecutará el servicio de Windows
$WorkingDir = $PSScriptRoot
if (-not $WorkingDir) { $WorkingDir = Get-Location }

# Cambiar al directorio raiz para cargar .env y la DLL
cd $WorkingDir

# Ruta al ejecutable de python dentro del venv
$PythonVenv = Join-Path $WorkingDir "venv\Scripts\python.exe"
$CaptureScript = Join-Path $WorkingDir "capture.py"

Write-Host "Esperando 30 segundos para estabilización del sistema..."
Start-Sleep -Seconds 30

# Ejecutar el script de captura
# Redireccionamos errores a un log local para facilitar el debugeo del servicio
Write-Host "Iniciando captura de telemetria..."

$env:PYTHONIOENCODING = "UTF-8"

& $PythonVenv -X utf8 $CaptureScript *>> "$WorkingDir\service_log.txt"
