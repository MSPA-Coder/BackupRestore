from __future__ import annotations

import datetime
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import banco
import cli
import configuracao
import motor
import vps
from projetos import CONTAINER_SANDBOX, PROJETOS

PROJETO_VPS = next(p for p in PROJETOS if p.ambiente == "vps")
PROJETO_LOCAL = next(p for p in PROJETOS if p.ambiente == "local")

ALVO = {"host": "vps.exemplo", "usuario": "ubuntu", "chave": "/chave"}


@contextmanager
def _ambiente_raiz(diretorio: str):
    """Isola raiz de backup e configuração local num diretório temporário —
    sem isto, `raiz_backup()` lê o `configuracao.local.json` real deste PC
    (já configurado por `cli.py configurar-raiz`) e o env var abaixo nunca
    vence, porque a chave do arquivo tem prioridade."""
    raiz = Path(diretorio, "backups")
    with (
        patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(diretorio, "config.json"))),
        patch.dict(
            os.environ,
            {
                configuracao.VARIAVEL_RAIZ_PERMITIDA: str(raiz),
                "BACKUPRESTORE_RAIZ_BACKUP": str(raiz),
            },
            clear=False,
        ),
    ):
        yield


class ListagemRemotaTests(unittest.TestCase):
    def test_parseia_linha_valida(self) -> None:
        nome = f"{PROJETO_VPS.slug_servidor}_banco_20260820_040329.dump"
        linha = (
            f"{PROJETO_VPS.slug_servidor}/{nome} 41264 "
            "3f9124a1efe188c1a82aa5bc609253d42b0d84fe37cc9be7946d86b86273a688\n"
        )
        processo = subprocess.CompletedProcess(args=[], returncode=0, stdout=linha.encode(), stderr=b"")
        with patch.object(vps, "_ssh", return_value=processo):
            dumps = vps.listar_remoto(ALVO)
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0].slug_servidor, PROJETO_VPS.slug_servidor)
        self.assertEqual(dumps[0].arquivo, nome)
        self.assertEqual(dumps[0].bytes, 41264)
        self.assertEqual(dumps[0].carimbo, "20260820_040329")

    def test_linha_mal_formada_e_recusada(self) -> None:
        processo = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"isso nao e uma linha valida\n", stderr=b""
        )
        with patch.object(vps, "_ssh", return_value=processo):
            with self.assertRaises(vps.FalhaDeSincronizacao):
                vps.listar_remoto(ALVO)

    def test_listar_com_erro_ssh_e_recusado(self) -> None:
        processo = subprocess.CompletedProcess(
            args=[], returncode=255, stdout=b"", stderr=b"Connection refused\n"
        )
        with patch.object(vps, "_ssh", return_value=processo):
            with self.assertRaises(vps.FalhaDeSincronizacao):
                vps.listar_remoto(ALVO)


class CarimboTests(unittest.TestCase):
    def test_converte_utc_do_servidor_para_local(self) -> None:
        resultado = vps._criado_em_do_carimbo("20260820_060015")
        esperado_utc = datetime.datetime(2026, 8, 20, 6, 0, 15, tzinfo=datetime.timezone.utc)
        esperado = esperado_utc.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        self.assertEqual(resultado, esperado)


class ApagarRemotoTests(unittest.TestCase):
    def _dump(self) -> vps.DumpRemoto:
        return vps.DumpRemoto(
            slug_servidor="x", arquivo="x_banco_20260819_000000.dump",
            caminho_remoto="x/x_banco_20260819_000000.dump", bytes=1, sha256="a" * 64,
            carimbo="20260819_000000",
        )

    def test_sucesso(self) -> None:
        processo = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"apagado: x\n", stderr=b"")
        with patch.object(vps, "_ssh", return_value=processo):
            self.assertEqual(vps._apagar_remoto(ALVO, self._dump()), "apagado")

    def test_recusa_por_ser_o_mais_recente_e_mantido(self) -> None:
        processo = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"",
            stderr="ERRO: recusado: é o dump mais recente de x\n".encode("utf-8"),
        )
        with patch.object(vps, "_ssh", return_value=processo):
            self.assertEqual(vps._apagar_remoto(ALVO, self._dump()), "mantido")

    def test_outra_falha_vira_aviso_nao_erro(self) -> None:
        processo = subprocess.CompletedProcess(
            args=[], returncode=255, stdout=b"", stderr=b"Connection timed out\n"
        )
        with patch.object(vps, "_ssh", return_value=processo):
            marca = vps._apagar_remoto(ALVO, self._dump())
        self.assertTrue(marca.startswith("aviso:"))


class VerificarNoSandboxTests(unittest.TestCase):
    def test_recusa_se_sandbox_nao_existe(self) -> None:
        with patch.object(motor, "estado_container", return_value=(False, False)):
            with self.assertRaises(vps.FalhaDeSincronizacao):
                vps.verificar_dump_no_sandbox("/tmp/algum.dump")

    def test_usa_pg_restore_list_no_container_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            caminho = Path(diretorio, "x.dump")
            caminho.write_bytes(b"conteudo")
            with (
                patch.object(motor, "estado_container", return_value=(True, True)),
                patch.object(motor, "_rodar") as rodar,
            ):
                rodar.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=b"", stderr=b""
                )
                vps.verificar_dump_no_sandbox(str(caminho))
            comando = rodar.call_args.args[0]
        self.assertEqual(comando, ["docker", "exec", "-i", CONTAINER_SANDBOX, "pg_restore", "--list"])


class BuscarECatalogarTests(unittest.TestCase):
    def test_sucesso_grava_artefato_com_carimbo_do_servidor(self) -> None:
        conteudo = b"conteudo-fake-do-dump"
        sha256 = hashlib.sha256(conteudo).hexdigest()
        nome = f"{PROJETO_VPS.slug_servidor}_banco_20260819_235959.dump"
        dump = vps.DumpRemoto(
            slug_servidor=PROJETO_VPS.slug_servidor, arquivo=nome,
            caminho_remoto=f"{PROJETO_VPS.slug_servidor}/{nome}",
            bytes=len(conteudo), sha256=sha256, carimbo="20260819_235959",
        )

        def _enviar_fake(alvo, dump_arg, destino):
            Path(destino).write_bytes(conteudo)

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            with (
                patch.object(vps, "enviar_remoto", side_effect=_enviar_fake) as enviar,
                patch.object(vps, "verificar_dump_no_sandbox") as verificar,
                patch.object(banco, "registrar_artefato", return_value=1) as registrar,
                patch.object(banco, "registrar_evento"),
            ):
                ok = vps._buscar_e_catalogar(PROJETO_VPS, ALVO, dump, execucao_id=42)
            caminho_final = configuracao.caminho_sob_raiz("projects", PROJETO_VPS.slug, "banco", nome)

            self.assertTrue(ok)
            self.assertTrue(os.path.exists(caminho_final))
            self.assertFalse(os.path.exists(caminho_final + ".tmp"))

        enviar.assert_called_once()
        verificar.assert_called_once()
        registrar.assert_called_once()
        kwargs = registrar.call_args.kwargs
        self.assertEqual(kwargs["projeto"], PROJETO_VPS.slug)
        self.assertEqual(kwargs["sha256"], sha256)
        self.assertEqual(kwargs["bytes_"], len(conteudo))
        self.assertEqual(kwargs["criado_em"], vps._criado_em_do_carimbo("20260819_235959"))

    def test_sha256_incompativel_e_reprovado_sem_promover(self) -> None:
        dump = vps.DumpRemoto(
            slug_servidor=PROJETO_VPS.slug_servidor, arquivo="x_banco_20260819_000000.dump",
            caminho_remoto="x/x_banco_20260819_000000.dump", bytes=4, sha256="0" * 64,
            carimbo="20260819_000000",
        )

        def _enviar_fake(alvo, dump_arg, destino):
            Path(destino).write_bytes(b"nope")

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            with (
                patch.object(vps, "enviar_remoto", side_effect=_enviar_fake),
                patch.object(vps, "verificar_dump_no_sandbox") as verificar,
                patch.object(banco, "registrar_artefato") as registrar,
                patch.object(banco, "registrar_evento") as evento,
            ):
                ok = vps._buscar_e_catalogar(PROJETO_VPS, ALVO, dump, execucao_id=42)
            caminho_final = configuracao.caminho_sob_raiz(
                "projects", PROJETO_VPS.slug, "banco", dump.arquivo
            )
            self.assertFalse(ok)
            self.assertFalse(os.path.exists(caminho_final))

        verificar.assert_not_called()
        registrar.assert_not_called()
        evento.assert_called_once()

    def test_reprovado_no_sandbox_nao_promove(self) -> None:
        conteudo = b"conteudo"
        sha256 = hashlib.sha256(conteudo).hexdigest()
        dump = vps.DumpRemoto(
            slug_servidor=PROJETO_VPS.slug_servidor, arquivo="y_banco_20260819_000000.dump",
            caminho_remoto="y/y_banco_20260819_000000.dump", bytes=len(conteudo), sha256=sha256,
            carimbo="20260819_000000",
        )

        def _enviar_fake(alvo, dump_arg, destino):
            Path(destino).write_bytes(conteudo)

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            with (
                patch.object(vps, "enviar_remoto", side_effect=_enviar_fake),
                patch.object(
                    vps, "verificar_dump_no_sandbox",
                    side_effect=vps.FalhaDeSincronizacao("reprovado"),
                ),
                patch.object(banco, "registrar_artefato") as registrar,
                patch.object(banco, "registrar_evento") as evento,
            ):
                ok = vps._buscar_e_catalogar(PROJETO_VPS, ALVO, dump, execucao_id=42)
            caminho_final = configuracao.caminho_sob_raiz(
                "projects", PROJETO_VPS.slug, "banco", dump.arquivo
            )
            self.assertFalse(ok)
            self.assertFalse(os.path.exists(caminho_final))

        registrar.assert_not_called()
        evento.assert_called_once()


class SincronizarProjetoTests(unittest.TestCase):
    def test_recusa_projeto_local_e_fecha_execucao(self) -> None:
        with (
            patch.object(banco, "abrir_execucao", return_value=999) as abrir,
            patch.object(banco, "fechar_execucao") as fechar,
            patch.object(banco, "registrar_evento") as registrar,
            patch.object(vps, "_alvo_configurado") as alvo_configurado,
        ):
            with self.assertRaises(vps.FalhaDeSincronizacao):
                vps.sincronizar_projeto(PROJETO_LOCAL)
        abrir.assert_called_once_with(PROJETO_LOCAL.slug, "sincronizacao")
        fechar.assert_called_once()
        registrar.assert_called_once()
        alvo_configurado.assert_not_called()

    def test_vps_nao_configurado_fecha_execucao_como_falha(self) -> None:
        with (
            patch.object(banco, "abrir_execucao", return_value=999),
            patch.object(banco, "fechar_execucao") as fechar,
            patch.object(banco, "registrar_evento"),
            patch.object(
                vps, "_alvo_configurado",
                side_effect=vps.FalhaDeSincronizacao("VPS não configurado"),
            ),
        ):
            with self.assertRaises(vps.FalhaDeSincronizacao):
                vps.sincronizar_projeto(PROJETO_VPS)
        fechar.assert_called_once_with(999, "falha", "VPS não configurado")

    def test_dump_ja_existente_pula_busca_mas_tenta_apagar(self) -> None:
        nome = f"{PROJETO_VPS.slug_servidor}_banco_20260819_000000.dump"
        linha = f"{PROJETO_VPS.slug_servidor}/{nome} 10 " + "a" * 64
        listar_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=(linha + "\n").encode(), stderr=b""
        )
        apagar_proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"",
            stderr="ERRO: recusado: é o dump mais recente de x\n".encode("utf-8"),
        )

        def _ssh_fake(alvo, comando, **kwargs):
            if comando == "listar":
                return listar_proc
            if comando.startswith("apagar "):
                return apagar_proc
            raise AssertionError(f"não deveria buscar — o dump já existe local: {comando!r}")

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            caminho = configuracao.caminho_sob_raiz("projects", PROJETO_VPS.slug, "banco", nome)
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            Path(caminho).write_bytes(b"ja-esta-aqui")

            with (
                patch.object(vps, "_ssh", side_effect=_ssh_fake),
                patch.object(vps, "_alvo_configurado", return_value=ALVO),
                patch.object(banco, "abrir_execucao", return_value=1),
                patch.object(banco, "marcar_fase"),
                patch.object(banco, "fechar_execucao") as fechar,
                patch.object(banco, "registrar_evento"),
            ):
                resultado = vps.sincronizar_projeto(PROJETO_VPS)

        self.assertEqual(resultado.ja_existentes, 1)
        self.assertEqual(resultado.buscados, 0)
        self.assertEqual(resultado.mantidos, 1)
        fechar.assert_called_once_with(1, "sucesso")

    def test_sem_hash_e_reprovado_sem_tentar_buscar_ou_apagar(self) -> None:
        nome = f"{PROJETO_VPS.slug_servidor}_banco_20260819_000000.dump"
        linha = f"{PROJETO_VPS.slug_servidor}/{nome} 10 sem-hash"
        listar_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=(linha + "\n").encode(), stderr=b""
        )

        def _ssh_fake(alvo, comando, **kwargs):
            if comando == "listar":
                return listar_proc
            raise AssertionError(f"não deveria buscar nem apagar um dump sem hash: {comando!r}")

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            with (
                patch.object(vps, "_ssh", side_effect=_ssh_fake),
                patch.object(vps, "_alvo_configurado", return_value=ALVO),
                patch.object(banco, "abrir_execucao", return_value=1),
                patch.object(banco, "marcar_fase"),
                patch.object(banco, "fechar_execucao") as fechar,
                patch.object(banco, "registrar_evento") as evento,
            ):
                resultado = vps.sincronizar_projeto(PROJETO_VPS)

        self.assertEqual(resultado.reprovados, 1)
        self.assertEqual(resultado.buscados, 0)
        self.assertEqual(resultado.mantidos, 0)
        fechar.assert_called_once_with(
            1,
            "falha",
            "Sincronização incompleta: 1 dump(s) remoto(s) reprovado(s).",
        )
        self.assertEqual(
            [chamada.args[0] for chamada in evento.call_args_list],
            ["sincronizacao.reprovado", "sincronizacao.falha"],
        )

    def test_mistura_sucesso_e_reprovacoes_preserva_contagens_e_fecha_uma_vez(self) -> None:
        dumps = [
            vps.DumpRemoto(
                slug_servidor=PROJETO_VPS.slug_servidor,
                arquivo=f"{PROJETO_VPS.slug_servidor}_banco_202608{dia:02d}_000000.dump",
                caminho_remoto=f"{PROJETO_VPS.slug_servidor}/dump-{dia}",
                bytes=10,
                sha256="a" * 64,
                carimbo=f"202608{dia:02d}_000000",
            )
            for dia in (18, 19, 20)
        ]

        with tempfile.TemporaryDirectory() as diretorio, _ambiente_raiz(diretorio):
            with (
                patch.object(vps, "_alvo_configurado", return_value=ALVO),
                patch.object(vps, "listar_remoto", return_value=dumps),
                patch.object(vps, "_buscar_e_catalogar", side_effect=[True, False, False]),
                patch.object(vps, "_apagar_remoto", return_value="mantido") as apagar,
                patch.object(banco, "abrir_execucao", return_value=7),
                patch.object(banco, "marcar_fase"),
                patch.object(banco, "fechar_execucao") as fechar,
                patch.object(banco, "registrar_evento") as evento,
            ):
                resultado = vps.sincronizar_projeto(PROJETO_VPS)

        self.assertEqual(resultado.buscados, 1)
        self.assertEqual(resultado.reprovados, 2)
        self.assertEqual(resultado.mantidos, 1)
        apagar.assert_called_once_with(ALVO, dumps[0])
        fechar.assert_called_once_with(
            7,
            "falha",
            "Sincronização incompleta: 2 dump(s) remoto(s) reprovado(s).",
        )
        eventos_falha = [
            chamada for chamada in evento.call_args_list
            if chamada.args[0] == "sincronizacao.falha"
        ]
        self.assertEqual(len(eventos_falha), 1)


class SincronizarVpsCliTests(unittest.TestCase):
    def test_todos_retorna_um_se_um_projeto_tiver_dump_reprovado(self) -> None:
        projetos = [p for p in PROJETOS if p.ambiente == "vps"][:2]
        resultados = [
            vps.ResultadoSincronizacao(buscados=1),
            vps.ResultadoSincronizacao(buscados=1, reprovados=2),
        ]

        with (
            patch.object(cli, "PROJETOS", projetos),
            patch.object(vps, "sincronizar_projeto", side_effect=resultados),
            patch("sys.stdout", new_callable=io.StringIO),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            codigo = cli.comando_sincronizar_vps(Namespace(todos=True, projeto=None))

        self.assertEqual(codigo, 1)
        self.assertIn("1 projeto(s) falharam", stderr.getvalue())
        self.assertIn("2 dump(s) remoto(s) reprovado(s)", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestArgumentoParaOShellRemoto(unittest.TestCase):
    """O `ssh` entrega a string ao shell do outro lado — o escape mora aqui.

    O ponto sensível não é escapar: é escapar **sem mudar o que vai na linha**
    para os nomes legítimos. O agente do VPS quebra `SSH_ORIGINAL_COMMAND` com
    `read -r -a`, que separa em palavras mas não remove aspas; se o escape
    aspasse um nome válido, o agente receberia `'x.dump'` com as aspas
    literais e recusaria todo dump.
    """

    def test_nome_legitimo_atravessa_sem_alteracao(self):
        for nome in (
            "mega_sena/mega_sena_banco_20260820_031500.dump",
            "conforto_termico/conforto_termico_banco_20260101_000000.dump",
            "controle_renda_variavel/controle_renda_variavel_banco_20261231_235959.dump",
        ):
            with self.subTest(nome=nome):
                self.assertEqual(vps._argumento(nome), nome)

    def test_todo_nome_aceito_pelo_padrao_atravessa_sem_alteracao(self):
        # Amarra o escape ao formato que `_PADRAO_LISTAGEM` aceita: enquanto os
        # dois combinarem, o escape é identidade e o agente nunca vê aspas.
        linha = "x_y/x_y_banco_20260820_031500.dump 123 " + "a" * 64
        m = vps._PADRAO_LISTAGEM.match(linha)
        self.assertIsNotNone(m)
        caminho = f"{m['slug']}/{m['arquivo']}"
        self.assertEqual(vps._argumento(caminho), caminho)

    def test_metacaractere_de_shell_vira_palavra_literal(self):
        # Nome que só existiria se o servidor tivesse sido comprometido: o
        # `;` não pode encerrar o comando do lado de lá.
        sujo = "mega_sena/x.dump; rm -rf /"
        escapado = vps._argumento(sujo)
        self.assertNotEqual(escapado, sujo)
        self.assertTrue(escapado.startswith("'"))
        self.assertTrue(escapado.endswith("'"))

    def test_aspa_simples_no_nome_nao_escapa_do_escape(self):
        escapado = vps._argumento("a'; rm -rf /; echo '")
        # Reabrir a string com uma aspa própria é o truque clássico; o
        # `shlex.quote` fecha, escapa a aspa e reabre.
        self.assertNotIn("'; rm", escapado.replace("'\"'\"'", ""))
