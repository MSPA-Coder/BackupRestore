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
import threading
import time
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


class _CenarioDeVerificacao(unittest.TestCase):
    """Disco e Docker sob controle; sem testes próprios."""

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


class VerificarTests(_CenarioDeVerificacao):
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

    def test_dump_que_nao_rele_com_sandbox_de_pe_e_corrompido(self) -> None:
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


class VerificarEmParaleloTests(_CenarioDeVerificacao):
    """O paralelismo divide o tempo de `docker exec` sem mudar o veredito."""

    def test_releituras_de_dumps_se_sobrepoem(self) -> None:
        """Com releitura sequencial, a barreira nunca juntaria as duas."""
        barreira = threading.Barrier(2, timeout=5)

        def _reler(comando, **kwargs):
            barreira.wait()
            return _ok()

        contagem, gravadas = self._executar(
            [_linha(1, "banco"), _linha(2, "banco")], sandbox=(True, True), reler=_reler
        )

        self.assertEqual(contagem["conferidos"], 2)
        self.assertEqual(gravadas, [(1, "valido"), (2, "valido")])

    def test_sandbox_e_decidido_uma_unica_vez(self) -> None:
        chamadas: list[int] = []

        def _decidir() -> bool:
            chamadas.append(1)
            time.sleep(0.05)  # dá tempo de os outros trabalhadores chegarem
            return False

        with patch.object(motor, "_sandbox_disponivel", side_effect=_decidir):
            contagem, gravadas = self._executar(
                [_linha(i, "banco") for i in range(1, 9)],
                sandbox=(True, True),
                reler=lambda comando, **kwargs: _ok(),
            )

        self.assertEqual(len(chamadas), 1)
        self.assertEqual(contagem["nao_verificados"], 8)
        self.assertEqual(gravadas, [])

    def test_gravacao_segue_a_ordem_do_catalogo(self) -> None:
        """O primeiro a terminar não é o primeiro a ser gravado."""
        def _reler(comando, **kwargs):
            # O dump de id 1 é o mais lento; os outros terminam antes dele.
            if kwargs.get("entrada_arquivo", "").endswith("lento"):
                time.sleep(0.1)
            return _ok()

        caminhos = {1: self.arquivo + "lento"}
        with open(caminhos[1], "wb") as destino:
            destino.write(b"conteudo qualquer")
        with patch.object(
            motor, "caminho_artefato",
            side_effect=lambda relativo: caminhos[1] if relativo.endswith("-1") else self.arquivo,
        ):
            gravadas: list[tuple[int, str]] = []
            with (
                patch.object(banco, "listar_artefatos",
                             return_value=[_linha(i, "banco") for i in range(1, 5)]),
                patch.object(banco, "marcar_situacao_artefato",
                             side_effect=lambda id_, situacao: gravadas.append((id_, situacao))),
                patch.object(motor, "sha256_arquivo", return_value=SHA_QUALQUER),
                patch.object(motor, "estado_container", return_value=(True, True)),
                patch.object(motor, "_rodar", side_effect=_reler),
            ):
                motor.verificar()

        self.assertEqual([id_ for id_, _ in gravadas], [1, 2, 3, 4])

    def test_erro_inesperado_de_disco_chega_a_quem_chamou(self) -> None:
        with (
            patch.object(banco, "listar_artefatos",
                         return_value=[_linha(i, "codigo") for i in range(1, 20)]),
            patch.object(banco, "marcar_situacao_artefato"),
            patch.object(motor, "caminho_artefato", return_value=self.arquivo),
            patch.object(motor, "sha256_arquivo", side_effect=PermissionError("sem acesso")),
        ):
            with self.assertRaises(PermissionError):
                motor.verificar()


if __name__ == "__main__":
    unittest.main()
