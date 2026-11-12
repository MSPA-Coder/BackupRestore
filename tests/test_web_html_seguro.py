"""A interface não monta HTML com dado vindo do servidor (achado SEG-2).

Não há teste de navegador neste repositório. O que está aqui é um vigia de
texto sobre o JS e os templates, mais a checagem de que o Jinja escapa `.html`:
barra a volta dos sinks conhecidos, não prova o comportamento na tela. Esse foi
conferido à mão no navegador, com `erro` e `fase` contendo `<img onerror>`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import web

RAIZ = Path(__file__).resolve().parent.parent
SINKS = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\|\s*safe\b|Markup\(|autoescape\s+false"
)


class SemMontagemDeHtml(unittest.TestCase):
    def test_js_e_templates_sem_sink_de_html(self) -> None:
        arquivos = list((RAIZ / "static").rglob("*.js")) + list((RAIZ / "templates").glob("*.html"))
        self.assertTrue(arquivos)
        achados = [
            f"{arquivo.relative_to(RAIZ)}:{numero}: {linha.strip()}"
            for arquivo in arquivos
            for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1)
            if SINKS.search(linha)
        ]
        self.assertEqual(achados, [], "use textContent/createElement/replaceChildren")

    def test_jinja_escapa_templates_html(self) -> None:
        self.assertTrue(web.app.select_jinja_autoescape("projeto.html"))

    def test_templates_sem_script_ou_estilo_embutido(self) -> None:
        """A CSP (`script-src 'self'; style-src 'self'`) recusa o que está embutido."""
        embutido = re.compile(
            r"<style\b|<script\b(?![^>]*\bsrc=)|\sstyle\s*=|\son[a-z]+\s*=|javascript:",
            re.IGNORECASE,
        )
        achados = [
            f"{arquivo.name}:{numero}: {linha.strip()}"
            for arquivo in (RAIZ / "templates").glob("*.html")
            for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1)
            if embutido.search(linha)
        ]
        self.assertEqual(achados, [])
        js = (RAIZ / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"setAttribute\(\s*['\"]style")


class CabecalhosDeSeguranca(unittest.TestCase):
    def test_resposta_traz_csp_restrita(self) -> None:
        # 404: a rota não existe, então não toca o catálogo.
        resposta = web.app.test_client().get("/nao-existe", base_url="http://127.0.0.1:5401")
        self.assertEqual(resposta.status_code, 404)
        csp = resposta.headers["Content-Security-Policy"]
        for diretiva in ("default-src 'none'", "script-src 'self'", "style-src 'self'",
                         "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(diretiva, csp)
        self.assertNotIn("unsafe", csp)
        self.assertEqual(resposta.headers["X-Content-Type-Options"], "nosniff")

    def test_recusa_de_host_tambem_traz_os_cabecalhos(self) -> None:
        resposta = web.app.test_client().get("/", base_url="http://dominio.hostil")
        self.assertEqual(resposta.status_code, 403)
        self.assertIn("Content-Security-Policy", resposta.headers)


if __name__ == "__main__":
    unittest.main()
