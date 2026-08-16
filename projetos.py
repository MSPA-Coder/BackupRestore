"""Os projetos cobertos pelo backup, e onde tudo mora.

Fonte de verdade em Python, não no banco: são quatro projetos que mudam de ano
em ano. Uma tabela editável pela interface custaria CRUD, validação e telas para
resolver um problema que uma lista resolve.

Os nomes de contêiner foram levantados do `compose.yaml` de cada projeto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

RAIZ_PROJETOS = r"C:\Users\MSPA\Dropbox\Programacao\VSCodeProjects"

# O catálogo SQLite fica fora da pasta de backup de propósito: copiar os
# artefatos para outro lugar não deve levar junto o índice do que existe.
CAMINHO_CATALOGO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "catalogo.sqlite3"
)

# Contêiner descartável de `compose.teste.yaml`. É o único destino de
# restauração que o motor aceita.
CONTAINER_SANDBOX = "backuprestore-sandbox"

RETENCAO_PADRAO = 10


@dataclass(frozen=True)
class Projeto:
    slug: str
    nome: str
    pasta: str
    container: str
    usuario: str
    banco: str
    retencao: int = RETENCAO_PADRAO
    tipos: tuple[str, ...] = field(default=("banco", "codigo"))

    @property
    def caminho(self) -> str:
        return os.path.join(RAIZ_PROJETOS, self.pasta)

    @property
    def e_repo_git(self) -> bool:
        return os.path.isdir(os.path.join(self.caminho, ".git"))


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
)

# Guarda-chuva da regra 6. Restaurar é a única operação destrutiva do sistema, e
# o erro que custa caro é acertar o arquivo e errar o destino. Estes nomes são
# recusados por `restaurar.py` antes de qualquer confirmação — não há flag que
# libere.
CONTAINERS_PROTEGIDOS = frozenset(p.container for p in PROJETOS)


def por_slug(slug: str) -> Projeto:
    for projeto in PROJETOS:
        if projeto.slug == slug:
            return projeto
    conhecidos = ", ".join(p.slug for p in PROJETOS)
    raise KeyError(f"Projeto desconhecido: {slug!r}. Conhecidos: {conhecidos}")
