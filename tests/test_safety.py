from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import ANY, patch

import configuracao
import cli
import motor
import projetos
import restaurar
import web
from projetos import CONTAINER_SANDBOX, CONTAINERS_PROTEGIDOS, PROJETOS, por_slug

PROJETO_LOCAL = next(p for p in PROJETOS if p.ambiente == "local")
PROJETO_VPS = next(p for p in PROJETOS if p.ambiente == "vps")


class ProjectCatalogTests(unittest.TestCase):
    def test_every_operational_container_is_protected(self) -> None:
        self.assertEqual(
            CONTAINERS_PROTEGIDOS,
            frozenset((project.ambiente, project.container) for project in PROJETOS),
        )

    def test_local_and_vps_projects_share_container_names_by_design(self) -> None:
        # A colisão é real, não um erro de digitação: local e VPS descrevem o
        # mesmo serviço em ambientes diferentes.
        self.assertEqual(PROJETO_LOCAL.container, PROJETO_VPS.container)
        self.assertNotEqual(PROJETO_LOCAL.slug, PROJETO_VPS.slug)

    def test_project_root_defaults_to_repository_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory, "Programacao", "VSCodeProjects", "BackupRestore")
            expected = repository.parent.resolve()
            self.assertEqual(
                projetos.resolver_raiz_projetos(pasta_aplicacao=repository),
                str(expected),
            )

    def test_project_root_explicit_override_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory, "checkouts").resolve()
            with patch.dict(
                os.environ,
                {projetos.VARIAVEL_RAIZ_PROJETOS: str(Path(directory, "x", "..", "checkouts"))},
                clear=True,
            ):
                self.assertEqual(projetos.resolver_raiz_projetos(), str(expected))

    def test_vps_project_has_no_local_folder_or_code_type(self) -> None:
        self.assertEqual(PROJETO_VPS.pasta, "")
        self.assertFalse(PROJETO_VPS.e_repo_git)
        self.assertNotIn("codigo", PROJETO_VPS.tipos)

    def test_unknown_slug_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            por_slug("nao_existe")


class RestoreGuardTests(unittest.TestCase):
    def test_sandbox_is_accepted_as_restore_destination(self) -> None:
        with patch.object(restaurar.banco, "obter_artefato", return_value=None) as get_artifact:
            with self.assertRaisesRegex(restaurar.RestauracaoRecusada, "não existe no catálogo"):
                restaurar.restaurar(
                    1,
                    container_destino=CONTAINER_SANDBOX,
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="destino",
                )
        get_artifact.assert_called_once_with(1)

    def test_real_container_is_rejected_without_docker_or_safety_dump(self) -> None:
        protected = PROJETO_LOCAL.container
        with (
            patch.object(restaurar.banco, "obter_artefato") as get_artifact,
            patch.object(restaurar.motor, "_rodar") as run_docker,
            patch.object(restaurar, "_dump_de_seguranca") as safety_dump,
        ):
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    1,
                    container_destino=protected,
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="destino",
                )
        get_artifact.assert_not_called()
        run_docker.assert_not_called()
        safety_dump.assert_not_called()

    def test_arbitrary_unprotected_name_is_rejected_without_docker_or_safety_dump(self) -> None:
        with (
            patch.object(restaurar.banco, "obter_artefato") as get_artifact,
            patch.object(restaurar.motor, "_rodar") as run_docker,
            patch.object(restaurar, "_dump_de_seguranca") as safety_dump,
        ):
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    1,
                    container_destino="postgres-descartavel-qualquer",
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="destino",
                )
        get_artifact.assert_not_called()
        run_docker.assert_not_called()
        safety_dump.assert_not_called()

    def test_confirmation_is_rejected_before_catalog_access(self) -> None:
        with patch.object(restaurar.banco, "obter_artefato") as get_artifact:
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    1,
                    container_destino=CONTAINER_SANDBOX,
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="outro",
                )
        get_artifact.assert_not_called()

    def test_database_name_is_escaped_as_literal_and_identifier(self) -> None:
        value = 'banco\'"perigoso'
        self.assertEqual(restaurar._literal_sql(value), "'banco''\"perigoso'")
        self.assertEqual(restaurar._identificador_sql(value), '"banco\'""perigoso"')

    def test_database_lookup_uses_escaped_literal(self) -> None:
        with patch.object(restaurar, "_psql", return_value="1") as psql:
            self.assertTrue(
                restaurar.banco_existe("sandbox", "sandbox", "x'; DROP DATABASE y; --")
            )
        sql = psql.call_args.args[3]
        self.assertIn("x''; DROP DATABASE y; --", sql)
        self.assertNotIn("datname = 'x';", sql)

    def test_live_origin_drift_does_not_invalidate_restored_snapshot(self) -> None:
        project = PROJETOS[0]
        with (
            patch.object(motor, "estado_container", return_value=(True, True)),
            patch.object(
                restaurar,
                "resumo_banco",
                side_effect=[{"eventos": 110}, {"eventos": 100}],
            ),
        ):
            result = restaurar.comparar_com_origem(
                project, "backuprestore-sandbox", "sandbox", "ensaio"
            )
        self.assertFalse(result["confere"])
        self.assertTrue(result["restauracao_valida"])
        self.assertEqual(result["divergencias"]["eventos"], (110, 100))

    def test_non_local_backup_is_refused_before_touching_docker(self) -> None:
        # Não usa o catálogo real: abrir/fechar execução são mockados, do
        # mesmo jeito que as outras travas pré-`try` desta função fecham a
        # execução antes de propagar o erro (sem isso, ela ficaria presa em
        # "fila" — foi exatamente o bug encontrado ao testar isto na tela).
        with (
            patch.object(motor.banco, "abrir_execucao", return_value=999) as abrir,
            patch.object(motor.banco, "fechar_execucao") as fechar,
            patch.object(motor.banco, "registrar_evento") as registrar,
            patch.object(motor, "estado_container") as estado_container,
        ):
            with self.assertRaises(motor.FalhaDeBackup):
                motor.fazer_backup(PROJETO_VPS)
        abrir.assert_called_once_with(PROJETO_VPS.slug, "backup")
        fechar.assert_called_once_with(999, "falha", ANY)
        registrar.assert_called_once()
        estado_container.assert_not_called()

    def test_non_local_origin_comparison_is_refused(self) -> None:
        with patch.object(motor, "estado_container") as estado_container:
            with self.assertRaises(RuntimeError):
                restaurar.comparar_com_origem(
                    PROJETO_VPS, "backuprestore-sandbox", "sandbox", "ensaio"
                )
        estado_container.assert_not_called()

    def test_catalog_traversal_is_refused_before_restore(self) -> None:
        artefato = {"tipo": "banco", "caminho_relativo": "../fora.dump", "projeto": "mega_sena"}
        with (
            patch.object(restaurar.banco, "obter_artefato", return_value=artefato),
            patch.object(motor, "caminho_artefato", side_effect=motor.FalhaDeBackup("inseguro")),
            patch.object(restaurar.banco, "marcar_situacao_artefato") as marcar,
        ):
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    7,
                    container_destino="backuprestore-sandbox",
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="destino",
                )
        marcar.assert_called_once_with(7, "corrompido")


class BackupRootGuardTests(unittest.TestCase):
    def test_default_root_is_derived_from_repository_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory, "Programacao", "VSCodeProjects", "BackupRestore")
            config = Path(directory, "ausente.json")
            expected = Path(directory, "Programacao", "Backups", "BackupRestore").resolve()
            with (
                patch.object(configuracao, "PASTA_APLICACAO", str(repository)),
                patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(config)),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(configuracao.raiz_backup(), str(expected))
                self.assertEqual(configuracao.raiz_permitida(), str(expected))

    def test_persisted_root_precedes_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowed = Path(directory).resolve()
            persisted = Path(directory, "persisted")
            environment = Path(directory, "environment")
            config = Path(directory, "config.json")
            config.write_text(
                json.dumps({"raiz_backup": str(persisted), "raiz_permitida": str(allowed)})
            )
            with (
                patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(config)),
                patch.dict(
                    os.environ,
                    {configuracao.VARIAVEL_RAIZ_BACKUP: str(environment)},
                    clear=True,
                ),
            ):
                self.assertEqual(configuracao.raiz_backup(), str(persisted.resolve()))

    def test_environment_root_is_its_own_limit_without_explicit_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory, "environment")
            config = Path(directory, "ausente.json")
            with (
                patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(config)),
                patch.dict(
                    os.environ,
                    {configuracao.VARIAVEL_RAIZ_BACKUP: str(environment)},
                    clear=True,
                ),
            ):
                self.assertEqual(configuracao.raiz_backup(), str(environment.resolve()))
                self.assertEqual(configuracao.raiz_permitida(), str(environment.resolve()))

    def test_sandbox_compose_has_no_host_backup_bind(self) -> None:
        compose = Path(__file__).resolve().parents[1] / "compose.teste.yaml"
        content = compose.read_text(encoding="utf-8")
        self.assertNotIn("/backups", content)
        self.assertNotIn("C:/Users/MSPA", content)

    def test_external_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            permitida = Path(directory, "permitida")
            externa = Path(directory, "externa")
            with patch.dict(
                os.environ,
                {configuracao.VARIAVEL_RAIZ_PERMITIDA: str(permitida)},
                clear=False,
            ):
                with self.assertRaises(configuracao.ConfiguracaoInvalida):
                    configuracao.validar_raiz(str(externa))

    def test_catalog_traversal_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raiz = Path(directory, "backups")
            raiz.mkdir()
            with (
                patch.dict(
                    os.environ,
                    {
                        configuracao.VARIAVEL_RAIZ_PERMITIDA: str(raiz),
                        "BACKUPRESTORE_RAIZ_BACKUP": str(raiz),
                    },
                    clear=False,
                ),
                self.assertRaises(motor.FalhaDeBackup),
            ):
                motor.caminho_artefato("../fora.dump")

    def test_symlink_leaving_permitted_root_is_refused_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            permitida = Path(directory, "permitida")
            externa = Path(directory, "externa")
            permitida.mkdir()
            externa.mkdir()
            link = permitida / "link-externo"
            try:
                os.symlink(externa, link, target_is_directory=True)
            except (NotImplementedError, OSError) as erro:
                self.skipTest(f"links simbólicos indisponíveis neste ambiente: {erro}")
            with patch.dict(
                os.environ,
                {configuracao.VARIAVEL_RAIZ_PERMITIDA: str(permitida)},
                clear=False,
            ):
                with self.assertRaises(configuracao.ConfiguracaoInvalida):
                    configuracao.validar_raiz(str(link))

    def test_web_does_not_expose_root_change_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raiz = Path(directory, "backups")
            with (
                patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(directory, "config.json"))),
                patch.dict(
                    os.environ,
                    {
                        configuracao.VARIAVEL_RAIZ_PERMITIDA: str(raiz),
                        "BACKUPRESTORE_RAIZ_BACKUP": str(raiz),
                    },
                    clear=False,
                ),
            ):
                sys.modules.pop("web", None)
                web = importlib.import_module("web")
                regras = [
                    regra
                    for regra in web.app.url_map.iter_rules()
                    if regra.rule == "/configuracoes"
                ]
                self.assertEqual(len(regras), 1)
                self.assertNotIn("POST", regras[0].methods)
            sys.modules.pop("web", None)

    def test_web_does_not_expose_vps_change_post(self) -> None:
        # A interface nem sequer importa a função de escrita, então não há
        # como uma rota vir a chamá-la por engano.
        self.assertFalse(hasattr(web, "configurar_vps"))
        regras = [r for r in web.app.url_map.iter_rules() if r.rule == "/configuracoes"]
        self.assertEqual(len(regras), 1)
        self.assertNotIn("POST", regras[0].methods)

    def test_permitida_can_change_without_changing_catalog_root(self) -> None:
        raiz = os.path.abspath("C:/backups/BackupRestore")
        argumentos = type("Args", (), {"caminho": raiz, "permitida": "C:/backups"})()
        with (
            patch.object(cli.banco, "listar_artefatos", return_value=[object()]),
            patch.object(cli, "raiz_backup", return_value=raiz),
            patch.object(cli, "configurar_raiz_backup", return_value=raiz) as configurar,
        ):
            self.assertEqual(cli.comando_configurar_raiz(argumentos), 0)
        configurar.assert_called_once_with(raiz, permitida="C:/backups")


class VpsTargetConfigTests(unittest.TestCase):
    def test_unconfigured_target_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(directory, "config.json"))):
                self.assertIsNone(configuracao.alvo_vps())

    def test_missing_key_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(directory, "config.json"))):
                with self.assertRaises(configuracao.ConfiguracaoInvalida):
                    configuracao.configurar_vps(
                        "163.176.214.214", "ubuntu", str(Path(directory, "nao-existe.key"))
                    )

    def test_valid_target_is_persisted_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chave = Path(directory, "chave.key")
            chave.write_text("fake")
            with patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(directory, "config.json"))):
                configuracao.configurar_vps("163.176.214.214", "ubuntu", str(chave))
                alvo = configuracao.alvo_vps()
        self.assertEqual(
            alvo, {"host": "163.176.214.214", "usuario": "ubuntu", "chave": str(chave)}
        )

    def test_configuring_vps_preserves_existing_backup_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chave = Path(directory, "chave.key")
            chave.write_text("fake")
            arquivo = Path(directory, "config.json")
            arquivo.write_text(json.dumps({"raiz_backup": "D:/Backups/BackupRestore"}))
            with patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(arquivo)):
                configuracao.configurar_vps("163.176.214.214", "ubuntu", str(chave))
                dados = json.loads(arquivo.read_text())
        self.assertEqual(dados["raiz_backup"], "D:/Backups/BackupRestore")
        self.assertEqual(dados["vps"]["host"], "163.176.214.214")


class EnsaioVpsTests(unittest.TestCase):
    def test_ensaio_de_projeto_vps_pula_comparacao_com_origem(self) -> None:
        # `comparar_com_origem` recusaria porque o contêiner do projeto VPS
        # colide de nome com o local. `cli.comando_ensaio` não
        # deve nem chamá-la para um projeto de ambiente != local — confere só
        # o que a restauração produziu no sandbox.
        artefato = {"id": 1, "caminho_relativo": "projects/x/banco/x.dump", "projeto": PROJETO_VPS.slug}
        argumentos = type("Args", (), {"projeto": PROJETO_VPS.slug})()

        with (
            patch.object(cli.banco, "artefatos_validos", return_value=[artefato]),
            patch.object(cli.motor, "estado_container", return_value=(True, True)),
            patch.object(cli.restauracao, "restaurar", return_value={}) as restaurar_mock,
            patch.object(
                cli.restauracao, "resumo_banco", return_value={"tabela": 5}
            ) as resumo_mock,
            patch.object(cli.restauracao, "comparar_com_origem") as comparar_mock,
        ):
            codigo = cli.comando_ensaio(argumentos)

        self.assertEqual(codigo, 0)
        restaurar_mock.assert_called_once()
        resumo_mock.assert_called_once()
        comparar_mock.assert_not_called()


class ArtifactValidationTests(unittest.TestCase):
    def test_zip_requires_internal_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sem-manifesto.zip")
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("arquivo.txt", "conteudo")
            with self.assertRaises(motor.FalhaDeBackup):
                motor.verificar_zip_codigo(path)

    def test_zip_with_valid_manifest_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "valido.zip")
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("arquivo.txt", "conteudo")
                package.writestr(
                    "backuprestore-manifest.json",
                    json.dumps({"versao": 1, "arquivos": []}),
                )
            motor.verificar_zip_codigo(path)


if __name__ == "__main__":
    unittest.main()
