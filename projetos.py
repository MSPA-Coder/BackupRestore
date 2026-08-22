"""Os projetos cobertos pelo backup, e onde tudo mora.

Fonte de verdade em Python, não no banco: são quatro projetos que mudam de ano
em ano. Uma tabela editável pela interface custaria CRUD, validação e telas para
resolver um problema que uma lista resolve.

Os nomes de contêiner foram levantados do `compose.yaml` de cada projeto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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

# Contêiner descartável de `compose.teste.yaml`. É o único destino de
# restauração que o motor aceita.
CONTAINER_SANDBOX = "backuprestore-sandbox"

RETENCAO_PADRAO = 10


AMBIENTE_LOCAL = "local"
AMBIENTE_VPS = "vps"

# Retenção do catálogo local para artefatos de origem VPS. Acompanha os 14
# dias que o servidor guarda por conta própria.
RETENCAO_VPS = 14


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
        return self.slug.removesuffix("_vps")


PROJETOS: tuple[Projeto, ...] = (
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
)

# Inventário dos contêineres associados aos projetos. A restauração automática
# não depende desta lista: sua trava é a allowlist exata de CONTAINER_SANDBOX.
CONTAINERS_PROTEGIDOS = frozenset((p.ambiente, p.container) for p in PROJETOS)


def por_slug(slug: str) -> Projeto:
    for projeto in PROJETOS:
        if projeto.slug == slug:
            return projeto
    conhecidos = ", ".join(p.slug for p in PROJETOS)
    raise KeyError(f"Projeto desconhecido: {slug!r}. Conhecidos: {conhecidos}")
