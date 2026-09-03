from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import agendamento
import banco
import web
from projetos import AMBIENTE_VPS, PROJETOS


class CatalogoFalso:
    """Substitui `banco` no orquestrador: um teste de agendamento nao pode
    escrever no catalogo real deste PC."""

    def __init__(self, execucoes: list[dict[str, object]] | None = None) -> None:
        self.execucoes = execucoes or []

    def criar_tabelas(self) -> None:
        pass

    def ultimo_id_execucao(self) -> int:
        return 100

    def execucoes_desde(self, id_minimo: int) -> list[dict[str, object]]:
        return [e for e in self.execucoes if int(e["id"]) > id_minimo]


def _execucao(
    id_: int,
    projeto: str,
    operacao: str,
    situacao: str,
    erro: str | None = None,
) -> dict[str, object]:
    return {
        "id": id_,
        "projeto": projeto,
        "operacao": operacao,
        "situacao": situacao,
        "erro": erro,
        "pedido_em": "2026-09-02T03:30:00",
        "terminado_em": "2026-09-02T03:30:05",
    }


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
            resumo = Path(diretorio) / "ultima-execucao.txt"
            comandos: list[tuple[str, ...]] = []
            esperas: list[float] = []

            resultado = agendamento.main(
                arquivo_estado=arquivo,
                obter_agora=lambda: datetime(2026, 8, 30, 4, 5),
                dormir=esperas.append,
                executar=lambda argumentos: comandos.append(tuple(argumentos)) or 0,
                arquivo_resumo=resumo,
                catalogo=CatalogoFalso(),
            )

            self.assertEqual(resultado, 0)
            self.assertEqual(esperas, [])
            self.assertEqual(comandos, [("sincronizar-vps", "--todos"), ("backup", "--todos")])
            self.assertIn("2026-08-30", arquivo.read_text(encoding="utf-8"))
            self.assertIn("OK", resumo.read_text(encoding="utf-8"))


class ResumoLegivelTests(unittest.TestCase):
    """A falha precisa ser legivel sem abrir o catalogo — foi por nao ser que
    uma execucao com seis sub-operacoes quebradas passou quatro dias
    despercebida."""

    def test_resumo_nomeia_projeto_operacao_e_motivo_inteiro(self) -> None:
        motivo = "listar falhou: ssh: connect to host 100.94.82.91 port 22: Permission denied"
        texto = agendamento.montar_resumo(
            inicio=datetime(2026, 9, 2, 3, 30),
            fim=datetime(2026, 9, 2, 3, 41),
            execucoes=[
                _execucao(1, "mega_sena_vps", "sincronizacao", "falha", motivo),
                _execucao(2, "controle_bancario", "backup", "sucesso"),
            ],
            codigo_vps=1,
            codigo_local=0,
        )
        self.assertIn("FALHA", texto)
        self.assertIn("mega_sena_vps", texto)
        self.assertIn("sincronizacao", texto)
        self.assertIn("controle_bancario", texto)
        self.assertIn("02/09/2026 03:30:00", texto)
        # O motivo e quebrado em varias linhas, mas nenhuma palavra some.
        self.assertIn(motivo, " ".join(texto.split()))

    def test_tudo_certo_diz_ok_e_backup_nao_devido(self) -> None:
        texto = agendamento.montar_resumo(
            inicio=datetime(2026, 9, 2, 3, 30),
            fim=datetime(2026, 9, 2, 3, 33),
            execucoes=[_execucao(1, "mega_sena_vps", "sincronizacao", "sucesso")],
            codigo_vps=0,
            codigo_local=None,
        )
        self.assertIn("OK", texto)
        self.assertNotIn("FALHARAM", texto)
        self.assertIn("não era devido", texto)

    def test_falha_antes_de_abrir_qualquer_execucao_nao_fica_muda(self) -> None:
        texto = agendamento.montar_resumo(
            inicio=datetime(2026, 9, 2, 3, 30),
            fim=datetime(2026, 9, 2, 3, 30),
            execucoes=[],
            codigo_vps=2,
            codigo_local=None,
        )
        self.assertIn("FALHA", texto)
        self.assertIn("Nenhuma operação chegou a abrir", texto)

    def test_execucao_sem_desfecho_aparece_separada(self) -> None:
        texto = agendamento.montar_resumo(
            inicio=datetime(2026, 9, 2, 3, 30),
            fim=datetime(2026, 9, 2, 3, 31),
            execucoes=[_execucao(1, "mega_sena", "backup", "rodando")],
            codigo_vps=0,
            codigo_local=1,
        )
        self.assertIn("SEM DESFECHO", texto)
        self.assertIn("mega_sena", texto)

    def test_rodada_com_falha_escreve_o_arquivo_e_sai_com_um(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            arquivo = Path(diretorio) / "agendamento.local.json"
            arquivo.write_text('{"habilitado_em":"2026-08-27"}', encoding="utf-8")
            resumo = Path(diretorio) / "ultima-execucao.txt"
            catalogo = CatalogoFalso(
                [
                    _execucao(
                        101,
                        "conforto_termico_vps",
                        "sincronizacao",
                        "falha",
                        "listar falhou: Permission denied",
                    )
                ]
            )

            resultado = agendamento.main(
                arquivo_estado=arquivo,
                obter_agora=lambda: datetime(2026, 9, 2, 3, 30),
                dormir=lambda _: None,
                executar=lambda argumentos: 1,
                arquivo_resumo=resumo,
                catalogo=catalogo,
            )

            self.assertEqual(resultado, 1)
            texto = resumo.read_text(encoding="utf-8")
            self.assertIn("FALHA", texto)
            self.assertIn("conforto_termico_vps", texto)
            self.assertIn("Permission denied", texto)


class FalhasAtuaisTests(unittest.TestCase):
    """`falhas_atuais` alimenta o destaque do painel: e o estado de agora, nao
    o historico."""

    def test_sucesso_posterior_apaga_a_falha_e_o_resto_permanece(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            with patch.object(banco, "CAMINHO_CATALOGO", str(Path(diretorio, "c.sqlite3"))):
                banco.criar_tabelas()
                self.assertIsNone(banco.momento_ultima_execucao())

                corrigida = banco.abrir_execucao("mega_sena_vps", "sincronizacao")
                banco.fechar_execucao(corrigida, "falha", "Permission denied")
                persistente = banco.abrir_execucao("conforto_termico", "backup")
                banco.fechar_execucao(persistente, "falha", "contêiner não existe")

                self.assertEqual(len(banco.falhas_atuais()), 2)

                # A sincronizacao voltou a funcionar; so o backup local segue falhando.
                nova = banco.abrir_execucao("mega_sena_vps", "sincronizacao")
                banco.fechar_execucao(nova, "sucesso")

                falhas = banco.falhas_atuais()
                self.assertEqual([f["projeto"] for f in falhas], ["conforto_termico"])
                self.assertEqual(falhas[0]["erro"], "contêiner não existe")
                self.assertIsNotNone(banco.momento_ultima_execucao())

    def test_execucao_travada_em_rodando_nao_esconde_a_falha_anterior(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            with patch.object(banco, "CAMINHO_CATALOGO", str(Path(diretorio, "c.sqlite3"))):
                banco.criar_tabelas()
                antiga = banco.abrir_execucao("conforto_termico", "backup")
                banco.fechar_execucao(antiga, "falha", "contêiner não existe")
                banco.abrir_execucao("conforto_termico", "backup")  # ficou 'rodando'

                self.assertEqual([f["id"] for f in banco.falhas_atuais()], [antiga])


class MarcaDaRodadaTests(unittest.TestCase):
    def test_execucoes_desde_pega_so_o_que_esta_rodada_abriu(self) -> None:
        with tempfile.TemporaryDirectory() as diretorio:
            with patch.object(banco, "CAMINHO_CATALOGO", str(Path(diretorio, "c.sqlite3"))):
                banco.criar_tabelas()
                self.assertEqual(banco.ultimo_id_execucao(), 0)
                anterior = banco.abrir_execucao("mega_sena", "backup")

                marca = banco.ultimo_id_execucao()
                self.assertEqual(marca, anterior)

                desta = banco.abrir_execucao("mega_sena", "backup")
                self.assertEqual([e["id"] for e in banco.execucoes_desde(marca)], [desta])


class DestaqueDoPainelTests(unittest.TestCase):
    """O painel só destaca o que a tarefa diária repete: uma falha que nada
    tenta de novo ficaria para sempre no alerta e o esvaziaria de sentido."""

    def test_recusa_de_chamada_manual_nao_polui_o_destaque(self) -> None:
        local = next(p for p in PROJETOS if p.ambiente != AMBIENTE_VPS)
        remoto = next(p for p in PROJETOS if p.ambiente == AMBIENTE_VPS)
        catalogo = [
            _execucao(1, local.slug, "backup", "falha", "contêiner não existe"),
            _execucao(2, remoto.slug, "sincronizacao", "falha", "Permission denied"),
            # Chamada manual errada: o motor recusa `backup` em ambiente VPS.
            _execucao(3, remoto.slug, "backup", "falha", "ambiente 'vps' não produz backup"),
            # Projeto que saiu de `projetos.py` desde então.
            _execucao(4, "projeto_aposentado", "backup", "falha", "sumiu"),
        ]

        with patch.object(banco, "falhas_atuais", return_value=catalogo):
            destacadas = web._falhas_do_agendamento()

        self.assertEqual(
            [(f["projeto"], f["operacao"]) for f in destacadas],
            [(local.slug, "backup"), (remoto.slug, "sincronizacao")],
        )


if __name__ == "__main__":
    unittest.main()
