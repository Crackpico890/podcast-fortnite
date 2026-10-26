# Programa el lector de X para que se ejecute cada noche a las 3:00 en este PC.
# Ejecutar una vez desde PowerShell dentro de la carpeta del repo:
#   powershell -ExecutionPolicy Bypass -File pc_agent\programar_windows.ps1

$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "pc_agent\x_lists.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00am
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "PodcastFortnite-X" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Tarea 'PodcastFortnite-X' programada a las 3:00. El PC tiene que estar encendido o en suspensión."
