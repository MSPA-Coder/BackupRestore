"""Orquestra a sincronizacao diaria do VPS e o backup local semanal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import banco


RAIZ_PROJETO = Path(__file__).resolve().parent
ARQUIVO_ESTADO = RAIZ_PROJETO / "agendamento.local.json"

# O codigo de saida 1 desta tarefa so aparece na coluna "Resultado da ultima
# execucao" do Agendador de Tarefas, e o motivo da falha ficava apenas em
# `execucoes.erro` no catalogo. Uma falha assim passou quatro dias sem ser
# notada. Este arquivo e o canal legivel: quem sucedeu, quem falhou e por que.
ARQUIVO_RESUMO = RAIZ_PROJETO / "ultima-execucao.txt"


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


def _gravar_texto(arquivo: Path, texto: str) -> None:
    """Grava por troca atomica: quem le nunca ve meio arquivo."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=arquivo.parent, delete=False
    ) as temporario:
        temporario.write(texto)
        temporario.flush()
        os.fsync(temporario.fileno())
        nome_temporario = temporario.name
    os.replace(nome_temporario, arquivo)


def _gravar_estado(arquivo: Path, dados: dict[str, str]) -> None:
    _gravar_texto(arquivo, json.dumps(dados, ensure_ascii=False, sort_keys=True))


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


def _imprimir(texto: str) -> None:
    """Espelha o resumo na saida padrao, para quem roda a tarefa a mao.

    O console do Agendador de Tarefas raramente e UTF-8: sem esta troca, um
    travessao no resumo derrubaria a tarefa com UnicodeEncodeError depois de
    todo o trabalho ja ter sido feito.
    """
    codificacao = getattr(sys.stdout, "encoding", None) or "ascii"
    print(texto.encode(codificacao, "replace").decode(codificacao))


def _operacoes(quantidade: int) -> str:
    return f"{quantidade} operação" if quantidade == 1 else f"{quantidade} operações"


def _instante(momento: datetime) -> str:
    return momento.strftime("%d/%m/%Y %H:%M:%S")


def _linhas_do_motivo(erro: str | None) -> list[str]:
    """O motivo inteiro, quebrado para caber na tela — nunca truncado.

    Uma mensagem truncada obrigaria a abrir o catalogo justamente para
    descobrir o que faltou, que e o problema que este arquivo resolve.
    """
    motivo = " ".join((erro or "sem mensagem registrada").split())
    return textwrap.wrap(motivo, width=88) or [motivo]


def montar_resumo(
    *,
    inicio: datetime,
    fim: datetime,
    execucoes: Sequence[Mapping[str, Any]],
    codigo_vps: int,
    codigo_local: int | None,
) -> str:
    """Texto legivel da rodada, a partir do que o motor gravou no catalogo.

    `execucoes` sao as linhas abertas durante esta rodada; o motor e o `vps.py`
    ja registraram ali situacao e motivo de cada projeto. Nada e reinterpretado
    aqui: o resumo mostra o catalogo, nao uma segunda versao dos fatos.
    """
    falhas = [e for e in execucoes if e["situacao"] == "falha"]
    sucessos = [e for e in execucoes if e["situacao"] == "sucesso"]
    pendentes = [e for e in execucoes if e["situacao"] in ("fila", "rodando")]

    houve_falha = bool(falhas) or codigo_vps != 0 or (codigo_local or 0) != 0
    if houve_falha:
        veredito = (
            f"FALHA — {len(falhas)} de {_operacoes(len(execucoes))} "
            f"{'falhou' if len(falhas) == 1 else 'falharam'}"
        )
    else:
        veredito = f"OK — {_operacoes(len(sucessos))}, nenhuma falha"

    if codigo_local is None:
        situacao_local = "não era devido nesta data"
    elif codigo_local == 0:
        situacao_local = "concluído"
    else:
        situacao_local = "falhou"

    linhas = [
        "BackupRestore — resumo da última execução agendada",
        "=" * 64,
        f"Início ....: {_instante(inicio)}",
        f"Fim .......: {_instante(fim)}",
        f"RESULTADO .: {veredito}",
        "",
        f"Sincronização do VPS ..: {'concluída' if codigo_vps == 0 else 'falhou'}",
        f"Backup local ..........: {situacao_local}",
    ]

    if falhas:
        linhas += ["", f"FALHARAM ({len(falhas)})", "-" * 64]
        for execucao in falhas:
            linhas.append(f"  {execucao['projeto']}  [{execucao['operacao']}]")
            linhas += [f"      {parte}" for parte in _linhas_do_motivo(execucao["erro"])]
            linhas.append("")

    if pendentes:
        # Rodada interrompida: a linha ficou aberta e ninguem a fechou.
        linhas += ["", f"SEM DESFECHO ({len(pendentes)})", "-" * 64]
        linhas += [
            f"  {e['projeto']}  [{e['operacao']}]  situação={e['situacao']}"
            for e in pendentes
        ]
        linhas.append("")

    if sucessos:
        linhas += ["", f"SUCESSO ({len(sucessos)})", "-" * 64]
        linhas += [f"  {e['projeto']}  [{e['operacao']}]" for e in sucessos]

    if not execucoes and houve_falha:
        # Nem o primeiro projeto chegou a abrir execução: o defeito é anterior
        # a eles (runtime, catálogo inacessível, argumento errado na tarefa).
        linhas += ["", "Nenhuma operação chegou a abrir no catálogo.",
                   "A falha é anterior aos projetos — veja a saída da tarefa agendada."]
    elif not execucoes:
        linhas += ["", "Nenhuma operação foi registrada no catálogo nesta execução."]

    linhas += ["", "Detalhe completo: interface web (Histórico) ou `python cli.py listar`."]
    return "\n".join(linhas) + "\n"


def main(
    argv: Sequence[str] = (),
    *,
    arquivo_estado: Path = ARQUIVO_ESTADO,
    obter_agora: Callable[[], datetime] = datetime.now,
    dormir: Callable[[float], None] = time.sleep,
    executar: Callable[[Sequence[str]], int] | None = None,
    arquivo_resumo: Path = ARQUIVO_RESUMO,
    catalogo: Any = banco,
) -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--inicializar", action="store_true")
    args = analisador.parse_args(argv)

    agora = obter_agora()
    estado = inicializar_estado(arquivo_estado, agora.date())
    if args.inicializar:
        return 0

    catalogo.criar_tabelas()
    marca = catalogo.ultimo_id_execucao()

    chamar_cli = executar or executar_cli
    codigo_vps = chamar_cli(("sincronizar-vps", "--todos"))
    codigo_local: int | None = None

    if backup_local_devido(agora.date(), estado):
        espera = segundos_ate_quatro_horas(obter_agora())
        if espera:
            dormir(espera)
        codigo_local = chamar_cli(("backup", "--todos"))
        if codigo_local == 0:
            estado["ultimo_backup_local_em"] = obter_agora().date().isoformat()
            _gravar_estado(arquivo_estado, estado)

    # Depois de gravar o estado: uma falha ao escrever o resumo nao pode
    # custar o registro do backup que de fato aconteceu.
    resumo = montar_resumo(
        inicio=agora,
        fim=obter_agora(),
        execucoes=[dict(linha) for linha in catalogo.execucoes_desde(marca)],
        codigo_vps=codigo_vps,
        codigo_local=codigo_local,
    )
    _gravar_texto(arquivo_resumo, resumo)
    _imprimir(resumo)

    return 0 if codigo_vps == 0 and (codigo_local or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
