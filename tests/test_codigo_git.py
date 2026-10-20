"""Produção de ZIP a partir de um repositório Git descartável real."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import banco
import configuracao
import motor
from projetos import Projeto


class CodigoGitTests(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory()
        self.addCleanup(temporario.cleanup)
        self.pasta = Path(temporario.name)
        self.repo = self.pasta / "repo"
        self.repo.mkdir()
        self.raiz = self.pasta / "backups"
        for controle in (
            patch.object(configuracao, "ARQUIVO_CONFIGURACAO", str(self.pasta / "config.json")),
            patch.object(banco, "CAMINHO_CATALOGO", str(self.pasta / "catalogo.sqlite3")),
            patch.dict(os.environ, {"BACKUPRESTORE_RAIZ_BACKUP": str(self.raiz),
                                    "BACKUPRESTORE_RAIZ_PERMITIDA": str(self.raiz)}),
        ):
            controle.start()
            self.addCleanup(controle.stop)
        banco.criar_tabelas()
        self.git("init", "-q")
        self.git("config", "user.name", "QA sintético")
        self.git("config", "user.email", "qa@example.invalid")
        (self.repo / ".gitignore").write_text(".secrets/\n.cache/\n", encoding="utf-8")
        (self.repo / "app.py").write_text("valor = 'inicial'\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Base sintética")
        self.projeto = Projeto("qa_git", "QA", str(self.repo), "inexistente", "qa", "qa", tipos=("codigo",))
        executar_real = subprocess.run

        def executar(comando, **opcoes):
            if comando[0] != "docker":
                return executar_real(comando, **opcoes)
            self.assertEqual(comando[1], "inspect")
            return subprocess.CompletedProcess(comando, 0, b"true", b"")

        controle = patch("subprocess.run", side_effect=executar)
        controle.start()
        self.addCleanup(controle.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def test_zip_restaura_trabalho_atual_sem_arquivos_ignorados(self):
        (self.repo / "app.py").write_text("valor = 'alteração local'\n", encoding="utf-8")
        (self.repo / "novo.py").write_text("novo = True\n", encoding="utf-8")
        (self.repo / ".secrets").mkdir()
        (self.repo / ".secrets" / "qa.txt").write_text("segredo sintético que não deve ser copiado")
        resultado = motor.fazer_backup(self.projeto)[0]
        with zipfile.ZipFile(resultado.caminho) as pacote:
            nomes = set(pacote.namelist())
            self.assertEqual(nomes, {".gitignore", "app.py", "novo.py", "backuprestore-manifest.json"})
            manifesto = json.loads(pacote.read("backuprestore-manifest.json"))
            self.assertTrue(manifesto["git"]["arvore_suja"])
            self.assertEqual(manifesto["git"]["head"], self.git("rev-parse", "HEAD").decode().strip())
            for arquivo in manifesto["arquivos"]:
                conteudo = pacote.read(arquivo["caminho"])
                self.assertEqual(hashlib.sha256(conteudo).hexdigest(), arquivo["sha256"])
                self.assertEqual(len(conteudo), arquivo["bytes"])
        self.assertEqual(banco.listar_artefatos()[0]["situacao"], "valido")

    def test_link_simbolico_para_fora_recusa_zip_inteiro(self):
        externo = self.pasta / "fora.txt"
        externo.write_text("Fora do repositório")
        (self.repo / "atalho").symlink_to(externo)
        with self.assertRaises(motor.FalhaDeBackup):
            motor.fazer_backup(self.projeto)
        self.assertEqual(banco.listar_artefatos(), [])
        self.assertEqual(list(self.raiz.rglob("*.zip")), [])
        self.assertEqual(list(self.raiz.rglob("*.tmp")), [])
