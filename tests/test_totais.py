"""Os números do painel, da integridade e da retenção contam o acervo inteiro.

Antes, vinham de `len()` sobre `listar_artefatos(limite=...)`: passado o
limite, o total encolhia calado — e os corrompidos mais antigos sumiam do
alerta, que é exatamente onde não podiam sumir.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import banco
import web

VALIDOS = 1200  # acima do maior LIMIT usado pela interface (1000)


def _artefato(projeto: str, tipo: str, situacao: str, criado_em: str, bytes_: int = 10):
    return (projeto, tipo, "regular", situacao, f"projects/{projeto}/{tipo}/x", bytes_,
            "a" * 64, criado_em)


class TotaisDoAcervoTests(unittest.TestCase):
    def setUp(self) -> None:
        diretorio = tempfile.TemporaryDirectory()
        self.addCleanup(diretorio.cleanup)
        catalogo = patch.object(banco, "CAMINHO_CATALOGO",
                                os.path.join(diretorio.name, "c.sqlite3"))
        catalogo.start()
        self.addCleanup(catalogo.stop)
        banco.criar_tabelas()

        linhas = [_artefato("mega_sena", "banco", "valido", f"2026-09-{i % 28 + 1:02d}T00:00:00")
                  for i in range(VALIDOS)]
        # Os problemas são os mais antigos: ficariam fora de qualquer LIMIT.
        linhas += [_artefato("mega_sena", "banco", "corrompido", "2020-01-01T00:00:00"),
                   _artefato("networth", "codigo", "ausente", "2020-01-02T00:00:00"),
                   _artefato("networth", "codigo", "valido", "2026-09-01T00:00:00", 5),
                   _artefato("networth", "codigo", "removido", "2026-09-01T00:00:00", 999)]
        with banco.conectar() as conexao:
            conexao.executemany(
                "INSERT INTO artefatos (projeto, tipo, finalidade, situacao,"
                " caminho_relativo, bytes, sha256, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                linhas,
            )

    def test_totais_por_situacao_nao_tem_teto(self) -> None:
        totais = banco.totais_por_situacao()

        self.assertEqual(totais["valido"], {"quantidade": VALIDOS + 1, "bytes": VALIDOS * 10 + 5})
        self.assertEqual(totais["corrompido"]["quantidade"], 1)
        self.assertEqual(totais["ausente"]["quantidade"], 1)
        self.assertEqual(totais["criando"], {"quantidade": 0, "bytes": 0})

    def test_contagens_validas_por_projeto_e_tipo(self) -> None:
        self.assertEqual(
            banco.contagens_validas(),
            {("mega_sena", "banco"): VALIDOS, ("networth", "codigo"): 1},
        )

    def test_integridade_mostra_problemas_antigos(self) -> None:
        with web.app.test_client() as cliente:
            resposta = cliente.get("/integridade")

        self.assertEqual(resposta.status_code, 200)
        pagina = resposta.get_data(as_text=True)
        self.assertIn(f"<strong>{VALIDOS + 1}</strong>", pagina)
        self.assertIn("<strong>2</strong><small>ausentes ou corrompidos", pagina)

    def test_retencao_conta_todos_os_validos(self) -> None:
        with web.app.test_client() as cliente:
            resposta = cliente.get("/retencao")

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(f"{VALIDOS} <small>", resposta.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
