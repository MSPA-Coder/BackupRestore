<#
.SYNOPSIS
  Confere o catálogo e ensaia a restauração local e do último dump do VPS.

.DESCRIPTION
  É o ensaio operacional mensal do projeto ControleRendaVariavel. A
  restauração continua presa ao sandbox autorizado pelo cli.py; este script
  apenas encadeia a interface oficial e grava um resumo legível para o
  Agendador de Tarefas.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$python = $null
if ($env:BACKUPRESTORE_PYTHON) {
    $python = $env:BACKUPRESTORE_PYTHON
} elseif (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA "Python\bin\python.exe")) {
    $python = Join-Path $env:LOCALAPPDATA "Python\bin\python.exe"
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$cli = Join-Path $projectDir "cli.py"
$resumo = Join-Path $projectDir "ensaio-mensal-ultimo.txt"
$temporario = Join-Path $projectDir ".ensaio-mensal-$([guid]::NewGuid().ToString('N')).tmp"
$linhas = [System.Collections.Generic.List[string]]::new()
$falhou = $false

function Invoke-Operacao {
    param([Parameter(Mandatory = $true)][string[]]$Argumentos)

    $linhas.Add("$((Get-Date).ToString('s'))  python cli.py $($Argumentos -join ' ')")
    $saida = & $python $cli @Argumentos 2>&1 | Out-String
    if ($saida) {
        $linhas.Add($saida.TrimEnd())
    }
    if ($LASTEXITCODE -ne 0) {
        $script:falhou = $true
        $linhas.Add("código de saída: $LASTEXITCODE")
    }
}

try {
    $linhas.Add("BackupRestore - ensaio mensal")
    $linhas.Add("Início: $((Get-Date).ToString('s'))")
    Invoke-Operacao @("verificar")
    Invoke-Operacao @("ensaio", "--projeto", "controle_renda_variavel")
    Invoke-Operacao @("ensaio", "--projeto", "controle_renda_variavel_vps")
    $linhas.Add("Fim: $((Get-Date).ToString('s'))")
    $linhas.Add($(if ($falhou) { "RESULTADO: FALHA" } else { "RESULTADO: OK" }))
    [System.IO.File]::WriteAllLines($temporario, [string[]]$linhas, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporario -Destination $resumo -Force
    $linhas | Write-Output
    if ($falhou) { exit 1 }
} catch {
    $linhas.Add("RESULTADO: FALHA - $($_.Exception.Message)")
    [System.IO.File]::WriteAllLines($temporario, [string[]]$linhas, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporario -Destination $resumo -Force
    $linhas | Write-Output
    exit 1
} finally {
    Remove-Item -LiteralPath $temporario -Force -ErrorAction SilentlyContinue
}
