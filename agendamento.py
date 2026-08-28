"""Orquestra a sincronizacao diaria do VPS e o backup local semanal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence


RAIZ_PROJETO = Path(__file__).resolve().parent
ARQUIVO_ESTADO = RAIZ_PROJETO / "agendamento.local.json"


def domingo_mais_recente(hoje: date) -> date:
    return hoje.fromordinal(hoje.toordinal() - ((hoje.weekday() + 1) % 7))


def _ler_estado(arquivo: Path) -> dict[str, str]:
    if not arquivo.exists():
        return {}
    with arquivo.open(encoding="utf-8") as origem:
        dados = json.load(origem)
    if not isinstance(dados, dict):
        raise ValueError("estado do agendamento invalido")
    return {str(chave): str(valor) for chave, valor in dados.items()}


def _gravar_estado(arquivo: Path, dados: dict[str, str]) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=arquivo.parent, delete=False
    ) as temporario:
        json.dump(dados, temporario, ensure_ascii=False, sort_keys=True)
        temporario.flush()
        os.fsync(temporario.fileno())
        nome_temporario = temporario.name
    os.replace(nome_temporario, arquivo)


def inicializar_estado(arquivo: Path, hoje: date) -> dict[str, str]:
    estado = _ler_estado(arquivo)
    if "habilitado_em" not in estado:
        estado["habilitado_em"] = hoje.isoformat()
        _gravar_estado(arquivo, estado)
    return estado


def backup_local_devido(hoje: date, estado: dict[str, str]) -> bool:
    domingo = domingo_mais_recente(hoje)
    habilitado_em = date.fromisoformat(estado["habilitado_em"])
    ultima_data = estado.get("ultimo_backup_local_em")
    if ultima_data and date.fromisoformat(ultima_data) >= domingo:
        return False
    return domingo >= habilitado_em


def segundos_ate_quatro_horas(agora: datetime) -> float:
    if agora.weekday() != 6:
        return 0.0
    janela = agora.replace(hour=4, minute=0, second=0, microsecond=0)
    return max(0.0, (janela - agora).total_seconds())


def executar_cli(
    argumentos: Sequence[str],
    executar_processo: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> int:
    resultado = executar_processo(
        [sys.executable, str(RAIZ_PROJETO / "cli.py"), *argumentos],
        cwd=RAIZ_PROJETO,
        check=False,
    )
    return int(resultado.returncode)


def main(
    argv: Sequence[str] = (),
    *,
    arquivo_estado: Path = ARQUIVO_ESTADO,
    obter_agora: Callable[[], datetime] = datetime.now,
    dormir: Callable[[float], None] = time.sleep,
    executar: Callable[[Sequence[str]], int] | None = None,
) -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--inicializar", action="store_true")
    args = analisador.parse_args(argv)

    agora = obter_agora()
    estado = inicializar_estado(arquivo_estado, agora.date())
    if args.inicializar:
        return 0

    chamar_cli = executar or executar_cli
    codigo_vps = chamar_cli(("sincronizar-vps", "--todos"))
    codigo_local = 0

    if backup_local_devido(agora.date(), estado):
        espera = segundos_ate_quatro_horas(obter_agora())
        if espera:
            dormir(espera)
        codigo_local = chamar_cli(("backup", "--todos"))
        if codigo_local == 0:
            estado["ultimo_backup_local_em"] = obter_agora().date().isoformat()
            _gravar_estado(arquivo_estado, estado)

    return 0 if codigo_vps == 0 and codigo_local == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
