"""Backup pela API pública, com disco e catálogo reais; Docker é a fronteira externa."""

import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import banco
import configuracao
import motor
import reconstruir
import restaurar
import cli
import web
from projetos import CONTAINER_SANDBOX, PROJETOS

LOCAL = next(p for p in PROJETOS if p.ambiente == "local")


class FluxoBackupTests(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory()
        self.addCleanup(temporario.cleanup)
        self.raiz = Path(temporario.name) / "backups"
        for controle in (
            patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(temporario.name) / "config.json")),
            patch.object(banco, "CAMINHO_CATALOGO", str(Path(temporario.name) / "catalogo.sqlite3")),
            patch.dict(os.environ, {
                "BACKUPRESTORE_RAIZ_BACKUP": str(self.raiz),
                "BACKUPRESTORE_RAIZ_PERMITIDA": str(self.raiz),
            }),
        ):
            controle.start()
            self.addCleanup(controle.stop)
        banco.criar_tabelas()
        self.comandos = []
        self.rodando = True
        self.falha_dump = False
        self.falha_prontidao = False
        self.falha_releitura = False
        self.falha_restore = False
        self.destino_existe = True
        controle = patch("subprocess.run", side_effect=self.docker)
        controle.start()
        self.addCleanup(controle.stop)

    def docker(self, comando, **opcoes):
        self.comandos.append(comando)
        codigo, saida, erro = 0, b"", b""
        if comando[1] == "inspect":
            saida = b"true" if self.rodando else b"false"
        elif comando[1] in ("start", "stop"):
            self.rodando = comando[1] == "start"
        elif "pg_isready" in comando:
            if self.falha_prontidao:
                raise subprocess.TimeoutExpired(comando, 30)
        elif "pg_dump" in comando:
            opcoes["stdout"].write(b"PGDMP-conteudo-sintetico")
            if self.falha_dump:
                codigo, erro = 1, b"dump interrompido"
        elif "pg_restore" in comando:
            self.assertEqual(opcoes["stdin"].read(), b"PGDMP-conteudo-sintetico")
            if ("--list" in comando and self.falha_releitura) or ("--list" not in comando and self.falha_restore):
                codigo, erro = 1, b"artefato recusado"
        elif "psql" in comando:
            sql = comando[-1]
            if "FROM pg_database" in sql:
                saida = b"1" if self.destino_existe else b""
            elif "pg_stat_user_tables" in sql:
                saida = b"medicoes=3\nvazia=0\n"
        else:
            raise AssertionError(f"Comando externo inesperado: {comando}")
        return subprocess.CompletedProcess(comando, codigo, saida, erro)

    def test_backup_verificado_publica_hash_manifesto_e_catalogo_coerentes(self):
        resultados = motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertEqual(len(resultados), 1)
        caminho = Path(resultados[0].caminho)
        digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
        manifesto = json.loads(Path(str(caminho) + ".manifest.json").read_text())
        artefato = banco.listar_artefatos(LOCAL.slug)[0]
        self.assertEqual(artefato["sha256"], digest)
        self.assertEqual(manifesto["sha256"], digest)
        self.assertEqual(artefato["bytes"], caminho.stat().st_size)
        self.assertEqual(artefato["situacao"], "valido")
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "sucesso")
        self.assertFalse(any(c[1] in ("start", "stop") for c in self.comandos))
        self.assertEqual(list((self.raiz / "temp").iterdir()), [])

    def test_dump_interrompido_nao_publica_e_devolve_container_parado(self):
        self.rodando = False
        self.falha_dump = True
        with self.assertRaises(motor.FalhaDeBackup):
            motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertFalse(self.rodando)
        self.assertEqual(banco.listar_artefatos(LOCAL.slug), [])
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")
        self.assertEqual(list(self.raiz.rglob("*.tmp")), [])
        self.assertEqual(list(self.raiz.rglob("*.dump")), [])

    def test_falha_ao_restaurar_estado_do_container_nao_informa_sucesso(self):
        self.rodando = False
        docker_original = self.docker

        def docker_com_falha(comando, **opcoes):
            if comando[1] == "stop":
                return subprocess.CompletedProcess(comando, 1, b"", b"falha ao parar")
            return docker_original(comando, **opcoes)

        with patch("subprocess.run", side_effect=docker_com_falha):
            with self.assertRaises(motor.FalhaDeBackup):
                motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")
        self.assertFalse(any(e["tipo"] == "backup.sucesso" for e in banco.listar_eventos()))

    def test_comparacao_recusa_sucesso_se_nao_consegue_parar_a_origem(self):
        self.rodando = False
        docker_original = self.docker

        def docker_com_falha(comando, **opcoes):
            if comando[1] == "stop":
                return subprocess.CompletedProcess(comando, 1, b"", b"falha ao parar")
            return docker_original(comando, **opcoes)

        with patch("subprocess.run", side_effect=docker_com_falha):
            with self.assertRaisesRegex(RuntimeError, "estado original"):
                restaurar.comparar_com_origem(LOCAL, CONTAINER_SANDBOX, "sandbox", "sandbox")

    def test_interrupcao_apos_publicacao_permite_recuperar_catalogo(self):
        substituir = os.replace

        def interromper(origem, destino):
            substituir(origem, destino)
            if str(destino).endswith(".dump"):
                raise KeyboardInterrupt("processo interrompido após publicação")

        with patch("os.replace", side_effect=interromper):
            with self.assertRaises(KeyboardInterrupt):
                motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertEqual(banco.listar_artefatos(LOCAL.slug), [])
        with patch.object(motor, "_sandbox_disponivel", return_value=True):
            relatorio = reconstruir.reconstruir(aplicar=True, projeto_slug=LOCAL.slug)
        self.assertTrue(relatorio.limpo)
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 1)

    def test_interrupcao_antes_do_artefato_nao_cataloga_manifesto_isolado(self):
        anterior = motor.fazer_backup(replace(LOCAL, retencao=1), tipos=("banco",))[0]
        substituir = os.replace

        def interromper(origem, destino):
            substituir(origem, destino)
            if str(destino).endswith(".manifest.json"):
                raise KeyboardInterrupt("processo interrompido antes da publicação")

        with patch("os.replace", side_effect=interromper):
            with self.assertRaises(KeyboardInterrupt):
                motor.fazer_backup(replace(LOCAL, retencao=1), tipos=("banco",))
        self.assertTrue(Path(anterior.caminho).is_file())
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 1)
        with patch.object(motor, "_sandbox_disponivel", return_value=True):
            relatorio = reconstruir.reconstruir(aplicar=True, projeto_slug=LOCAL.slug)
        self.assertTrue(relatorio.limpo)
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 1)

    def test_backups_no_mesmo_segundo_preservam_ambos_os_artefatos(self):
        with patch.object(motor, "_carimbo", return_value="20261028_120000"):
            primeiro = motor.fazer_backup(LOCAL, tipos=("banco",))[0]
            segundo = motor.fazer_backup(LOCAL, tipos=("banco",))[0]

        self.assertNotEqual(primeiro.caminho, segundo.caminho)
        artefatos = banco.artefatos_validos(LOCAL.slug, "banco")
        self.assertEqual(len(artefatos), 2)
        for artefato in artefatos:
            caminho = self.raiz / artefato["caminho_relativo"]
            self.assertEqual(hashlib.sha256(caminho.read_bytes()).hexdigest(), artefato["sha256"])
            self.assertTrue(Path(str(caminho) + ".manifest.json").is_file())

    def test_dois_backups_do_mesmo_container_nao_interrompem_um_ao_outro(self):
        self.rodando = False
        primeiro_dump = threading.Event()
        liberar_primeiro = threading.Event()
        segundo_dump = threading.Event()
        resultados = []

        def docker(comando, **opcoes):
            if "pg_dump" in comando:
                if threading.current_thread().name == "primeiro":
                    primeiro_dump.set()
                    if not liberar_primeiro.wait(10):
                        raise AssertionError("primeiro backup não foi liberado")
                else:
                    segundo_dump.set()
                if not self.rodando:
                    return subprocess.CompletedProcess(comando, 1, b"", b"container parado")
            return self.docker(comando, **opcoes)

        def executar():
            try:
                motor.fazer_backup(LOCAL, tipos=("banco",))
                resultados.append("sucesso")
            except Exception as erro:
                resultados.append(str(erro))

        with patch("subprocess.run", side_effect=docker):
            primeiro = threading.Thread(target=executar, name="primeiro")
            segundo = threading.Thread(target=executar, name="segundo")
            primeiro.start()
            self.assertTrue(primeiro_dump.wait(10))
            segundo.start()
            try:
                self.assertFalse(segundo_dump.wait(0.2), "o segundo não deve iniciar o dump antes do primeiro terminar")
            finally:
                liberar_primeiro.set()
                primeiro.join(10)
                segundo.join(10)
        self.assertFalse(primeiro.is_alive() or segundo.is_alive())
        self.assertEqual(resultados, ["sucesso", "sucesso"])
        self.assertFalse(self.rodando)
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 2)

    def test_retencao_espera_restauracao_terminar_de_ler_o_dump(self):
        projeto = replace(LOCAL, retencao=1)
        primeiro = motor.fazer_backup(projeto, tipos=("banco",))[0]
        artefato = banco.listar_artefatos(projeto.slug)[0]
        restaurando = threading.Event()
        liberar_restore = threading.Event()
        segundo_dump = threading.Event()
        resultados = []

        def docker(comando, **opcoes):
            if "pg_restore" in comando and "--exit-on-error" in comando:
                restaurando.set()
                if not liberar_restore.wait(10):
                    raise AssertionError("restauração não foi liberada")
            if "pg_dump" in comando and threading.current_thread().name == "backup-segundo":
                segundo_dump.set()
            return self.docker(comando, **opcoes)

        def restaurar_dump():
            try:
                restaurar.restaurar(
                    artefato["id"], container_destino=CONTAINER_SANDBOX,
                    banco_destino="ensaio", usuario_destino="sandbox", confirmacao="ensaio",
                )
                resultados.append("restaurado")
            except Exception as erro:
                resultados.append(str(erro))

        def fazer_segundo_backup():
            try:
                motor.fazer_backup(projeto, tipos=("banco",))
                resultados.append("backup")
            except Exception as erro:
                resultados.append(str(erro))

        with patch("subprocess.run", side_effect=docker):
            leitura = threading.Thread(target=restaurar_dump)
            backup = threading.Thread(target=fazer_segundo_backup, name="backup-segundo")
            leitura.start()
            self.assertTrue(restaurando.wait(10))
            backup.start()
            try:
                self.assertFalse(segundo_dump.wait(0.2))
                self.assertTrue(Path(primeiro.caminho).exists())
            finally:
                liberar_restore.set()
                leitura.join(10)
                backup.join(10)
        self.assertFalse(leitura.is_alive() or backup.is_alive())
        self.assertCountEqual(resultados, ["restaurado", "backup"])
        self.assertFalse(Path(primeiro.caminho).exists())
        self.assertEqual(len(banco.artefatos_validos(projeto.slug, "banco")), 1)

    def test_troca_de_raiz_aguarda_backup_e_preserva_vinculo_do_catalogo(self):
        iniciando_dump = threading.Event()
        liberar_dump = threading.Event()
        configuracao_concluida = threading.Event()
        resultados = []

        def docker(comando, **opcoes):
            if "pg_dump" in comando:
                iniciando_dump.set()
                if not liberar_dump.wait(10):
                    raise AssertionError("backup não foi liberado")
            return self.docker(comando, **opcoes)

        def backup():
            try:
                motor.fazer_backup(LOCAL, tipos=("banco",))
                resultados.append("backup")
            except Exception as erro:
                resultados.append(str(erro))

        def trocar_raiz():
            try:
                nova = str(self.raiz.parent / "outra-raiz")
                resultados.append(cli.comando_configurar_raiz(SimpleNamespace(caminho=nova, permitida=nova)))
            finally:
                configuracao_concluida.set()

        with patch("subprocess.run", side_effect=docker):
            primeiro = threading.Thread(target=backup)
            segundo = threading.Thread(target=trocar_raiz)
            primeiro.start()
            self.assertTrue(iniciando_dump.wait(10))
            segundo.start()
            try:
                self.assertFalse(configuracao_concluida.wait(0.2))
            finally:
                liberar_dump.set()
                primeiro.join(10)
                segundo.join(10)
        self.assertFalse(primeiro.is_alive() or segundo.is_alive())
        self.assertCountEqual(resultados, ["backup", 2])
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 1)

    def test_timeout_de_prontidao_tambem_devolve_container_parado(self):
        self.rodando = False
        self.falha_prontidao = True
        with self.assertRaises(subprocess.TimeoutExpired):
            motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertFalse(self.rodando)
        self.assertFalse(any("pg_dump" in c for c in self.comandos))
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")

    def test_restauracao_adulterada_e_recusada_antes_de_falar_com_docker(self):
        resultado = motor.fazer_backup(LOCAL, tipos=("banco",))[0]
        artefato = banco.listar_artefatos(LOCAL.slug)[0]
        Path(resultado.caminho).write_bytes(b"adulterado-depois-do-backup")
        self.comandos.clear()
        with self.assertRaises(restaurar.RestauracaoRecusada):
            restaurar.restaurar(
                artefato["id"], container_destino=CONTAINER_SANDBOX,
                banco_destino="ensaio", usuario_destino="sandbox", confirmacao="ensaio",
            )
        self.assertEqual(self.comandos, [])
        self.assertEqual(banco.obter_artefato(artefato["id"])["situacao"], "corrompido")

    def test_falha_na_nova_copia_preserva_artefato_anterior(self):
        resultado = motor.fazer_backup(LOCAL, tipos=("banco",))[0]
        anterior = Path(resultado.caminho).read_bytes()
        self.falha_dump = True
        with self.assertRaises(motor.FalhaDeBackup):
            motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertEqual(Path(resultado.caminho).read_bytes(), anterior)
        self.assertEqual(len(banco.artefatos_validos(LOCAL.slug, "banco")), 1)

    def preparar_restore(self):
        motor.fazer_backup(LOCAL, tipos=("banco",))
        self.comandos.clear()
        return banco.listar_artefatos(LOCAL.slug)[0]["id"]

    def restaurar(self, artefato_id):
        return restaurar.restaurar(
            artefato_id, container_destino=CONTAINER_SANDBOX,
            banco_destino='qa "destino"', usuario_destino="sandbox", confirmacao='qa "destino"',
        )

    def test_restore_preserva_destino_antes_de_drop_e_registra_seguranca(self):
        resultado = self.restaurar(self.preparar_restore())
        comandos = self.comandos
        releitura = next(i for i, c in enumerate(comandos) if "--list" in c)
        drop = next(i for i, c in enumerate(comandos) if "psql" in c and c[-1].startswith("DROP DATABASE"))
        aplicar = next(i for i, c in enumerate(comandos) if "--exit-on-error" in c)
        self.assertLess(releitura, drop)
        self.assertLess(drop, aplicar)
        self.assertIn('"qa ""destino"""', comandos[drop][-1])
        self.assertEqual(resultado["tabelas"], {"medicoes": 3, "vazia": 0})
        seguranca = next(a for a in banco.listar_artefatos(LOCAL.slug) if a["finalidade"] == "pre_restauracao")
        self.assertEqual(seguranca["sha256"], motor.sha256_arquivo(resultado["dump_de_seguranca"]))
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "sucesso")

    def test_restore_sem_destino_previo_nao_inventa_dump_de_seguranca(self):
        artefato_id = self.preparar_restore()
        self.destino_existe = False
        resultado = self.restaurar(artefato_id)
        self.assertIsNone(resultado["dump_de_seguranca"])
        self.assertFalse(any("pg_dump" in c for c in self.comandos))
        self.assertEqual(resultado["tabelas"]["medicoes"], 3)

    def test_dump_seguranca_invalido_impede_qualquer_drop(self):
        artefato_id = self.preparar_restore()
        self.falha_releitura = True
        with self.assertRaises(restaurar.RestauracaoRecusada):
            self.restaurar(artefato_id)
        self.assertFalse(any("psql" in c and "DROP DATABASE" in c[-1] for c in self.comandos))
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")
        self.assertEqual(list(self.raiz.rglob("*.tmp")), [])

    def test_restore_interrompido_conserva_seguranca_e_audita_falha(self):
        artefato_id = self.preparar_restore()
        self.falha_restore = True
        with self.assertRaises(RuntimeError):
            self.restaurar(artefato_id)
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")
        seguranca = next(a for a in banco.listar_artefatos(LOCAL.slug) if a["finalidade"] == "pre_restauracao")
        self.assertTrue(Path(motor.caminho_artefato(seguranca["caminho_relativo"])).is_file())
        self.assertTrue(any(e["tipo"] == "restauracao.falha" for e in banco.listar_eventos()))

    def test_releitura_reprovada_nao_publica_artefato_com_exit_zero_no_dump(self):
        self.falha_releitura = True
        with self.assertRaises(motor.FalhaDeBackup):
            motor.fazer_backup(LOCAL, tipos=("banco",))
        self.assertEqual(banco.listar_artefatos(), [])
        self.assertEqual(list(self.raiz.rglob("*.dump")), [])

    def test_cli_backup_lista_verifica_e_recusa_restore_sem_confirmacao(self):
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(cli.main(["backup", "--projeto", LOCAL.slug, "--tipos", "banco"]), 0)
            self.assertEqual(cli.main(["listar", "--projeto", LOCAL.slug]), 0)
            self.assertEqual(cli.main(["verificar", "--projeto", LOCAL.slug]), 0)
            artefato = banco.listar_artefatos()[0]
            self.comandos.clear()
            self.assertEqual(cli.main(["restaurar", "--artefato", str(artefato["id"]),
                                       "--destino-container", CONTAINER_SANDBOX, "--destino-banco", "qa"]), 2)
        self.assertEqual(self.comandos, [])

    def test_cli_backup_falha_tem_codigo_nao_zero_e_catalogo_diagnostico(self):
        self.falha_dump = True
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(cli.main(["backup", "--projeto", LOCAL.slug, "--tipos", "banco"]), 1)
        self.assertEqual(banco.listar_execucoes()[0]["situacao"], "falha")

    def test_http_fixa_desfixa_e_expoe_progresso_persistido(self):
        artefato_id = self.preparar_restore()
        cliente = web.app.test_client()
        for esperado in (True, False):
            self.assertEqual(cliente.post(f"/artefato/{artefato_id}/fixar").status_code, 302)
            self.assertEqual(bool(banco.obter_artefato(artefato_id)["fixado"]), esperado)
        execucao = banco.listar_execucoes()[0]
        resposta = cliente.get(f"/api/execucao/{execucao['id']}")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json["situacao"], "sucesso")
        for caminho in ("/api/execucao/99999", "/projeto/ausente", "/backups?tipo=ausente"):
            self.assertEqual(cliente.get(caminho).status_code, 404)

    def test_http_restauracao_destino_fixo_ignora_alvo_recebido(self):
        artefato_id = self.preparar_restore()
        cliente = web.app.test_client()
        resposta = cliente.post(f"/restaurar/{artefato_id}", data={
            "confirmacao": f"ensaio_{LOCAL.slug}", "container_destino": "producao",
            "banco_destino": "producao",
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(any("--exit-on-error" in c for c in self.comandos))
        self.assertTrue(all(CONTAINER_SANDBOX in c for c in self.comandos if c[1] == "exec"))
        self.assertFalse(any("producao" in c for c in self.comandos))

    def test_http_confirmacao_incorreta_nao_dispara_operacao(self):
        artefato_id = self.preparar_restore()
        resposta = web.app.test_client().post(f"/restaurar/{artefato_id}", data={"confirmacao": "errada"})
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.comandos, [])
