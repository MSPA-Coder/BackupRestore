from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import agendamento


class AgendamentoTests(unittest.TestCase):
    def test_semana_normal_e_domingo_perdido(self) -> None:
        estado = {"habilitado_em": "2026-08-27", "ultimo_backup_local_em": "2026-08-30"}
        self.assertFalse(agendamento.backup_local_devido(date(2026, 8, 31), estado))

        estado["ultimo_backup_local_em"] = "2026-08-23"
        self.assertTrue(agendamento.backup_local_devido(date(2026, 8, 31), estado))

    def test_domingo_espera_ate_quatro_horas(self) -> None:
        self.assertEqual(
            agendamento.segundos_ate_quatro_horas(datetime(2026, 8, 30, 3, 30)),
            30 * 60,
        )
        self.assertEqual(
            agendamento.segundos_ate_quatro_horas(datetime(2026, 8, 31, 3, 30)),
            0,
        )

    def test_vps_antecede_backup_local_e_marca_sucesso(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            arquivo = Path(diretorio) / "agendamento.local.json"
            arquivo.write_text('{"habilitado_em":"2026-08-27"}', encoding="utf-8")
            comandos: list[tuple[str, ...]] = []
            esperas: list[float] = []

            resultado = agendamento.main(
                arquivo_estado=arquivo,
                obter_agora=lambda: datetime(2026, 8, 30, 4, 5),
                dormir=esperas.append,
                executar=lambda argumentos: comandos.append(tuple(argumentos)) or 0,
            )

            self.assertEqual(resultado, 0)
            self.assertEqual(esperas, [])
            self.assertEqual(comandos, [("sincronizar-vps", "--todos"), ("backup", "--todos")])
            self.assertIn("2026-08-30", arquivo.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
