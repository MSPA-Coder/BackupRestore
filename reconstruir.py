"""Reconstrói o catálogo a partir dos manifestos que ficam ao lado dos artefatos.

POR QUE EXISTE

O `catalogo.sqlite3` não entra no kit de recuperação, de propósito (ver
KIT_RECUPERACAO.md): muda a cada execução, então uma cópia no kit envelheceria.
A contrapartida prometida era que ele é reconstruível, porque cada artefato
carrega ao lado um `<arquivo>.manifest.json`. Faltava o comando; sem ele, perder
o catálogo custava escrever o script na hora do aperto.

AS REGRAS DO CATÁLOGO VALEM AQUI TAMBÉM

Um artefato só entra como `valido` depois de relido (AGENTS.md, invariante 2).
O manifesto é uma afirmação, não uma prova: ele diz qual é o SHA-256, mas quem
confere é este módulo, e com a MESMA releitura que o backup normal faz:

- tamanho e SHA-256 contra o manifesto;
- dump: `pg_restore --list` no sandbox (se o sandbox não estiver de pé, o dump
  fica como "não verificado" e NÃO é registrado -- nada é julgado às cegas);
- ZIP de código: `verificar_zip_codigo`;
- cópia de volume: `verificar_volume`.

O padrão é SIMULAR. `aplicar=True` grava, e só o que passou.

O QUE NÃO VOLTA, E É PRECISO SABER

- `fixado` (a marca de "não aplicar retenção") não está no manifesto: todo
  artefato reconstruído volta com `fixado=0`. Quem fixava algo precisa fixar de
  novo antes do próximo backup, porque a retenção roda ao fim dele.
- O histórico de execuções e de eventos não volta (é o diário, não o acervo).
- Dumps de segurança de restauração (`pre_restauracao`) não têm manifesto e
  ficam de fora; são regraváveis e não contam como acervo.
- Pasta de projeto que o `projetos.py` não conhece (o NetWorth, por exemplo) é
  listada e ignorada.

Nada aqui apaga ou move arquivo.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass, field

import banco
import motor
from configuracao import ConfiguracaoInvalida, caminho_sob_raiz
from projetos import CONTAINER_SANDBOX, por_slug

TIPOS = ("banco", "codigo", "volume")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUFIXO_MANIFESTO = ".manifest.json"


@dataclass
class Relatorio:
    """O que a reconstrução fez (ou faria, na simulação)."""

    aplicado: bool
    registrados: list[str] = field(default_factory=list)
    ja_catalogados: list[str] = field(default_factory=list)
    rejeitados: list[tuple[str, str]] = field(default_factory=list)
    nao_verificados: list[str] = field(default_factory=list)
    ignorados: list[str] = field(default_factory=list)

    @property
    def limpo(self) -> bool:
        """Nada reprovado e nada ficou sem julgamento."""
        return not self.rejeitados and not self.nao_verificados


def _ler_manifesto(caminho: str, slug: str, tipo: str, nome: str) -> dict:
    """O manifesto, ou `ValueError` com o motivo. Não confia em nada dele."""
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError) as erro:
        raise ValueError(f"manifesto ilegível: {erro}") from erro
    if not isinstance(dados, dict):
        raise ValueError("manifesto não é um objeto JSON")
    # Cada campo tem de bater com o lugar onde o arquivo está: um manifesto
    # copiado para a pasta errada não pode catalogar o artefato errado.
    esperado = {"projeto": slug, "tipo": tipo, "arquivo": nome}
    for chave, valor in esperado.items():
        if dados.get(chave) != valor:
            raise ValueError(f"manifesto diz {chave}={dados.get(chave)!r}, o lugar diz {valor!r}")
    sha = dados.get("sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise ValueError("sha256 do manifesto ausente ou malformado")
    tamanho = dados.get("bytes")
    if not isinstance(tamanho, int) or isinstance(tamanho, bool) or tamanho <= 0:
        raise ValueError("bytes do manifesto ausente ou inválido")
    try:
        datetime.datetime.fromisoformat(str(dados.get("criado_em")))
    except ValueError as erro:
        raise ValueError("criado_em do manifesto ausente ou inválido") from erro
    return dados


def _arquivos_da_pasta(pasta: str) -> list[os.DirEntry]:
    """Arquivos regulares de uma pasta, sem seguir link simbólico."""
    with os.scandir(pasta) as itens:
        return sorted(
            (e for e in itens if e.is_file(follow_symlinks=False)), key=lambda e: e.name
        )


def _sandbox_de_pe(cache: list[bool]) -> bool:
    if not cache:
        cache.append(motor._sandbox_disponivel())
    return cache[0]


def reconstruir(*, aplicar: bool = False, projeto_slug: str | None = None) -> Relatorio:
    """Varre `projects/<slug>/<tipo>/` e cataloga o que ainda não está no catálogo."""
    relatorio = Relatorio(aplicado=aplicar)
    try:
        base = caminho_sob_raiz("projects")
    except ConfiguracaoInvalida as erro:
        raise motor.FalhaDeBackup(f"raiz de backup inválida: {erro}") from erro
    if not os.path.isdir(base):
        return relatorio

    # Qualquer situação conta, inclusive `removido`: o arquivo ainda existir com
    # uma linha de histórico não é motivo para uma segunda linha do mesmo caminho.
    with banco.conectar() as conexao:
        ja_no_catalogo = {
            linha["caminho_relativo"]
            for linha in conexao.execute("SELECT caminho_relativo FROM artefatos")
        }
    sandbox: list[bool] = []

    with os.scandir(base) as pastas:
        slugs = sorted(e.name for e in pastas if e.is_dir(follow_symlinks=False))

    for slug in slugs:
        if projeto_slug and slug != projeto_slug:
            continue
        try:
            por_slug(slug)
        except KeyError:
            relatorio.ignorados.append(f"{slug} (projeto desconhecido)")
            continue
        for tipo in TIPOS:
            pasta = os.path.join(base, slug, tipo)
            if not os.path.isdir(pasta) or os.path.islink(pasta):
                continue
            for entrada in _arquivos_da_pasta(pasta):
                nome = entrada.name
                if nome.endswith(_SUFIXO_MANIFESTO) or nome.endswith(".tmp"):
                    continue
                _tratar(slug, tipo, entrada, ja_no_catalogo, sandbox, relatorio, aplicar)

    if aplicar and relatorio.registrados:
        banco.registrar_evento(
            "catalogo.reconstruido",
            f"{len(relatorio.registrados)} artefato(s) catalogado(s) a partir dos manifestos; "
            f"{len(relatorio.rejeitados)} rejeitado(s); "
            f"{len(relatorio.nao_verificados)} sem verificação",
            severidade="aviso" if not relatorio.limpo else "info",
        )
    return relatorio


def _tratar(slug, tipo, entrada, ja_no_catalogo, sandbox, relatorio, aplicar) -> None:
    nome = entrada.name
    rotulo = f"{slug}/{tipo}/{nome}"
    relativo = motor._relativo(entrada.path)
    if relativo in ja_no_catalogo:
        relatorio.ja_catalogados.append(rotulo)
        return

    caminho_manifesto = entrada.path + _SUFIXO_MANIFESTO
    if not os.path.isfile(caminho_manifesto) or os.path.islink(caminho_manifesto):
        relatorio.rejeitados.append((rotulo, "sem manifesto ao lado"))
        return
    try:
        manifesto = _ler_manifesto(caminho_manifesto, slug, tipo, nome)
    except ValueError as erro:
        relatorio.rejeitados.append((rotulo, str(erro)))
        return

    if os.path.getsize(entrada.path) != manifesto["bytes"]:
        relatorio.rejeitados.append((rotulo, "tamanho diferente do manifesto"))
        return
    if motor.sha256_arquivo(entrada.path) != manifesto["sha256"]:
        relatorio.rejeitados.append((rotulo, "SHA-256 diferente do manifesto"))
        return

    try:
        if tipo == "banco":
            if not _sandbox_de_pe(sandbox):
                relatorio.nao_verificados.append(
                    f"{rotulo} (sandbox {CONTAINER_SANDBOX} indisponível; suba com: "
                    "docker compose -f compose.teste.yaml up -d)"
                )
                return
            motor._reler_dump(CONTAINER_SANDBOX, entrada.path)
        elif tipo == "codigo":
            motor.verificar_zip_codigo(entrada.path)
        else:
            motor.verificar_volume(entrada.path)
    except motor.FalhaDeBackup as erro:
        relatorio.rejeitados.append((rotulo, f"releitura reprovou: {erro}"))
        return

    if aplicar:
        duracao = manifesto.get("duracao_ms")
        banco.registrar_artefato(
            projeto=slug,
            tipo=tipo,
            caminho_relativo=relativo,
            bytes_=manifesto["bytes"],
            sha256=manifesto["sha256"],
            duracao_ms=duracao if isinstance(duracao, int) else 0,
            execucao_id=None,
            criado_em=str(manifesto["criado_em"]),
        )
        ja_no_catalogo.add(relativo)
    relatorio.registrados.append(rotulo)
