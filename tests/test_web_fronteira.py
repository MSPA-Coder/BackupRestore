"""A fronteira de origem de `web.py`.

Escutar em 127.0.0.1 não protege contra o que passa pelo navegador de quem
está no host: CSRF de origem cruzada e DNS rebinding. Estes testes guardam as
duas checagens que fecham isso.
"""

from __future__ import annotations

import unittest

import web


class FronteiraDeOrigem(unittest.TestCase):
    def setUp(self) -> None:
        web.app.config["TESTING"] = True
        self.cliente = web.app.test_client()

    # -- Host: mata o DNS rebinding ------------------------------------

    def test_host_de_fora_e_recusado_ate_em_get(self) -> None:
        """Um domínio hostil que resolva para 127.0.0.1 chega com o Host dele.

        Sem esta checagem, até as rotas de leitura (catálogo, progresso das
        execuções) viram leitura para fora.
        """
        resposta = self.cliente.get("/", headers={"Host": "dominio.hostil"})
        self.assertEqual(resposta.status_code, 403)

    def test_host_loopback_passa(self) -> None:
        for host in ("127.0.0.1:5401", "localhost:5401"):
            with self.subTest(host=host):
                resposta = self.cliente.get("/projetos", headers={"Host": host})
                self.assertNotEqual(resposta.status_code, 403)

    # -- Origin: mata o CSRF -------------------------------------------

    def test_post_de_outra_origem_e_recusado(self) -> None:
        resposta = self.cliente.post(
            "/artefato/1/fixar",
            headers={"Host": "127.0.0.1:5401", "Origin": "https://site.hostil"},
        )
        self.assertEqual(resposta.status_code, 403)

    def test_post_sem_origin_e_aceito(self) -> None:
        """Aceito de propósito: não é navegador, então não é o cenário de CSRF.

        Um POST de formulário sempre carrega `Origin` nos navegadores atuais;
        a ausência significa curl ou script. Recusar aqui quebraria o uso por
        linha de comando sem fechar nenhum ataque.

        O 404 e o que interessa: passou da fronteira e chegou na rota, que
        recusou por o artefato 999999 nao existir.
        """
        resposta = self.cliente.post(
            "/artefato/999999/fixar", headers={"Host": "127.0.0.1:5401"}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_post_da_propria_origem_e_aceito(self) -> None:
        resposta = self.cliente.post(
            "/artefato/999999/fixar",
            headers={"Host": "127.0.0.1:5401", "Origin": "http://127.0.0.1:5401"},
        )
        self.assertEqual(resposta.status_code, 404)


class ExtracaoDeHospedeiro(unittest.TestCase):
    def test_remove_porta_e_esquema(self) -> None:
        self.assertEqual(web._hospedeiro("127.0.0.1:5401"), "127.0.0.1")
        self.assertEqual(web._hospedeiro("http://127.0.0.1:5401"), "127.0.0.1")
        self.assertEqual(web._hospedeiro("https://site.hostil"), "site.hostil")

    def test_ipv6_entre_colchetes(self) -> None:
        self.assertEqual(web._hospedeiro("[::1]:5401"), "::1")


if __name__ == "__main__":
    unittest.main()
