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


if __name__ == "__main__":
    unittest.main()
