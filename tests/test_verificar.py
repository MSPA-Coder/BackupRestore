"""`verificar` só pode gravar veredito que ele realmente alcançou.

O caso que motivou estes testes: com a stack do projeto parada, a releitura
falhava e 90 artefatos íntegros viravam `corrompido` no catálogo. Como
`aplicar_retencao` conta artefatos válidos, o rótulo falso ainda tirava esses
dumps da retenção — a regra 3 passava a decidir sobre um acervo que não
correspondia ao disco.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import banco
import motor
from projetos import CONTAINER_SANDBOX

SHA_QUALQUER = "a" * 64


def _linha(id_: int, tipo: str, projeto: str = "mega_sena_vps") -> dict:
    return {
        "id": id_,
        "projeto": projeto,
        "tipo": tipo,
        "situacao": "valido",
        "caminho_relativo": f"projects/{projeto}/{tipo}/artefato-{id_}",
        "sha256": SHA_QUALQUER,
        "bytes": 10,
    }


def _ok() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")


class VerificarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.diretorio = tempfile.TemporaryDirectory()
        self.addCleanup(self.diretorio.cleanup)
        self.arquivo = os.path.join(self.diretorio.name, "artefato.dump")
        with open(self.arquivo, "wb") as destino:
            destino.write(b"conteudo qualquer")

    def _executar(self, linhas: list[dict], *, sandbox: tuple[bool, bool], reler):
        """Roda `verificar` com o disco e o Docker sob controle.

        Devolve a contagem e as situações efetivamente gravadas, porque o que
        importa aqui é tanto o número quanto o que foi (ou não foi) escrito.
        """
        gravadas: list[tuple[int, str]] = []
        with (
            patch.object(banco, "listar_artefatos", return_value=linhas),
            patch.object(
                banco, "marcar_situacao_artefato",
                side_effect=lambda id_, situacao: gravadas.append((id_, situacao)),
            ),
            patch.object(motor, "caminho_artefato", return_value=self.arquivo),
            patch.object(motor, "sha256_arquivo", return_value=SHA_QUALQUER),
            patch.object(motor, "estado_container", return_value=sandbox),
            patch.object(motor, "_rodar", side_effect=reler),
        ):
            return motor.verificar(), gravadas

    def test_sem_sandbox_nenhum_dump_e_julgado(self) -> None:
        def _reler(comando, **kwargs):
            raise AssertionError(f"não deveria chamar o Docker: {comando!r}")

        contagem, gravadas = self._executar(
            [_linha(1, "banco"), _linha(2, "banco")],
            sandbox=(False, False),
            reler=_reler,
        )

        self.assertEqual(contagem["nao_verificados"], 2)
        self.assertEqual(contagem["corrompidos"], 0)
        self.assertEqual(contagem["conferidos"], 0)
        self.assertEqual(gravadas, [], "a situação tem de ficar como estava")

    def test_dump_integro_e_relido_no_sandbox_e_nao_no_container_do_projeto(self) -> None:
        comandos: list[list[str]] = []

        def _reler(comando, **kwargs):
            comandos.append(comando)
            return _ok()

        contagem, gravadas = self._executar(
            [_linha(1, "banco")], sandbox=(True, True), reler=_reler
        )

        self.assertEqual(contagem["conferidos"], 1)
        self.assertEqual(contagem["nao_verificados"], 0)
        self.assertEqual(gravadas, [(1, "valido")])
        self.assertEqual(len(comandos), 1)
        self.assertIn(CONTAINER_SANDBOX, comandos[0])
        self.assertNotIn("mega-sena-postgres-1", comandos[0])

    def test_dump_que_nao_relê_com_sandbox_de_pe_e_corrompido(self) -> None:
        def _reler(comando, **kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout=b"", stderr=b"nao e um dump\n"
            )

        contagem, gravadas = self._executar(
            [_linha(1, "banco")], sandbox=(True, True), reler=_reler
        )

        self.assertEqual(contagem["corrompidos"], 1)
        self.assertEqual(contagem["nao_verificados"], 0)
        self.assertEqual(gravadas, [(1, "corrompido")])

    def test_zip_de_codigo_independe_do_sandbox(self) -> None:
        """O `testzip` roda no host — sandbox fora do ar não afeta código."""
        def _reler(comando, **kwargs):
            raise AssertionError(f"não deveria chamar o Docker: {comando!r}")

        with patch.object(motor, "verificar_zip_codigo", return_value=None):
            contagem, gravadas = self._executar(
                [_linha(1, "codigo")], sandbox=(False, False), reler=_reler
            )

        self.assertEqual(contagem["conferidos"], 1)
        self.assertEqual(contagem["nao_verificados"], 0)
        self.assertEqual(gravadas, [(1, "valido")])


if __name__ == "__main__":
    unittest.main()
