"""A fronteira de origem de `web.py`.

Escutar em 127.0.0.1 não protege contra o que passa pelo navegador de quem
está no host: CSRF de origem cruzada e DNS rebinding. Estes testes guardam as
duas checagens que fecham isso.

Nenhum teste aqui toca o catálogo. A fronteira decide em `before_request`,
antes de qualquer rota rodar, então exercitá-la não precisa de banco — e
depender de um faria o resultado variar com a máquina, que é exatamente o que
um teste não pode fazer.
"""

from __future__ import annotations

import unittest

from werkzeug.exceptions import Forbidden

import web


class DecisaoDaFronteira(unittest.TestCase):
    """O que o próprio `_recusar_origem_estranha` decide, caso a caso."""

    def _decidir(self, caminho: str, metodo: str = "GET", **headers: str):
        """Roda a checagem no contexto de uma requisição, sem despachar rota."""
        with web.app.test_request_context(caminho, method=metodo, headers=headers):
            return web._recusar_origem_estranha()

    # -- Host: mata o DNS rebinding ------------------------------------

    def test_host_de_fora_e_recusado_ate_em_get(self) -> None:
        """Um domínio hostil que resolva para 127.0.0.1 chega com o Host dele.

        Sem esta checagem, até as rotas de leitura (catálogo, progresso das
        execuções) viram leitura para fora.
        """
        with self.assertRaises(Forbidden):
            self._decidir("/", host="dominio.hostil")

    def test_host_loopback_passa(self) -> None:
        for host in ("127.0.0.1:5401", "localhost:5401", "[::1]:5401"):
            with self.subTest(host=host):
                self.assertIsNone(self._decidir("/projetos", host=host))

    # -- Origin: mata o CSRF -------------------------------------------

    def test_post_de_outra_origem_e_recusado(self) -> None:
        with self.assertRaises(Forbidden):
            self._decidir(
                "/artefato/1/fixar",
                metodo="POST",
                host="127.0.0.1:5401",
                origin="https://site.hostil",
            )

    def test_origem_estranha_em_get_nao_e_recusada(self) -> None:
        """A checagem de `Origin` vale só para método mutante.

        Um GET com `Origin` de outra página é o caso normal de um link ou de
        uma imagem: não altera nada e não é o cenário de CSRF.
        """
        self.assertIsNone(
            self._decidir("/", host="127.0.0.1:5401", origin="https://site.hostil")
        )

    def test_post_sem_origin_e_aceito(self) -> None:
        """Aceito de propósito: não é navegador, então não é o cenário de CSRF.

        Um POST de formulário sempre carrega `Origin` nos navegadores atuais;
        a ausência significa curl ou script. Recusar aqui quebraria o uso por
        linha de comando sem fechar nenhum ataque.
        """
        self.assertIsNone(
            self._decidir("/artefato/1/fixar", metodo="POST", host="127.0.0.1:5401")
        )

    def test_post_da_propria_origem_e_aceito(self) -> None:
        self.assertIsNone(
            self._decidir(
                "/artefato/1/fixar",
                metodo="POST",
                host="127.0.0.1:5401",
                origin="http://127.0.0.1:5401",
            )
        )

    def test_todo_metodo_mutante_e_coberto(self) -> None:
        """Se um método entrar em `METODOS_MUTANTES`, ele já nasce protegido."""
        for metodo in sorted(web.METODOS_MUTANTES):
            with self.subTest(metodo=metodo), self.assertRaises(Forbidden):
                self._decidir(
                    "/artefato/1/fixar",
                    metodo=metodo,
                    host="127.0.0.1:5401",
                    origin="https://site.hostil",
                )


class FronteiraEstaLigada(unittest.TestCase):
    """A checagem está registrada como `before_request` e para de verdade.

    A classe acima prova o que a função decide; esta prova que ela roda —
    e que roda **antes** da rota, sem chegar a consultar o catálogo.
    """

    def setUp(self) -> None:
        web.app.config["TESTING"] = True
        self.cliente = web.app.test_client()

    def test_get_com_host_de_fora_para_antes_da_rota(self) -> None:
        resposta = self.cliente.get("/", headers={"Host": "dominio.hostil"})
        self.assertEqual(resposta.status_code, 403)

    def test_post_de_outra_origem_para_antes_da_rota(self) -> None:
        resposta = self.cliente.post(
            "/artefato/1/fixar",
            headers={"Host": "127.0.0.1:5401", "Origin": "https://site.hostil"},
        )
        self.assertEqual(resposta.status_code, 403)


class ExtracaoDeHospedeiro(unittest.TestCase):
    def test_remove_porta_e_esquema(self) -> None:
        self.assertEqual(web._hospedeiro("127.0.0.1:5401"), "127.0.0.1")
        self.assertEqual(web._hospedeiro("http://127.0.0.1:5401"), "127.0.0.1")
        self.assertEqual(web._hospedeiro("https://site.hostil"), "site.hostil")

    def test_ipv6_entre_colchetes(self) -> None:
        self.assertEqual(web._hospedeiro("[::1]:5401"), "::1")


if __name__ == "__main__":
    unittest.main()
