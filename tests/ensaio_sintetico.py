"""Ensaio operacional real, limitado ao sandbox, sem ler backups ou bancos do operador.

Usa o runtime já configurado do BackupRestore no host, como os comandos `ensaio`
e `verificar`. Docker, pg_dump, pg_restore, catálogo e hashes não são dublês.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import banco  # noqa: E402
import configuracao  # noqa: E402
import motor  # noqa: E402
import restaurar  # noqa: E402
from projetos import CONTAINER_SANDBOX, Projeto  # noqa: E402

ORIGEM = "validacao_sintetica_origem"
DESTINO = "validacao_sintetica_destino"


def sql(base, comando):
    feito = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_SANDBOX, "psql", "-U", "sandbox", "-d", base,
         "-v", "ON_ERROR_STOP=1", "-tA"],
        input=comando.encode("utf-8"), capture_output=True, check=True,
    )
    return feito.stdout.decode("utf-8").strip()


def main():
    if motor.estado_container(CONTAINER_SANDBOX) != (True, True):
        raise RuntimeError("O sandbox já preparado precisa estar disponível.")
    criados = []
    # Não reaproveita nem apaga bancos que já existam, mesmo dentro do sandbox.
    existentes = sql("postgres", "SELECT datname FROM pg_database").splitlines()
    if any(nome in existentes for nome in (ORIGEM, DESTINO)):
        raise RuntimeError("Os nomes reservados ao ensaio já existem; nenhum dado foi alterado.")
    try:
        for nome in (ORIGEM, DESTINO):
            sql("postgres", f'CREATE DATABASE "{nome}"')
            criados.append(nome)
        sql(ORIGEM, """CREATE TABLE valores (id integer primary key, valor numeric(24,8), nota text);
            INSERT INTO valores VALUES (1, 123456789.12345678, 'ação'), (2, -0.00000001, NULL);
            CREATE TABLE vazio (id integer);
        """)
        sql(DESTINO, "CREATE TABLE preservar (id integer); INSERT INTO preservar VALUES (42)")
        with tempfile.TemporaryDirectory(prefix="backuprestore-ensaio-") as temporario:
            raiz = Path(temporario) / "backups"
            with (
                patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(temporario) / "config.json")),
                patch.object(banco, "CAMINHO_CATALOGO", str(Path(temporario) / "catalogo.sqlite3")),
                patch.dict(os.environ, {
                    "BACKUPRESTORE_RAIZ_BACKUP": str(raiz),
                    "BACKUPRESTORE_RAIZ_PERMITIDA": str(raiz),
                }),
            ):
                banco.criar_tabelas()
                projeto = Projeto("ensaio_sintetico", "Ensaio sintético", "", CONTAINER_SANDBOX,
                                  "sandbox", ORIGEM, tipos=("banco",))
                motor.fazer_backup(projeto)
                artefato = banco.listar_artefatos(projeto.slug)[0]
                resultado = restaurar.restaurar(
                    artefato["id"], container_destino=CONTAINER_SANDBOX,
                    banco_destino=DESTINO, usuario_destino="sandbox", confirmacao=DESTINO,
                )
                consulta = "SELECT id, valor, COALESCE(nota, '<nulo>') FROM valores ORDER BY id"
                assert sql(DESTINO, consulta) == sql(ORIGEM, consulta)
                assert sql(DESTINO, "SELECT count(*) FROM vazio") == "0"
                assert sql(DESTINO, "SELECT to_regclass('preservar') IS NULL") == "t"
                seguranca = Path(resultado["dump_de_seguranca"])
                assert seguranca.is_file() and seguranca.stat().st_size > 0
                sql(DESTINO, "UPDATE valores SET valor = 7 WHERE id = 1")
                restaurar.restaurar(
                    artefato["id"], container_destino=CONTAINER_SANDBOX,
                    banco_destino=DESTINO, usuario_destino="sandbox", confirmacao=DESTINO,
                )
                assert sql(DESTINO, consulta) == sql(ORIGEM, consulta)
                assert banco.listar_execucoes()[0]["situacao"] == "sucesso"
                assert motor.estado_container(CONTAINER_SANDBOX) == (True, True)
                print(json.dumps({
                    "resultado": "aprovado", "linhas_restauradas": 2,
                    "decimal_e_unicode_preservados": True, "tabela_vazia_preservada": True,
                    "dump_de_seguranca_verificado": True, "reexecucao_restaurou_mesmo_estado": True,
                    "catalogo_isolado": True,
                }, ensure_ascii=False))
    finally:
        # Apenas nomes constantes, conferidos como inexistentes antes da criação.
        for nome in reversed(criados):
            sql("postgres", f'DROP DATABASE "{nome}" WITH (FORCE)')


if __name__ == "__main__":
    main()
