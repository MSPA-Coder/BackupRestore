from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

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
