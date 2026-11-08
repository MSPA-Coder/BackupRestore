"""A interface diz a verdade sobre o Docker e não quebra sem ele."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import motor
import web


class EstadoDoDocker(unittest.TestCase):
    def test_sem_executavel_docker_nada_existe(self) -> None:
        with patch.object(motor, "_rodar", side_effect=FileNotFoundError("docker")):
            self.assertEqual(motor.estado_container("qualquer"), (False, False))
            self.assertFalse(motor.docker_disponivel())
            self.assertIsNone(motor.projeto_compose_do_container("qualquer"))

    def test_daemon_travado_conta_como_indisponivel(self) -> None:
        with patch.object(motor, "_rodar", side_effect=subprocess.TimeoutExpired("docker", 5)):
            self.assertFalse(motor.docker_disponivel())

    def test_rodape_reflete_o_estado_real(self) -> None:
        with web.app.test_request_context("/"):
            with patch.object(motor, "docker_disponivel", return_value=False):
                self.assertEqual(web._estado_do_docker(), {"docker_ok": False})
            with patch.object(motor, "docker_disponivel", return_value=True):
                self.assertEqual(web._estado_do_docker(), {"docker_ok": True})


if __name__ == "__main__":
    unittest.main()
