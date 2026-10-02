"""O tipo `volume`: a cópia `.tar.gz` do diretório de dados de um contêiner.

Existe para o Wealthfolio, que guarda o patrimônio num SQLite cifrado dentro
do volume `wealthfolio-teste-data`, fora do alcance de qualquer `pg_dump`. O
VPS copia o diretório com o contêiner pausado (`_manutencao/vps/backup-db.sh`)
e este lado busca, relê e cataloga a cópia como mais um artefato.

O que estes testes guardam:

- a releitura recusa a cópia que não serve, inclusive a que o `tarfile`
  sozinho deixaria passar (CRC errado no fim do gzip) e a que, extraída,
  escreveria fora do diretório de destino;
- `verificar` julga cópia de volume sem depender do sandbox, que só existe
  para reler dump de Postgres;
- o catálogo criado antes do tipo novo é migrado sem perder linha nem id;
- o projeto do Wealthfolio aparece completo quando tem cópia, e o botão
  "Novo backup" dele não deixa execução presa em "fila".
"""

from __future__ import annotations

import gzip
import io
import os
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import ANY, patch

import flask

import banco
import motor
import web
from projetos import por_slug

WEALTHFOLIO = por_slug("wealthfolio_teste_vps")


def _tar_gz(membros: list[tuple[str, bytes | None]], *, simbolicos=()) -> bytes:
    """Monta um `.tar.gz` como o do `docker cp ... | gzip -n`: `None` é
    diretório; `simbolicos` são pares (nome, alvo)."""
    bruto = io.BytesIO()
    with tarfile.open(fileobj=bruto, mode="w") as pacote:
        for nome, conteudo in membros:
            info = tarfile.TarInfo(nome)
            if conteudo is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                pacote.addfile(info)
            else:
                info.size = len(conteudo)
                pacote.addfile(info, io.BytesIO(conteudo))
        for nome, alvo in simbolicos:
            info = tarfile.TarInfo(nome)
            info.type = tarfile.SYMTYPE
            info.linkname = alvo
            pacote.addfile(info)
    return gzip.compress(bruto.getvalue(), mtime=0)


COPIA_BOA = _tar_gz([
    ("data", None),
    ("data/wealthfolio.db", b"sqlite cifrado" * 300),
    ("data/wealthfolio.db-wal", b"wal" * 100),
])


class _ComArquivo(unittest.TestCase):
    def setUp(self) -> None:
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)

    def _gravar(self, conteudo: bytes, nome: str = "copia.tar.gz") -> str:
        caminho = os.path.join(self.pasta.name, nome)
        with open(caminho, "wb") as destino:
            destino.write(conteudo)
        return caminho


class VerificarVolumeTests(_ComArquivo):
    def test_copia_integra_passa(self) -> None:
        motor.verificar_volume(self._gravar(COPIA_BOA))

    def test_copia_recusada(self) -> None:
        crc_errado = bytearray(COPIA_BOA)
        crc_errado[-8] ^= 0xFF  # o CRC32 fica nos 8 bytes finais
        casos = {
            "arquivo vazio": b"",
            "gzip cortado no meio": COPIA_BOA[: len(COPIA_BOA) // 2],
            # O `tarfile` lê os membros e para antes do fim do gzip: sozinho,
            # não percebe um CRC errado.
            "CRC do gzip errado": bytes(crc_errado),
            "não é gzip": b"isto nao e um tar.gz",
            "volume vazio": _tar_gz([("data", None), ("data/sub", None)]),
        }
        for descricao, conteudo in casos.items():
            with self.subTest(descricao), self.assertRaises(motor.FalhaDeBackup):
                motor.verificar_volume(self._gravar(conteudo))

    def test_membro_que_escreveria_fora_do_destino_e_recusado(self) -> None:
        """A cópia vem de outra máquina e um dia é extraída num disco de
        verdade: o filtro `data` da extração já precisa aprová-la aqui."""
        casos = {
            "caminho absoluto": _tar_gz([("/etc/cron.d/x", b"x")]),
            "subida com ..": _tar_gz([("data/../../fora", b"x")]),
            "link para fora": _tar_gz(
                [("data/wealthfolio.db", b"x")], simbolicos=[("data/atalho", "/etc/passwd")]
            ),
        }
        for descricao, conteudo in casos.items():
            with self.subTest(descricao), self.assertRaises(motor.FalhaDeBackup):
                motor.verificar_volume(self._gravar(conteudo))


class VerificarAcervoComVolumeTests(_ComArquivo):
    """`verificar` julga a cópia de volume, e sem precisar do sandbox."""

    def _executar(self, caminho: str) -> tuple[dict, list]:
        linha = {
            "id": 7,
            "projeto": WEALTHFOLIO.slug,
            "tipo": "volume",
            "situacao": "valido",
            "caminho_relativo": "projects/x/volume/x.tar.gz",
            "sha256": motor.sha256_arquivo(caminho),
            "bytes": os.path.getsize(caminho),
        }
        gravadas: list = []
        with (
            patch.object(banco, "listar_artefatos", return_value=[linha]),
            patch.object(
                banco, "marcar_situacao_artefato",
                side_effect=lambda id_, situacao: gravadas.append((id_, situacao)),
            ),
            patch.object(motor, "caminho_artefato", return_value=caminho),
            patch.object(motor, "estado_container", return_value=(False, False)),
            patch.object(motor, "_rodar", side_effect=AssertionError("volume não usa Docker")),
        ):
            return motor.verificar(), gravadas

    def test_copia_integra_e_conferida_sem_sandbox(self) -> None:
        contagem, gravadas = self._executar(self._gravar(COPIA_BOA))
        self.assertEqual(contagem["conferidos"], 1)
        self.assertEqual(contagem["nao_verificados"], 0)
        self.assertEqual(gravadas, [(7, "valido")])

    def test_copia_ilegivel_com_sha_certo_e_corrompida(self) -> None:
        # O SHA-256 confere com o catálogo (o arquivo é o mesmo que entrou),
        # mas a releitura não: o veredito é da releitura.
        contagem, gravadas = self._executar(self._gravar(COPIA_BOA[: len(COPIA_BOA) // 2]))
        self.assertEqual(contagem["corrompidos"], 1)
        self.assertEqual(gravadas, [(7, "corrompido")])


class MigracaoDoCatalogoTests(unittest.TestCase):
    """O catálogo de antes de 02/10/2026 tinha `CHECK (tipo IN ('banco',
    'codigo'))`, e o SQLite não altera CHECK: sem a migração, a primeira cópia
    de volume buscada do VPS seria recusada pelo próprio catálogo."""

    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.caminho = os.path.join(pasta.name, "catalogo.sqlite3")
        trava = patch.object(banco, "CAMINHO_CATALOGO", self.caminho)
        trava.start()
        self.addCleanup(trava.stop)

    def _catalogo_antigo(self) -> list[tuple]:
        """Cria o catálogo como a versão anterior criava e devolve as linhas."""
        esquema = banco.ESQUEMA.replace("('banco','codigo','volume')", "('banco','codigo')")
        self.assertNotIn("'volume'", esquema, "andaime: o esquema antigo ainda aceita volume")
        conexao = sqlite3.connect(self.caminho)
        conexao.executescript(esquema)
        for numero, tipo in enumerate(("banco", "codigo", "banco", "banco"), start=1):
            conexao.execute(
                "INSERT INTO artefatos (projeto, tipo, situacao, caminho_relativo, bytes,"
                " sha256, criado_em, fixado) VALUES ('mega_sena', ?, 'valido', ?, ?, ?, ?, ?)",
                (tipo, f"projects/mega_sena/{tipo}/{numero}", numero * 10, "a" * 64,
                 f"2026-09-0{numero}T03:00:00", numero % 2),
            )
        # O último id some do catálogo: o AUTOINCREMENT promete não reusá-lo.
        conexao.execute("DELETE FROM artefatos WHERE id = 4")
        conexao.commit()
        linhas = conexao.execute("SELECT * FROM artefatos ORDER BY id").fetchall()
        conexao.close()
        return linhas

    def _linhas(self) -> list[tuple]:
        conexao = sqlite3.connect(self.caminho)
        try:
            return conexao.execute("SELECT * FROM artefatos ORDER BY id").fetchall()
        finally:
            conexao.close()

    def _registrar_volume(self) -> int:
        return banco.registrar_artefato(
            projeto=WEALTHFOLIO.slug, tipo="volume",
            caminho_relativo="projects/wealthfolio_teste_vps/volume/x.tar.gz",
            bytes_=1, sha256="b" * 64, duracao_ms=1, execucao_id=None,
        )

    def test_catalogo_antigo_passa_a_aceitar_volume_sem_perder_nada(self) -> None:
        antes = self._catalogo_antigo()
        banco.criar_tabelas()
        self.assertEqual(self._linhas(), antes, "toda linha, com o mesmo id e os mesmos valores")
        novo = self._registrar_volume()
        self.assertGreater(novo, 4, "o id apagado antes da migração não pode voltar")
        self.assertEqual(
            [a["tipo"] for a in banco.artefatos_validos(WEALTHFOLIO.slug, "volume")], ["volume"]
        )

    def _consultar(self, sql: str) -> list[tuple]:
        conexao = sqlite3.connect(self.caminho)
        try:
            return conexao.execute(sql).fetchall()
        finally:
            conexao.close()

    def test_migracao_mantem_os_indices_e_nao_se_repete(self) -> None:
        self._catalogo_antigo()
        banco.criar_tabelas()
        # Logo depois da migração, sem esperar a próxima partida recriá-los.
        indices = self._consultar(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'artefatos'"
        )
        self.assertEqual(
            {nome for (nome,) in indices}, {"ix_artefatos_projeto", "ix_artefatos_situacao"}
        )
        self._registrar_volume()
        banco.criar_tabelas()  # segunda partida: nada a migrar
        self.assertEqual(
            self._consultar("SELECT name FROM sqlite_master WHERE name = 'artefatos_migracao'"), []
        )
        self.assertEqual(len(self._linhas()), 4)

    def test_catalogo_novo_ja_nasce_aceitando_volume(self) -> None:
        banco.criar_tabelas()
        self.assertEqual(self._registrar_volume(), 1)


class ProjetoDoWealthfolioTests(unittest.TestCase):
    def test_e_de_origem_vps_e_so_produz_volume(self) -> None:
        self.assertEqual(WEALTHFOLIO.ambiente, "vps")
        self.assertEqual(WEALTHFOLIO.tipos, ("volume",))
        self.assertEqual(WEALTHFOLIO.slug_servidor, "wealthfolio_teste")

    def test_novo_backup_pela_interface_fecha_a_execucao(self) -> None:
        """A interface abre a execução e só depois chama o motor. Recusar o
        tipo `volume` antes de fechá-la a deixaria em "fila" para sempre."""
        with (
            patch.object(motor.banco, "fechar_execucao") as fechar,
            patch.object(motor.banco, "registrar_evento"),
            patch.object(motor, "estado_container") as estado_container,
        ):
            with self.assertRaises(motor.FalhaDeBackup):
                motor.fazer_backup(WEALTHFOLIO, execucao_id=55)
        fechar.assert_called_once_with(55, "falha", ANY)
        estado_container.assert_not_called()

    def test_completo_e_ter_o_que_o_projeto_produz(self) -> None:
        self.assertTrue(web._completo(WEALTHFOLIO, {"banco": None, "volume": {"id": 1}}))
        self.assertFalse(web._completo(WEALTHFOLIO, {"banco": None, "volume": None}))
        local = por_slug("mega_sena")
        self.assertFalse(web._completo(local, {"banco": {"id": 1}, "codigo": None}))
        self.assertTrue(web._completo(local, {"banco": {"id": 1}, "codigo": {"id": 2}}))

    def test_pagina_do_projeto_abre_na_aba_do_volume(self) -> None:
        contextos: list[dict] = []

        def _registrar(remetente, template, context, **extra):
            contextos.append(context)

        web.app.config["TESTING"] = True
        flask.template_rendered.connect(_registrar, web.app)
        self.addCleanup(flask.template_rendered.disconnect, _registrar, web.app)
        with (
            patch.object(banco, "listar_artefatos", return_value=[]),
            patch.object(banco, "resumo_projeto", return_value={"ultima_execucao": None}),
        ):
            resposta = web.app.test_client().get(
                f"/projeto/{WEALTHFOLIO.slug}", headers={"Host": "127.0.0.1:5401"}
            )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(contextos[-1]["tipo"], "volume")
        self.assertEqual(list(contextos[-1]["rotulos"]), ["volume"])


class TelasComVolumeTests(unittest.TestCase):
    """Fumaça: toda tela de leitura abre com um catálogo que tem os três
    tipos. Os templates escreviam "banco" e "codigo" à mão em vários lugares,
    e um tipo novo é exatamente o que quebra uma tela que ninguém abriu."""

    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        raiz = os.path.join(pasta.name, "backups")
        os.makedirs(raiz)
        for trava in (
            patch.object(banco, "CAMINHO_CATALOGO", os.path.join(pasta.name, "c.sqlite3")),
            patch.object(web, "raiz_backup", return_value=raiz),
            patch.object(motor, "raiz_backup", return_value=raiz),
        ):
            trava.start()
            self.addCleanup(trava.stop)
        banco.criar_tabelas()
        for projeto, tipo in (
            ("mega_sena", "banco"), ("mega_sena", "codigo"), (WEALTHFOLIO.slug, "volume"),
        ):
            banco.registrar_artefato(
                projeto=projeto, tipo=tipo, caminho_relativo=f"projects/{projeto}/{tipo}/x",
                bytes_=10, sha256="c" * 64, duracao_ms=1, execucao_id=None,
            )
        web.app.config["TESTING"] = True
        self.cliente = web.app.test_client()

    def test_telas_de_leitura_abrem(self) -> None:
        rotas = [
            "/", "/projetos", "/backups", "/backups?tipo=volume", "/integridade",
            "/retencao", "/restaurar", "/historico", f"/projeto/{WEALTHFOLIO.slug}",
            "/projeto/mega_sena", "/projeto/mega_sena?tipo=codigo",
        ]
        for rota in rotas:
            with self.subTest(rota=rota):
                resposta = self.cliente.get(rota, headers={"Host": "127.0.0.1:5401"})
                self.assertEqual(resposta.status_code, 200)

    def test_painel_da_o_wealthfolio_por_completo_com_a_copia(self) -> None:
        contextos: list[dict] = []

        def _registrar(remetente, template, context, **extra):
            contextos.append(context)

        flask.template_rendered.connect(_registrar, web.app)
        self.addCleanup(flask.template_rendered.disconnect, _registrar, web.app)
        self.cliente.get("/", headers={"Host": "127.0.0.1:5401"})
        completos = contextos[-1]["completos"]
        self.assertTrue(completos[WEALTHFOLIO.slug], "tem a cópia de volume, que é tudo que produz")
        self.assertFalse(completos["mega_sena_vps"], "sem dump no catálogo")


if __name__ == "__main__":
    unittest.main()
