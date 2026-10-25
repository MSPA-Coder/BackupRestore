"""Os projetos cobertos pelo backup, e onde tudo mora.

Fonte de verdade em Python, não no banco: são poucos projetos, que mudam de ano
em ano. Uma tabela editável pela interface custaria CRUD, validação e telas para
resolver um problema que uma lista resolve.

Os nomes de contêiner foram levantados do `compose.yaml` de cada projeto.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from configuracao import SERVIDOR_PRINCIPAL

VARIAVEL_RAIZ_PROJETOS = "BACKUPRESTORE_RAIZ_PROJETOS"


def resolver_raiz_projetos(
    valor: str | None = None, *, pasta_aplicacao: str | os.PathLike[str] | None = None
) -> str:
    """Resolve a pasta que contém os projetos locais.

    O override é útil quando os checkouts não são irmãos deste repositório. Sem
    ele, a raiz é o diretório pai do próprio BackupRestore.
    """
    informado = valor if valor is not None else os.environ.get(VARIAVEL_RAIZ_PROJETOS)
    if informado is not None:
        texto = os.path.expanduser(os.path.expandvars(str(informado).strip()))
        if not texto:
            raise ValueError(f"{VARIAVEL_RAIZ_PROJETOS} não pode ser vazia")
        return str(Path(texto).resolve(strict=False))
    aplicacao = Path(pasta_aplicacao or __file__).resolve(strict=False)
    if pasta_aplicacao is None:
        aplicacao = aplicacao.parent
    return str(aplicacao.parent.resolve(strict=False))


RAIZ_PROJETOS = resolver_raiz_projetos()

# O catálogo SQLite fica fora da pasta de backup de propósito: copiar os
# artefatos para outro lugar não deve levar junto o índice do que existe.
CAMINHO_CATALOGO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "catalogo.sqlite3"
)

# Contêiner descartável de `compose.yaml`. É o único destino de
# restauração que o motor aceita.
CONTAINER_SANDBOX = "backuprestore-sandbox"

RETENCAO_PADRAO = 10


AMBIENTE_LOCAL = "local"
AMBIENTE_VPS = "vps"

# Retenção do catálogo local para artefatos de origem VPS. Acompanha os 14
# dias que o servidor guarda por conta própria.
RETENCAO_VPS = 14

# Nome do alvo SSH de onde um projeto `ambiente="vps"` é buscado. Cada nome
# corresponde a um alvo gravado por `cli.py configurar-vps --servidor <nome>`.
# Os projetos fixos abaixo vêm do servidor principal (o portal saiu do VPS2 em
# 10/2026 e `mp_portal_vps` saiu do catálogo em 19/10/2026). Os sites e a
# configuração de cada servidor, inclusive o segundo, são DESCOBERTOS: ver
# `PROJETOS_DESCOBERTOS` mais abaixo.


@dataclass(frozen=True)
class Projeto:
    slug: str
    nome: str
    pasta: str
    container: str
    usuario: str
    banco: str
    ambiente: str = AMBIENTE_LOCAL
    retencao: int = RETENCAO_PADRAO
    tipos: tuple[str, ...] = field(default=("banco", "codigo"))
    servidor: str = SERVIDOR_PRINCIPAL
    # Pasta do projeto em `~/backups` no servidor, quando não é o slug sem o
    # sufixo `_vps` (os descobertos de outro servidor terminam em `_vps2`).
    slug_remoto: str = ""

    @property
    def caminho(self) -> str:
        # Projetos de ambiente != local não têm pasta local: a origem deles é
        # o servidor, não um checkout deste host.
        if not self.pasta:
            return f"(sem pasta local — ambiente {self.ambiente!r})"
        return os.path.join(RAIZ_PROJETOS, self.pasta)

    @property
    def e_repo_git(self) -> bool:
        if not self.pasta:
            return False
        return os.path.isdir(os.path.join(self.caminho, ".git"))

    @property
    def slug_servidor(self) -> str:
        """Apelido usado do lado do VPS — sem o sufixo ``_vps`` do catálogo
        local. É o que `backup-agent.sh` (Camada 2) usa para nomear pastas e
        arquivos: o servidor não sabe nada sobre apelidos locais."""
        if self.ambiente != AMBIENTE_VPS:
            raise ValueError(f"{self.slug}: slug_servidor só existe para projetos vps")
        return self.slug_remoto or self.slug.removesuffix("_vps")


PROJETOS_FIXOS: tuple[Projeto, ...] = (
    Projeto(
        slug="conforto_termico",
        nome="Conforto Térmico",
        pasta="ConfortoTermico",
        container="conforto-termico-postgres-1",
        usuario="conforto",
        banco="conforto_termico",
    ),
    Projeto(
        slug="mega_sena",
        nome="Mega-Sena",
        pasta="MegaSena",
        container="mega-sena-postgres-1",
        usuario="mega_sena",
        banco="mega_sena",
    ),
    Projeto(
        slug="controle_bancario",
        nome="Controle Bancário",
        pasta="ControleBancario",
        container="controle-bancario-postgres-1",
        usuario="controle_bancario",
        banco="controle_bancario",
    ),
    Projeto(
        slug="controle_renda_variavel",
        nome="Controle Renda Variável",
        pasta="ControleRendaVariavel",
        container="controle-renda-variavel-db-1",
        usuario="investimentos",
        banco="investimentos",
    ),
    # Projetos de produção no VPS, sincronizados pela Camada 2. Mesmos
    # apelidos de contêiner, usuário e banco dos originais — são os nomes reais
    # do lado de lá — mas em `ambiente="vps"`, sem pasta local e sem tipo
    # "codigo": o código do VPS é espelho do `main`, não um artefato
    # próprio. `motor.fazer_backup` recusa qualquer projeto que não seja
    # `AMBIENTE_LOCAL` — a produção por contêiner não existe para eles; os
    # artefatos chegam pela sincronização com o servidor implementada em vps.py.
    Projeto(
        slug="conforto_termico_vps",
        nome="Conforto Térmico (VPS)",
        pasta="",
        container="conforto-termico-postgres-1",
        usuario="conforto",
        banco="conforto_termico",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("banco",),
    ),
    Projeto(
        slug="mega_sena_vps",
        nome="Mega-Sena (VPS)",
        pasta="",
        container="mega-sena-postgres-1",
        usuario="mega_sena",
        banco="mega_sena",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("banco",),
    ),
    Projeto(
        slug="controle_bancario_vps",
        nome="Controle Bancário (VPS)",
        pasta="",
        container="controle-bancario-postgres-1",
        usuario="controle_bancario",
        banco="controle_bancario",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("banco",),
    ),
    Projeto(
        slug="controle_renda_variavel_vps",
        nome="Controle Renda Variável (VPS)",
        pasta="",
        container="controle-renda-variavel-db-1",
        usuario="investimentos",
        banco="investimentos",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("banco",),
    ),
    # `networth` e `networth_vps` saíram do catálogo em 29/09/2026: o NetWorth (prova
    # de conceito) foi aposentado, local e no VPS
    # (o Wealthfolio opera no domínio dele). Os dumps já catalogados ficam no
    # disco, sem retenção nem verificação novas. O mesmo vale para `mp_portal_vps`,
    # retirado em 19/10/2026.
    # O Wealthfolio não tem Postgres: o patrimônio fica num SQLite dentro do
    # volume `wealthfolio-teste-data`, e o que vem do servidor é a cópia desse
    # diretório (`volume/*.tar.gz`), feita lá com o contêiner pausado. Usuário
    # e banco ficam vazios porque não há `pg_dump` nem restauração automática;
    # a volta é manual e precisa da chave mestra do kit (RESTAURAR.md). O slug
    # segue o projeto do Compose (`wealthfolio-teste`): se ele for renomeado
    # no servidor, este muda junto.
    Projeto(
        slug="wealthfolio_teste_vps",
        nome="Wealthfolio (VPS)",
        pasta="",
        container="wealthfolio-teste-wealthfolio-1",
        usuario="",
        banco="",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("volume",),
    ),
)

# ---------------------------------------------------------------------------
# Projetos descobertos nos servidores (25/10/2026)
#
# Os sites que o nginx publica e a configuração de cada VPS não são listados
# aqui: o `backup-db.sh` do servidor os descobre sozinho (pastas `site_<nome>` e
# `configuracao_servidor` em `~/backups`), e a sincronização registra cada pasta
# nova neste arquivo local na primeira vez que a vê. Assim um site novo entra no
# backup sem ninguém editar este arquivo, e continua aparecendo na interface
# depois de sair do ar (as cópias antigas ficam no catálogo).
#
# Só cópias de arquivos (`volume`) entram sozinhas. Um banco novo precisa de
# usuário, banco e contêiner para a restauração, então continua sendo
# cadastrado à mão em `PROJETOS_FIXOS`; a sincronização avisa quando vê um.
# ---------------------------------------------------------------------------
VARIAVEL_DESCOBERTOS = "BACKUPRESTORE_PROJETOS_DESCOBERTOS"
ARQUIVO_DESCOBERTOS = Path(
    os.environ.get(VARIAVEL_DESCOBERTOS)
    or Path(__file__).resolve().with_name("projetos-descobertos.local.json")
)
_SLUG_REMOTO = re.compile(r"^[a-z_]+$")


def slug_local(slug_remoto: str, servidor: str) -> str:
    """`site_villa_rica` do principal vira `site_villa_rica_vps`; do servidor
    `vps2`, `site_villa_rica_vps2`. O sufixo evita colisão entre servidores."""
    sufixo = "vps" if servidor == SERVIDOR_PRINCIPAL else servidor.replace("-", "_")
    return f"{slug_remoto}_{sufixo}"


def nome_descoberto(slug_remoto: str, servidor: str) -> str:
    rotulo = "VPS" if servidor == SERVIDOR_PRINCIPAL else servidor.upper()
    if slug_remoto == "configuracao_servidor":
        return f"Configuração do servidor ({rotulo})"
    nome = slug_remoto.removeprefix("site_").replace("_", "-")
    return f"Site {nome} ({rotulo})"


def projeto_descoberto(slug_remoto: str, servidor: str) -> Projeto:
    if not _SLUG_REMOTO.match(slug_remoto):
        raise ValueError(f"slug remoto inválido: {slug_remoto!r}")
    return Projeto(
        slug=slug_local(slug_remoto, servidor),
        nome=nome_descoberto(slug_remoto, servidor),
        pasta="",
        container="",
        usuario="",
        banco="",
        ambiente=AMBIENTE_VPS,
        retencao=RETENCAO_VPS,
        tipos=("volume",),
        servidor=servidor,
        slug_remoto=slug_remoto,
    )


def carregar_descobertos(arquivo: Path | None = None) -> tuple[Projeto, ...]:
    """Os projetos já descobertos. Arquivo ausente ou ilegível é lista vazia:
    a próxima sincronização o refaz a partir do que os servidores mostram."""
    try:
        dados = json.loads(Path(arquivo or ARQUIVO_DESCOBERTOS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    projetos: list[Projeto] = []
    for item in dados if isinstance(dados, list) else []:
        try:
            projetos.append(projeto_descoberto(str(item["slug_remoto"]), str(item["servidor"])))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(projetos)


def registrar_descobertos(novos: list[Projeto], arquivo: Path | None = None) -> None:
    """Acrescenta `novos` ao arquivo, sem perder os que já estavam lá."""
    destino = Path(arquivo or ARQUIVO_DESCOBERTOS)
    atuais = {p.slug: p for p in carregar_descobertos(destino)}
    for projeto in novos:
        atuais.setdefault(projeto.slug, projeto)
    conteudo = [
        {"slug_remoto": p.slug_servidor, "servidor": p.servidor}
        for p in sorted(atuais.values(), key=lambda p: (p.servidor, p.slug))
    ]
    temporario = destino.with_name(destino.name + ".tmp")
    temporario.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    os.replace(temporario, destino)


def _juntar(fixos: tuple[Projeto, ...], descobertos: tuple[Projeto, ...]) -> tuple[Projeto, ...]:
    vistos = {p.slug for p in fixos}
    remotos = {(p.servidor, p.slug_servidor) for p in fixos if p.ambiente == AMBIENTE_VPS}
    extras = tuple(
        p for p in descobertos
        if p.slug not in vistos and (p.servidor, p.slug_servidor) not in remotos
    )
    return fixos + extras


PROJETOS: tuple[Projeto, ...] = _juntar(PROJETOS_FIXOS, carregar_descobertos())

# Inventário dos contêineres associados aos projetos. A restauração automática
# não depende desta lista: sua trava é a allowlist exata de CONTAINER_SANDBOX.
CONTAINERS_PROTEGIDOS = frozenset((p.ambiente, p.container) for p in PROJETOS)


def por_slug(slug: str) -> Projeto:
    for projeto in PROJETOS:
        if projeto.slug == slug:
            return projeto
    conhecidos = ", ".join(p.slug for p in PROJETOS)
    raise KeyError(f"Projeto desconhecido: {slug!r}. Conhecidos: {conhecidos}")
