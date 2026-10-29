"""O lock de ciclo também vale para outro processo, não só outra thread."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import banco
import travas


class TravaProcessosTests(unittest.TestCase):
    def test_outro_processo_espera_o_mesmo_container(self):
        with tempfile.TemporaryDirectory() as pasta:
            catalogo = str(Path(pasta) / "catalogo.sqlite3")
            codigo = (
                "import sys; import banco, travas; banco.CAMINHO_CATALOGO=sys.argv[1]; "
                "print('pronto', flush=True); "
                "\nwith travas.container_exclusivo('container-sintetico'): print('adquiriu', flush=True)"
            )
            with patch.object(banco, "CAMINHO_CATALOGO", catalogo):
                with travas.container_exclusivo("container-sintetico"):
                    processo = subprocess.Popen(
                        [sys.executable, "-c", codigo, catalogo],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    try:
                        self.assertEqual(processo.stdout.readline().strip(), "pronto")
                        with self.assertRaises(subprocess.TimeoutExpired):
                            processo.wait(timeout=0.2)
                    except Exception:
                        processo.kill()
                        processo.communicate()
                        raise
                saida, erro = processo.communicate(timeout=10)
                self.assertEqual(processo.returncode, 0, erro)
                self.assertEqual(saida.strip(), "adquiriu")
