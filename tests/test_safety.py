from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import configuracao
import cli
import motor
import restaurar
from projetos import CONTAINERS_PROTEGIDOS, PROJETOS, por_slug


class ProjectCatalogTests(unittest.TestCase):
    def test_every_operational_container_is_protected(self) -> None:
        self.assertEqual(
            CONTAINERS_PROTEGIDOS,
            frozenset(project.container for project in PROJETOS),
        )

    def test_unknown_slug_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            por_slug("nao_existe")


class RestoreGuardTests(unittest.TestCase):
    def test_protected_container_is_rejected_before_catalog_access(self) -> None:
        protected = next(iter(CONTAINERS_PROTEGIDOS))
        with patch.object(restaurar.banco, "obter_artefato") as get_artifact:
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    1,
                    container_destino=protected,
                    banco_destino="destino",
                    usuario_destino="sandbox",
                    confirmacao="destino",
                )
        get_artifact.assert_not_called()

    def test_confirmation_is_rejected_before_catalog_access(self) -> None:
        with patch.object(restaurar.banco, "obter_artefato") as get_artifact:
            with self.assertRaises(restaurar.RestauracaoRecusada):
                restaurar.restaurar(
                    1,
                    container_destino="backuprestore-sandbox",
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
