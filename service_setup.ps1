$TaskName = "LHM_Capture_Task"
$Description = "Captura de telemetría de hardware (LHM) al iniciar el sistema."

# Carpeta actual
$WorkingDir = $PSScriptRoot
if (-not $WorkingDir) { $WorkingDir = Get-Location }

$RunScript = Join-Path $WorkingDir "run_capture.ps1"

# 1. Eliminar tarea existente si ya estaba creada
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[WAIT] Eliminando tarea programada anterior..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 2. Configurar el comando para correr en segundo plano (PowerShell oculto)
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$RunScript`"" `
    -WorkingDirectory $WorkingDir

# 3. Disparador: Al iniciar el equipo (At startup)
$Trigger = New-ScheduledTaskTrigger -AtStartup

# 4. Configuración: Ejecutar con Privilege Más Alto (System/Admin)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# 5. Ajustes adicionales para que no se detenga sola y sea infinita
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) # Desactiva el límite de 72 horas

# 6. Registro de la tarea
Register-ScheduledTask -TaskName $TaskName `
                       -Description $Description `
                       -Action $Action `
                       -Trigger $Trigger `
                       -Principal $Principal `
                       -Settings $Settings

Write-Host "[OK] Tarea Programada '$TaskName' creada correctamente."
Write-Host "--------------------------------------------------------"
Write-Host "La captura se iniciará automáticamente en cada reinicio."
Write-Host "Para probarla ahora mismo, ejecuta en PowerShell (Admin):"
Write-Host "Start-ScheduledTask -TaskName '$TaskName'"
