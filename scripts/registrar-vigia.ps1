<#
.SYNOPSIS
  Registra (ou atualiza) a tarefa agendada "BackupRestore Vigia".

.DESCRIPTION
  O vigia (vigia.py) avisa por notificação do Windows quando o VPS acumula
  dumps que este PC não baixou. Ele é uma tarefa à parte da BackupRestore
  porque o defeito que precisa pegar é justamente aquela tarefa desabilitada,
  que não roda e portanto não relata nada.

  Roda todo dia às 12:00 e uma hora depois de cada logon (a BackupRestore,
  com "executar assim que possível", recupera a sincronização perdida nesse
  meio-tempo). Precisa da sessão do usuário, porque a notificação aparece
  na área de trabalho dele. Rodar de novo só substitui a tarefa.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$python = $null
if ($env:BACKUPRESTORE_PYTHON) {
    $python = $env:BACKUPRESTORE_PYTHON
} elseif (Test-Path -LiteralPath (Join-Path $projectDir ".venv\Scripts\python.exe")) {
    $python = Join-Path $projectDir ".venv\Scripts\python.exe"
} else {
    throw "Python 3.15 nao encontrado. Recrie .venv ou configure BACKUPRESTORE_PYTHON."
}

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$vigia = Join-Path $projectDir "vigia.py"
$acao = New-ScheduledTaskAction -Execute $python -Argument "`"$vigia`"" -WorkingDirectory $projectDir
$diario = New-ScheduledTaskTrigger -Daily -At "12:00"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$logon.Delay = "PT1H"
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "BackupRestore Vigia" -Action $acao -Trigger $diario, $logon `
    -Settings $config -Principal $principal -Force `
    -Description "Avisa quando o VPS acumula dumps que este PC nao baixou (vigia.py)." | Out-Null

$tarefa = Get-ScheduledTask -TaskName "BackupRestore Vigia"
$info = Get-ScheduledTaskInfo -TaskName "BackupRestore Vigia"
Write-Host "Tarefa '$($tarefa.TaskName)': $($tarefa.State); proxima execucao $($info.NextRunTime)"
