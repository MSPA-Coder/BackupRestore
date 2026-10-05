"""O catálogo se reconstrói dos manifestos, sem afrouxar nenhuma regra dele.

O que se protege aqui: a reconstrução é uma segunda porta de entrada para o
catálogo, e uma porta que dispensasse a releitura desfaria a invariante 2 do
AGENTS.md. Por isso quase todos os casos são sobre o que NÃO entra.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import banco
import cli
import configuracao
import motor
import reconstruir
from projetos import PROJETOS
from tests.test_volume import COPIA_BOA

LOCAL = next(p for p in PROJETOS if p.ambiente == "local")
OUTRO_LOCAL = next(p for p in PROJETOS if p.ambiente == "local" and p.slug != LOCAL.slug)


@contextmanager
def _ambiente(diretorio: str):
    """Raiz de backup e catálogo descartáveis: nada toca os reais deste PC."""
    raiz = Path(diretorio, "backups")
    with (
        patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(Path(diretorio, "config.json"))),
        patch.object(banco, "CAMINHO_CATALOGO", str(Path(diretorio, "catalogo.sqlite3"))),
        patch.dict(
            os.environ,
            {
                configuracao.VARIAVEL_RAIZ_PERMITIDA: str(raiz),
                "BACKUPRESTORE_RAIZ_BACKUP": str(raiz),
            },
            clear=False,
        ),
    ):
        banco.criar_tabelas()
        yield raiz


def _zip_de_codigo() -> bytes:
    bruto = io.BytesIO()
    with zipfile.ZipFile(bruto, "w") as pacote:
        pacote.writestr("backuprestore-manifest.json", "{}")
        pacote.writestr("app.py", "print('oi')\n")
    return bruto.getvalue()


def _gravar(raiz: Path, slug: str, tipo: str, nome: str, conteudo: bytes, *, manifesto=True, **sobrescritas):
    """Põe um artefato e o manifesto ao lado, como `motor._promover` faz."""
    pasta = Path(raiz, "projects", slug, tipo)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / nome).write_bytes(conteudo)
    if manifesto:
        dados = {
            "projeto": slug,
            "tipo": tipo,
            "arquivo": nome,
            "criado_em": "2026-09-01T03:00:05",
            "bytes": len(conteudo),
            "sha256": hashlib.sha256(conteudo).hexdigest(),
            "duracao_ms": 1234,
            **sobrescritas,
        }
        (pasta / (nome + ".manifest.json")).write_text(json.dumps(dados), encoding="utf-8")
    return pasta / nome


def _linhas():
    return banco.listar_artefatos(None)


class ReconstrucaoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        ambiente = _ambiente(self.pasta.name)
        self.raiz = ambiente.__enter__()
        self.addCleanup(ambiente.__exit__, None, None, None)

    def test_simula_por_padrao_e_nao_grava_nada(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())

        relatorio = reconstruir.reconstruir()

        self.assertEqual(len(relatorio.registrados), 1)
        self.assertFalse(relatorio.aplicado)
        self.assertEqual(_linhas(), [])

    def test_aplicar_cataloga_com_os_dados_do_manifesto(self) -> None:
        arquivo = _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())
        _gravar(self.raiz, LOCAL.slug, "volume", "v.tar.gz", COPIA_BOA)

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertTrue(relatorio.limpo)
        linhas = {linha["tipo"]: linha for linha in _linhas()}
        self.assertEqual(set(linhas), {"codigo", "volume"})
        codigo = linhas["codigo"]
        self.assertEqual(codigo["situacao"], "valido")
        self.assertEqual(codigo["criado_em"], "2026-09-01T03:00:05")
        self.assertEqual(codigo["sha256"], hashlib.sha256(arquivo.read_bytes()).hexdigest())
        self.assertEqual(codigo["duracao_ms"], 1234)
        self.assertEqual(codigo["fixado"], 0)
        self.assertEqual(codigo["caminho_relativo"], f"projects/{LOCAL.slug}/codigo/a.zip")

    def test_rodar_duas_vezes_nao_duplica(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())

        reconstruir.reconstruir(aplicar=True)
        segunda = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(len(_linhas()), 1)
        self.assertEqual(segunda.registrados, [])
        self.assertEqual(len(segunda.ja_catalogados), 1)

    def test_artefato_adulterado_nao_entra(self) -> None:
        arquivo = _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())
        arquivo.write_bytes(arquivo.read_bytes() + b"x")  # mesmo manifesto, outro conteúdo

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(len(relatorio.rejeitados), 1)
        self.assertFalse(relatorio.limpo)

    def test_mesmo_tamanho_e_outro_conteudo_nao_entra(self) -> None:
        conteudo = _zip_de_codigo()
        arquivo = _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", conteudo)
        arquivo.write_bytes(bytes([conteudo[0] ^ 1]) + conteudo[1:])

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertIn("SHA-256", relatorio.rejeitados[0][1])

    def test_sem_manifesto_nao_entra(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo(), manifesto=False)

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertIn("sem manifesto", relatorio.rejeitados[0][1])

    def test_manifesto_que_nao_corresponde_ao_lugar_nao_entra(self) -> None:
        # Manifesto de outro projeto copiado para esta pasta.
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo(), projeto=OUTRO_LOCAL.slug)

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertIn("projeto", relatorio.rejeitados[0][1])

    def test_sha256_malformado_no_manifesto_nao_entra(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo(), sha256="zzz")

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(len(relatorio.rejeitados), 1)

    def test_conteudo_que_a_releitura_reprova_nao_entra_mesmo_com_sha_certo(self) -> None:
        # O SHA bate (o manifesto é do próprio arquivo), mas não é um zip de código válido.
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", b"nao sou um zip")

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertIn("releitura reprovou", relatorio.rejeitados[0][1])

    def test_dump_sem_sandbox_fica_sem_julgamento_e_nao_e_registrado(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "banco", "d.dump", b"PGDMP-qualquer")

        with patch.object(motor, "_sandbox_disponivel", return_value=False):
            relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(len(relatorio.nao_verificados), 1)
        self.assertFalse(relatorio.limpo)

    def test_dump_com_sandbox_so_entra_se_o_pg_restore_aprovar(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "banco", "bom.dump", b"PGDMP-bom")
        _gravar(self.raiz, LOCAL.slug, "banco", "ruim.dump", b"PGDMP-ruim")

        def releitura(container, caminho):
            if caminho.endswith("ruim.dump"):
                raise motor.FalhaDeBackup("dump não passou em pg_restore --list")

        with (
            patch.object(motor, "_sandbox_disponivel", return_value=True),
            patch.object(motor, "_reler_dump", side_effect=releitura),
        ):
            relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual([linha["caminho_relativo"] for linha in _linhas()],
                         [f"projects/{LOCAL.slug}/banco/bom.dump"])
        self.assertEqual(len(relatorio.rejeitados), 1)

    def test_pasta_de_projeto_desconhecido_e_ignorada(self) -> None:
        _gravar(self.raiz, "projeto_que_nao_existe", "banco", "d.dump", b"PGDMP")

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(len(relatorio.ignorados), 1)
        self.assertTrue(relatorio.limpo)  # ignorar não é falha

    def test_dump_de_seguranca_sem_manifesto_nao_e_tocado(self) -> None:
        pasta = Path(self.raiz, "projects", LOCAL.slug, "pre_restauracao")
        pasta.mkdir(parents=True)
        (pasta / "seguro.dump").write_bytes(b"PGDMP")

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(relatorio.rejeitados, [])
        self.assertTrue((pasta / "seguro.dump").exists())

    def test_pasta_que_e_link_para_fora_da_raiz_nao_e_seguida(self) -> None:
        fora = Path(self.pasta.name, "fora")
        fora.mkdir()
        (fora / "a.zip").write_bytes(_zip_de_codigo())
        (fora / "a.zip.manifest.json").write_text("{}", encoding="utf-8")
        projeto = Path(self.raiz, "projects", LOCAL.slug)
        projeto.mkdir(parents=True)
        try:
            os.symlink(fora, projeto / "codigo", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("este sistema não permite criar link simbólico sem privilégio")

        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertEqual(_linhas(), [])
        self.assertEqual(len(relatorio.rejeitados), 1)
        self.assertFalse(relatorio.limpo)

    def test_nao_apaga_nem_move_arquivo_nenhum(self) -> None:
        bom = _gravar(self.raiz, LOCAL.slug, "codigo", "bom.zip", _zip_de_codigo())
        ruim = _gravar(self.raiz, LOCAL.slug, "codigo", "ruim.zip", b"lixo", sha256="a" * 64)

        reconstruir.reconstruir(aplicar=True)

        for arquivo in (bom, ruim):
            self.assertTrue(arquivo.exists())
            self.assertTrue(Path(str(arquivo) + ".manifest.json").exists())

    def test_filtro_por_projeto(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())
        _gravar(self.raiz, OUTRO_LOCAL.slug, "codigo", "b.zip", _zip_de_codigo())

        reconstruir.reconstruir(aplicar=True, projeto_slug=OUTRO_LOCAL.slug)

        self.assertEqual({linha["projeto"] for linha in _linhas()}, {OUTRO_LOCAL.slug})

    def test_raiz_sem_projetos_nao_e_erro(self) -> None:
        relatorio = reconstruir.reconstruir(aplicar=True)

        self.assertTrue(relatorio.limpo)
        self.assertEqual(relatorio.registrados, [])

    def test_registra_um_evento_quando_catalogou(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())

        reconstruir.reconstruir(aplicar=True)

        tipos = [evento["tipo"] for evento in banco.listar_eventos()]
        self.assertIn("catalogo.reconstruido", tipos)

    def test_o_artefato_reconstruido_passa_na_verificacao_normal(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())
        reconstruir.reconstruir(aplicar=True)

        contagem = motor.verificar()

        self.assertEqual(contagem["conferidos"], 1)
        self.assertEqual(contagem["corrompidos"], 0)

    def test_comando_da_cli_devolve_zero_quando_limpo_e_um_quando_rejeita(self) -> None:
        _gravar(self.raiz, LOCAL.slug, "codigo", "a.zip", _zip_de_codigo())
        self.assertEqual(cli.comando_reconstruir_catalogo(Namespace(aplicar=True, projeto=None)), 0)

        _gravar(self.raiz, LOCAL.slug, "codigo", "ruim.zip", b"lixo", sha256="a" * 64)
        self.assertEqual(cli.comando_reconstruir_catalogo(Namespace(aplicar=True, projeto=None)), 1)


if __name__ == "__main__":
    unittest.main()
