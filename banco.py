"""Catálogo SQLite: o que existe, se está íntegro e o que aconteceu.

Sem ORM e sem migrações. São três tabelas de forma fixa; `criar_tabelas()` usa
`IF NOT EXISTS` e é chamada a cada início. Uma camada de mapeamento aqui seria
mais código que o schema inteiro.

Cada função abre e fecha a própria conexão. O modo WAL permite que a interface
(Fase 2) leia enquanto o motor escreve, que é a única concorrência que existe.
"""

from __future__ import annotations

import datetime
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from projetos import CAMINHO_CATALOGO

SITUACOES_ARTEFATO = ("criando", "valido", "corrompido", "ausente", "removido")
SITUACOES_EXECUCAO = ("fila", "rodando", "sucesso", "falha")
TIPOS_ARTEFATO = ("banco", "codigo")
FINALIDADES = ("regular", "pre_restauracao")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS artefatos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto          TEXT NOT NULL,
    tipo             TEXT NOT NULL CHECK (tipo IN ('banco','codigo')),
    finalidade       TEXT NOT NULL DEFAULT 'regular'
                     CHECK (finalidade IN ('regular','pre_restauracao')),
    situacao         TEXT NOT NULL DEFAULT 'criando'
                     CHECK (situacao IN ('criando','valido','corrompido','ausente','removido')),
    caminho_relativo TEXT NOT NULL,
    bytes            INTEGER NOT NULL DEFAULT 0,
    sha256           TEXT,
    criado_em        TEXT NOT NULL,
    validado_em      TEXT,
    duracao_ms       INTEGER,
    fixado           INTEGER NOT NULL DEFAULT 0,
    execucao_id      INTEGER
);

CREATE TABLE IF NOT EXISTS execucoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto       TEXT NOT NULL,
    operacao      TEXT NOT NULL,
    situacao      TEXT NOT NULL DEFAULT 'fila'
                  CHECK (situacao IN ('fila','rodando','sucesso','falha')),
    fase          TEXT,
    progresso     INTEGER NOT NULL DEFAULT 0,
    pedido_em     TEXT NOT NULL,
    iniciado_em   TEXT,
    terminado_em  TEXT,
    erro          TEXT
);

CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id INTEGER,
    projeto     TEXT,
    momento     TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    severidade  TEXT NOT NULL DEFAULT 'info'
                CHECK (severidade IN ('info','aviso','erro')),
    mensagem    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_artefatos_projeto ON artefatos(projeto, tipo, criado_em DESC);
CREATE INDEX IF NOT EXISTS ix_artefatos_situacao ON artefatos(situacao);
CREATE INDEX IF NOT EXISTS ix_execucoes_pedido ON execucoes(pedido_em DESC);
CREATE INDEX IF NOT EXISTS ix_eventos_momento ON eventos(momento DESC);
"""


def agora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@contextmanager
def conectar() -> Iterator[sqlite3.Connection]:
    conexao = sqlite3.connect(CAMINHO_CATALOGO, timeout=30)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA journal_mode=WAL")
    conexao.execute("PRAGMA foreign_keys=ON")
    try:
        yield conexao
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def criar_tabelas() -> None:
    with conectar() as conexao:
        conexao.executescript(ESQUEMA)


# --------------------------------------------------------------------------
# Execuções
# --------------------------------------------------------------------------


def abrir_execucao(projeto: str, operacao: str) -> int:
    with conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO execucoes (projeto, operacao, situacao, pedido_em, iniciado_em)"
            " VALUES (?, ?, 'rodando', ?, ?)",
            (projeto, operacao, agora(), agora()),
        )
        return int(cursor.lastrowid)


def enfileirar_execucao(projeto: str, operacao: str) -> int:
    """Usada pela interface: cria o pedido, quem executa é o worker."""
    with conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO execucoes (projeto, operacao, situacao, pedido_em)"
            " VALUES (?, ?, 'fila', ?)",
            (projeto, operacao, agora()),
        )
        return int(cursor.lastrowid)


def marcar_fase(execucao_id: int, fase: str, progresso: int) -> None:
    with conectar() as conexao:
        conexao.execute(
            "UPDATE execucoes SET fase = ?, progresso = ?, situacao = 'rodando',"
            " iniciado_em = COALESCE(iniciado_em, ?) WHERE id = ?",
            (fase, progresso, agora(), execucao_id),
        )


def fechar_execucao(execucao_id: int, situacao: str, erro: str | None = None) -> None:
    with conectar() as conexao:
        conexao.execute(
            "UPDATE execucoes SET situacao = ?, erro = ?, terminado_em = ?,"
            " progresso = CASE WHEN ? = 'sucesso' THEN 100 ELSE progresso END"
            " WHERE id = ?",
            (situacao, erro, agora(), situacao, execucao_id),
        )


def proxima_da_fila() -> sqlite3.Row | None:
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM execucoes WHERE situacao = 'fila' ORDER BY id LIMIT 1"
        ).fetchone()


# --------------------------------------------------------------------------
# Artefatos
# --------------------------------------------------------------------------


def registrar_artefato(
    *,
    projeto: str,
    tipo: str,
    caminho_relativo: str,
    bytes_: int,
    sha256: str,
    duracao_ms: int,
    execucao_id: int | None,
    finalidade: str = "regular",
) -> int:
    """Grava um artefato já verificado. Nada entra aqui como 'valido' sem ter
    passado pela releitura em `motor.verificar_*`."""
    with conectar() as conexao:
        cursor = conexao.execute(
            "INSERT INTO artefatos (projeto, tipo, finalidade, situacao, caminho_relativo,"
            " bytes, sha256, criado_em, validado_em, duracao_ms, execucao_id)"
            " VALUES (?, ?, ?, 'valido', ?, ?, ?, ?, ?, ?, ?)",
            (
                projeto,
                tipo,
                finalidade,
                caminho_relativo,
                bytes_,
                sha256,
                agora(),
                agora(),
                duracao_ms,
                execucao_id,
            ),
        )
        return int(cursor.lastrowid)


def marcar_situacao_artefato(artefato_id: int, situacao: str) -> None:
    with conectar() as conexao:
        conexao.execute(
            "UPDATE artefatos SET situacao = ? WHERE id = ?", (situacao, artefato_id)
        )


def fixar_artefato(artefato_id: int, fixado: bool) -> None:
    with conectar() as conexao:
        conexao.execute(
            "UPDATE artefatos SET fixado = ? WHERE id = ?", (1 if fixado else 0, artefato_id)
        )


def artefatos_validos(projeto: str, tipo: str, finalidade: str = "regular") -> list[sqlite3.Row]:
    """Mais recentes primeiro."""
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM artefatos WHERE projeto = ? AND tipo = ? AND finalidade = ?"
            " AND situacao = 'valido' ORDER BY criado_em DESC, id DESC",
            (projeto, tipo, finalidade),
        ).fetchall()


def listar_artefatos(projeto: str | None = None, limite: int = 200) -> list[sqlite3.Row]:
    with conectar() as conexao:
        if projeto:
            return conexao.execute(
                "SELECT * FROM artefatos WHERE projeto = ? AND situacao <> 'removido'"
                " ORDER BY criado_em DESC, id DESC LIMIT ?",
                (projeto, limite),
            ).fetchall()
        return conexao.execute(
            "SELECT * FROM artefatos WHERE situacao <> 'removido'"
            " ORDER BY criado_em DESC, id DESC LIMIT ?",
            (limite,),
        ).fetchall()


def obter_artefato(artefato_id: int) -> sqlite3.Row | None:
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM artefatos WHERE id = ?", (artefato_id,)
        ).fetchone()


# --------------------------------------------------------------------------
# Eventos e consultas de apresentação
# --------------------------------------------------------------------------


def registrar_evento(
    tipo: str,
    mensagem: str,
    *,
    projeto: str | None = None,
    execucao_id: int | None = None,
    severidade: str = "info",
) -> None:
    with conectar() as conexao:
        conexao.execute(
            "INSERT INTO eventos (execucao_id, projeto, momento, tipo, severidade, mensagem)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (execucao_id, projeto, agora(), tipo, severidade, mensagem),
        )


def listar_execucoes(limite: int = 50) -> list[sqlite3.Row]:
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM execucoes ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()


def obter_execucao(execucao_id: int) -> sqlite3.Row | None:
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM execucoes WHERE id = ?", (execucao_id,)
        ).fetchone()


def listar_eventos(limite: int = 100) -> list[sqlite3.Row]:
    with conectar() as conexao:
        return conexao.execute(
            "SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()


def resumo_projeto(slug: str) -> dict[str, Any]:
    """Último artefato válido de cada tipo, para o painel."""
    resumo: dict[str, Any] = {}
    with conectar() as conexao:
        for tipo in TIPOS_ARTEFATO:
            linha = conexao.execute(
                "SELECT * FROM artefatos WHERE projeto = ? AND tipo = ?"
                " AND finalidade = 'regular' AND situacao = 'valido'"
                " ORDER BY criado_em DESC, id DESC LIMIT 1",
                (slug, tipo),
            ).fetchone()
            resumo[tipo] = dict(linha) if linha else None
        ultima = conexao.execute(
            "SELECT * FROM execucoes WHERE projeto = ? ORDER BY id DESC LIMIT 1", (slug,)
        ).fetchone()
        resumo["ultima_execucao"] = dict(ultima) if ultima else None
    return resumo
