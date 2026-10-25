"""Sites e configuração dos servidores entram no backup sozinhos (25/10/2026),
e o resumo da tarefa agendada abre numa janela que não some."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import agendamento
import cli
import projetos
import vps
from projetos import (
    AMBIENTE_VPS,
    PROJETOS_FIXOS,
    carregar_descobertos,
    projeto_descoberto,
    registrar_descobertos,
)
from tests.test_agendamento import CatalogoFalso

ALVO = {"host": "192.0.2.10", "usuario": "ubuntu", "chave": "chave"}


def _remoto(slug: str, tipo: str = "volume") -> vps.DumpRemoto:
    extensao = "tar.gz" if tipo == "volume" else "dump"
    arquivo = f"{slug}_{tipo}_20261025_030000.{extensao}"
    return vps.DumpRemoto(
        slug_servidor=slug, arquivo=arquivo, caminho_remoto=f"{slug}/{arquivo}",
        bytes=10, sha256="0" * 64, carimbo="20261025_030000", tipo=tipo,
    )


class ProjetoDescobertoTests(unittest.TestCase):
    def test_slug_e_nome_dizem_de_que_servidor_vem(self) -> None:
        principal = projeto_descoberto("site_villa_rica", "principal")
        outro = projeto_descoberto("site_villa_rica", "vps2")
        self.assertEqual(principal.slug, "site_villa_rica_vps")
        self.assertEqual(outro.slug, "site_villa_rica_vps2")
        self.assertEqual(outro.nome, "Site villa-rica (VPS2)")
        self.assertEqual(
            projeto_descoberto("configuracao_servidor", "vps2").nome,
            "Configuração do servidor (VPS2)",
        )

    def test_a_pasta_no_servidor_e_o_slug_remoto(self) -> None:
        # Sem o campo, `slug_servidor` tiraria só `_vps` e pediria ao agente a
        # pasta `site_villa_rica_vps2`, que não existe lá.
        outro = projeto_descoberto("site_villa_rica", "vps2")
        self.assertEqual(outro.slug_servidor, "site_villa_rica")
        self.assertEqual(outro.ambiente, AMBIENTE_VPS)
        self.assertEqual(outro.tipos, ("volume",))

    def test_slug_remoto_fora_do_formato_do_agente_e_recusado(self) -> None:
        for invalido in ("Site", "site-x", "../x", "site1"):
            with self.subTest(invalido=invalido), self.assertRaises(ValueError):
                projeto_descoberto(invalido, "vps2")

    def test_registro_acumula_e_sobrevive_a_releitura(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "descobertos.json"
            self.assertEqual(carregar_descobertos(arquivo), ())
            registrar_descobertos([projeto_descoberto("site_a", "vps2")], arquivo)
            registrar_descobertos([projeto_descoberto("site_b", "principal"),
                                   projeto_descoberto("site_a", "vps2")], arquivo)
            slugs = sorted(p.slug for p in carregar_descobertos(arquivo))
            self.assertEqual(slugs, ["site_a_vps2", "site_b_vps"])

    def test_arquivo_estragado_e_lista_vazia(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "descobertos.json"
            arquivo.write_text("{não é json", encoding="utf-8")
            self.assertEqual(carregar_descobertos(arquivo), ())
            arquivo.write_text(json.dumps([{"slug_remoto": "X!", "servidor": "vps2"}]),
                               encoding="utf-8")
            self.assertEqual(carregar_descobertos(arquivo), ())

    def test_descoberto_nao_duplica_projeto_fixo(self) -> None:
        fixo = next(p for p in PROJETOS_FIXOS if p.ambiente == AMBIENTE_VPS)
        repetido = projeto_descoberto(fixo.slug_servidor, fixo.servidor)
        juntos = projetos._juntar(PROJETOS_FIXOS, (repetido,))
        self.assertEqual(len(juntos), len(PROJETOS_FIXOS))


class DescobertaNosServidoresTests(unittest.TestCase):
    def _descobrir(self, listagens: dict[str, object]):
        def listar(alvo, slug_servidor=None):
            resposta = listagens[alvo["host"]]
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

        servidores = {nome: {**ALVO, "host": nome} for nome in listagens}
        with (
            patch.object(vps, "servidores_vps", return_value=servidores),
            patch.object(vps, "listar_remoto", side_effect=listar),
            patch.object(vps.banco, "abrir_execucao", return_value=7) as abrir,
            patch.object(vps.banco, "fechar_execucao") as fechar,
        ):
            return vps.descobrir_projetos(PROJETOS_FIXOS), abrir, fechar

    def test_site_e_configuracao_novos_viram_projeto(self) -> None:
        resultado, _, _ = self._descobrir({
            "vps2": [_remoto("site_villa_rica"), _remoto("site_villa_rica"),
                     _remoto("configuracao_servidor")],
        })
        self.assertEqual(
            sorted(p.slug for p in resultado.novos),
            ["configuracao_servidor_vps2", "site_villa_rica_vps2"],
        )
        self.assertEqual(resultado.falhas, [])

    def test_o_que_ja_e_projeto_nao_e_novo(self) -> None:
        fixo = next(p for p in PROJETOS_FIXOS if p.ambiente == AMBIENTE_VPS)
        resultado, _, _ = self._descobrir({fixo.servidor: [_remoto(fixo.slug_servidor, "banco")]})
        self.assertEqual(resultado.novos, [])
        self.assertEqual(resultado.avisos, [])

    def test_banco_novo_e_aviso_e_nao_projeto(self) -> None:
        resultado, _, _ = self._descobrir({"principal": [_remoto("app_novo", "banco")]})
        self.assertEqual(resultado.novos, [])
        self.assertEqual(len(resultado.avisos), 1)
        self.assertIn("app_novo", resultado.avisos[0])

    def test_servidor_fora_do_ar_e_falha_registrada_no_catalogo(self) -> None:
        resultado, abrir, fechar = self._descobrir({
            "vps2": vps.FalhaDeSincronizacao("listar falhou: Connection timed out"),
            "principal": [_remoto("site_x")],
        })
        self.assertEqual(len(resultado.falhas), 1)
        self.assertIn("Connection timed out", resultado.falhas[0])
        abrir.assert_called_once_with("servidor_vps2", "descoberta")
        fechar.assert_called_once_with(7, "falha", "listar falhou: Connection timed out")
        self.assertEqual([p.slug for p in resultado.novos], ["site_x_vps"])


class SincronizarTodosTests(unittest.TestCase):
    def test_descobertos_sao_registrados_e_sincronizados_na_mesma_rodada(self) -> None:
        novo = projeto_descoberto("site_villa_rica", "vps2")
        descoberta = vps.Descoberta(novos=[novo])
        sincronizados: list[str] = []
        with (
            patch.object(vps, "descobrir_projetos", return_value=descoberta),
            patch.object(cli, "registrar_descobertos") as registrar,
            patch.object(vps, "sincronizar_projeto",
                         side_effect=lambda p: sincronizados.append(p.slug) or vps.ResultadoSincronizacao()),
            redirect_stdout(io.StringIO()) as saida,
        ):
            codigo = cli.comando_sincronizar_vps(Namespace(todos=True, projeto=None))
        self.assertEqual(codigo, 0)
        registrar.assert_called_once_with([novo])
        self.assertIn("site_villa_rica_vps2", sincronizados)
        self.assertIn("Site villa-rica (VPS2)", saida.getvalue())

    def test_falha_da_descoberta_faz_a_rodada_falhar(self) -> None:
        descoberta = vps.Descoberta(falhas=["servidor 'vps2': listar falhou"])
        with (
            patch.object(vps, "descobrir_projetos", return_value=descoberta),
            patch.object(vps, "sincronizar_projeto", return_value=vps.ResultadoSincronizacao()),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as erros,
        ):
            codigo = cli.comando_sincronizar_vps(Namespace(todos=True, projeto=None))
        self.assertEqual(codigo, 1)
        self.assertIn("vps2", erros.getvalue())


class JanelaDoResumoTests(unittest.TestCase):
    def _rodar(self, argv: tuple[str, ...]) -> list[Path]:
        janelas: list[Path] = []
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / "agendamento.local.json"
            estado.write_text('{"habilitado_em":"2026-08-27","ultimo_backup_local_em":"2026-10-25"}',
                              encoding="utf-8")
            agendamento.main(
                argv,
                arquivo_estado=estado,
                obter_agora=lambda: datetime(2026, 10, 26, 3, 30),
                dormir=lambda _: None,
                executar=lambda _: 0,
                arquivo_resumo=Path(pasta) / "ultima-execucao.txt",
                catalogo=CatalogoFalso(),
                mostrar_janela=janelas.append,
            )
        return janelas

    def test_a_tarefa_pede_a_janela(self) -> None:
        self.assertEqual(len(self._rodar(("--janela",))), 1)

    def test_sem_o_pedido_nenhuma_janela_abre(self) -> None:
        # Testes e execuções à mão não podem abrir janela no PC de ninguém.
        self.assertEqual(self._rodar(()), [])

    def test_o_comando_mostra_o_arquivo_e_fica_aberto(self) -> None:
        comando = agendamento.comando_janela_resumo(Path("C:/x/it's/ultima-execucao.txt"))
        self.assertIn("-NoExit", comando)
        script = base64.b64decode(comando[-1]).decode("utf-16-le")
        self.assertIn("-LiteralPath 'C:\\x\\it''s\\ultima-execucao.txt'", script.replace("/", "\\"))
        self.assertIn("RESULTADO \\.: OK", script)

    def test_janela_abre_em_console_proprio_e_falha_nao_derruba(self) -> None:
        chamadas: list[int] = []

        def abrir(comando, creationflags):
            chamadas.append(creationflags)
            if creationflags & agendamento.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError("o job não deixa sair")
            raise OSError("sem powershell")

        with redirect_stderr(io.StringIO()) as erros:
            agendamento.abrir_janela_resumo(Path("x.txt"), abrir=abrir)
        self.assertEqual(len(chamadas), 2)
        self.assertTrue(all(f & agendamento.CREATE_NEW_CONSOLE for f in chamadas))
        self.assertIn("janela do resumo", erros.getvalue())


if __name__ == "__main__":
    unittest.main()
