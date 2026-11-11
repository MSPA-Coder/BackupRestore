from __future__ import annotations

import base64
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import banco
import vigia
import vps
from projetos import AMBIENTE_VPS, Projeto

AGORA = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
ALVO = {"host": "100.64.0.10", "usuario": "ubuntu", "chave": "C:/chave"}


def _projeto(slug: str, *, ambiente: str = AMBIENTE_VPS, servidor: str = "vps1") -> Projeto:
    return Projeto(
        slug=slug, nome=slug, pasta="", container="", usuario="", banco="",
        ambiente=ambiente, servidor=servidor,
    )


def _dump(slug: str, carimbo: str, sha256: str = "a" * 64) -> vps.DumpRemoto:
    arquivo = f"{slug}_banco_{carimbo}.dump"
    return vps.DumpRemoto(
        slug_servidor=slug, arquivo=arquivo, caminho_remoto=f"{slug}/{arquivo}",
        bytes=10, sha256=sha256, carimbo=carimbo,
    )


class AvaliarTests(unittest.TestCase):
    def _avaliar(self, remotos, *, baixados=(), projetos=None, servidores=None):
        projetos = projetos or [_projeto("renda_vps")]

        def listar(alvo, slug):
            resposta = remotos.get(slug, [])
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

        return vigia.avaliar(
            AGORA,
            servidores={"vps1": ALVO} if servidores is None else servidores,
            projetos=projetos,
            listar=listar,
            ja_baixado=lambda projeto, dump: dump.arquivo in baixados,
        )

    def _slug(self, projeto: Projeto) -> str:
        return projeto.slug_servidor

    def test_dump_recente_ou_ja_baixado_nao_e_aviso(self) -> None:
        projeto = _projeto("renda_vps")
        slug = self._slug(projeto)
        recente = _dump(slug, "20261104_060000")  # 9 h
        antigo_baixado = _dump(slug, "20261102_060000")  # 57 h, mas já está aqui
        veredito = self._avaliar(
            {slug: [recente, antigo_baixado]}, baixados={antigo_baixado.arquivo},
            projetos=[projeto],
        )
        self.assertTrue(veredito.em_dia)
        self.assertEqual(veredito.projetos_consultados, 1)

    def test_dump_antigo_que_nao_chegou_e_aviso(self) -> None:
        projeto = _projeto("renda_vps")
        slug = self._slug(projeto)
        dois_dias = _dump(slug, "20261102_060000")
        ontem = _dump(slug, "20261103_060000")  # 33 h: passou do limite de 30
        veredito = self._avaliar({slug: [ontem, dois_dias]}, projetos=[projeto])
        self.assertFalse(veredito.em_dia)
        self.assertEqual([p.arquivo for p in veredito.pendentes],
                         [dois_dias.arquivo, ontem.arquivo])
        self.assertEqual(veredito.pendentes[0].horas(AGORA), 57)

    def test_limite_e_estrito_ao_redor_de_30_horas(self) -> None:
        projeto = _projeto("renda_vps")
        slug = self._slug(projeto)
        abaixo = _dump(slug, "20261103_090001")  # 29 h 59 min 59 s
        no_limite = _dump(slug, "20261103_090000")  # 30 h exatas
        veredito = self._avaliar({slug: [abaixo, no_limite]}, projetos=[projeto])
        self.assertEqual([p.arquivo for p in veredito.pendentes], [no_limite.arquivo])

    def test_servidor_sem_resposta_e_falha_e_nao_esconde_os_outros(self) -> None:
        caido, de_pe = _projeto("crv_vps"), _projeto("cb_vps")
        antigo = _dump(self._slug(de_pe), "20261101_060000")
        veredito = self._avaliar(
            {
                self._slug(caido): vps.FalhaDeSincronizacao("listar falhou: Permission denied"),
                self._slug(de_pe): [antigo],
            },
            projetos=[caido, de_pe],
        )
        self.assertEqual(veredito.falhas, ["crv_vps: listar falhou: Permission denied"])
        self.assertEqual([p.arquivo for p in veredito.pendentes], [antigo.arquivo])
        self.assertEqual(veredito.projetos_consultados, 1)

    def test_servidor_nao_configurado_e_falha(self) -> None:
        veredito = self._avaliar({}, servidores={})
        self.assertEqual(veredito.falhas, ["renda_vps: servidor 'vps1' não configurado"])

    def test_projeto_local_nao_e_consultado(self) -> None:
        local = _projeto("controle_local", ambiente="local")

        def listar(alvo, slug):
            raise AssertionError("projeto local não pode ir ao VPS")

        veredito = vigia.avaliar(AGORA, servidores={"vps1": ALVO}, projetos=[local],
                                 listar=listar, ja_baixado=lambda p, d: False)
        self.assertTrue(veredito.em_dia)
        self.assertEqual(veredito.projetos_consultados, 0)


class CatalogadoTests(unittest.TestCase):
    """Contra um catálogo SQLite temporário, nunca o deste PC."""

    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        controle = patch.object(banco, "CAMINHO_CATALOGO", str(Path(pasta.name) / "catalogo.sqlite3"))
        controle.start()
        self.addCleanup(controle.stop)
        banco.criar_tabelas()

    def _registrar(self, projeto: str, arquivo: str, sha256: str, situacao: str) -> None:
        artefato = banco.registrar_artefato(
            projeto=projeto, tipo="banco", caminho_relativo=f"projects/{projeto}/banco/{arquivo}",
            bytes_=10, sha256=sha256, duracao_ms=1, execucao_id=None,
        )
        if situacao != "valido":
            banco.marcar_situacao_artefato(artefato, situacao)

    def test_reconhece_pelo_sha256_mesmo_depois_da_retencao(self) -> None:
        dump = _dump("x", "20261101_060000", sha256="b" * 64)
        self._registrar("renda_vps", "outro_nome.dump", "b" * 64, "removido")
        self.assertTrue(vigia.catalogado("renda_vps", dump))

    def test_reconhece_pelo_nome_quando_o_servidor_nao_tem_hash(self) -> None:
        dump = _dump("x", "20261101_060000", sha256="sem-hash")
        self._registrar("renda_vps", dump.arquivo, "c" * 64, "valido")
        self.assertTrue(vigia.catalogado("renda_vps", dump))

    def test_nao_confunde_projeto_nem_arquivo(self) -> None:
        dump = _dump("x", "20261101_060000", sha256="d" * 64)
        self._registrar("outro_vps", dump.arquivo, "d" * 64, "valido")
        self._registrar("renda_vps", "x_banco_20261031_060000.dump", "e" * 64, "valido")
        self.assertFalse(vigia.catalogado("renda_vps", dump))


class SaidaTests(unittest.TestCase):
    def _veredito(self) -> vigia.Veredito:
        return vigia.Veredito(
            verificado_em=AGORA,
            pendentes=[vigia.Pendente("renda_vps", "x_banco_20261102_060000.dump",
                                      AGORA - timedelta(hours=57))],
            falhas=["cb_vps: listar falhou: Permission denied"],
            projetos_consultados=1,
        )

    def test_mensagem_resume_o_mais_antigo_e_a_falha(self) -> None:
        titulo, texto = vigia.mensagem(self._veredito())
        self.assertEqual(titulo, "Backup do VPS parado")
        self.assertIn("1 dump do VPS sem baixar", texto)
        self.assertIn("renda_vps", texto)
        self.assertIn("1 projeto sem resposta do servidor: cb_vps", texto)

    def test_relatorio_lista_tudo_e_o_que_conferir(self) -> None:
        relatorio = vigia.montar_relatorio(self._veredito())
        self.assertIn("RESULTADO: ATENÇÃO", relatorio)
        self.assertIn("x_banco_20261102_060000.dump  (57 h)", relatorio)
        self.assertIn("Get-ScheduledTaskInfo -TaskName BackupRestore", relatorio)

    def test_relatorio_em_dia(self) -> None:
        relatorio = vigia.montar_relatorio(vigia.Veredito(verificado_em=AGORA,
                                                          projetos_consultados=6))
        self.assertIn("RESULTADO: EM DIA", relatorio)
        self.assertNotIn("O que conferir", relatorio)

    def test_notificacao_escapa_o_texto_e_fica_na_tela(self) -> None:
        comando = vigia.comando_notificacao("Título <b>", "texto & 'aspas'")
        script = base64.b64decode(comando[-1]).decode("utf-16-le")
        self.assertIn('scenario="reminder"', script)
        self.assertIn("Título &lt;b&gt;", script)
        self.assertIn("texto &amp; ''aspas''", script)
        self.assertEqual(comando[0], "powershell.exe")

    def test_notificacao_recusada_abre_a_janela(self) -> None:
        janelas: list[Path] = []
        saiu = vigia.notificar(
            "t", "x", Path("relatorio.txt"),
            executar=lambda *a, **k: subprocess.CompletedProcess(a, 1),
            janela=janelas.append,
        )
        self.assertFalse(saiu)
        self.assertEqual(janelas, [Path("relatorio.txt")])

    def test_notificacao_aceita_nao_abre_janela(self) -> None:
        janelas: list[Path] = []
        saiu = vigia.notificar(
            "t", "x", Path("relatorio.txt"),
            executar=lambda *a, **k: subprocess.CompletedProcess(a, 0),
            janela=janelas.append,
        )
        self.assertTrue(saiu)
        self.assertEqual(janelas, [])


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.arquivo = Path(pasta.name) / "vigia-ultimo.txt"
        self.avisos: list[tuple[str, str, Path]] = []

    def _main(self, veredito: vigia.Veredito, argv=()) -> int:
        return vigia.main(
            argv,
            obter_agora=lambda: AGORA,
            servidores=lambda: {"vps1": ALVO},
            avaliar_=lambda agora, servidores: veredito,
            avisar=lambda titulo, texto, arquivo: self.avisos.append((titulo, texto, arquivo)) or True,
            arquivo_veredito=self.arquivo,
        )

    def test_em_dia_grava_o_veredito_e_nao_avisa(self) -> None:
        codigo = self._main(vigia.Veredito(verificado_em=AGORA, projetos_consultados=6))
        self.assertEqual(codigo, 0)
        self.assertIn("EM DIA", self.arquivo.read_text(encoding="utf-8"))
        self.assertEqual(self.avisos, [])

    def test_pendencia_avisa_e_sai_com_1(self) -> None:
        veredito = vigia.Veredito(verificado_em=AGORA, falhas=["cb_vps: sem rota"])
        codigo = self._main(veredito)
        self.assertEqual(codigo, 1)
        self.assertEqual(len(self.avisos), 1)
        self.assertEqual(self.avisos[0][2], self.arquivo)
        self.assertIn("ATENÇÃO", self.arquivo.read_text(encoding="utf-8"))

    def test_teste_de_notificacao_nao_toca_no_veredito_real(self) -> None:
        self.arquivo.write_text("veredito real\n", encoding="utf-8")
        codigo = self._main(vigia.Veredito(verificado_em=AGORA), argv=["--testar-notificacao"])
        self.assertEqual(codigo, 0)
        self.assertEqual(self.arquivo.read_text(encoding="utf-8"), "veredito real\n")
        self.assertEqual(self.avisos[0][2].name, "vigia-teste.txt")


if __name__ == "__main__":
    unittest.main()
