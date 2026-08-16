"""Configuração local mínima do BackupRestore."""

from __future__ import annotations

import json
import os
import tempfile


PASTA_APLICACAO = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_CONFIGURACAO = os.path.join(PASTA_APLICACAO, "configuracao.local.json")
RAIZ_PADRAO = r"C:\Users\MSPA\Dropbox\BackpsDB"


class ConfiguracaoInvalida(ValueError):
    pass


def _normalizar(caminho: str) -> str:
    texto = os.path.expanduser(os.path.expandvars(caminho.strip()))
    if not texto:
        raise ConfiguracaoInvalida("informe uma pasta para os backups")
    return os.path.normpath(os.path.abspath(texto))


def _ler() -> dict[str, str]:
    if not os.path.exists(ARQUIVO_CONFIGURACAO):
        return {}
    try:
        with open(ARQUIVO_CONFIGURACAO, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError) as erro:
        raise ConfiguracaoInvalida("o arquivo de configuração local é inválido") from erro
    if not isinstance(dados, dict):
        raise ConfiguracaoInvalida("o arquivo de configuração local é inválido")
    return dados


def raiz_backup() -> str:
    dados = _ler()
    valor = dados.get("raiz_backup") or os.environ.get("BACKUPRESTORE_RAIZ_BACKUP") or RAIZ_PADRAO
    return _normalizar(str(valor))


def validar_raiz(caminho: str) -> str:
    raiz = _normalizar(caminho)
    os.makedirs(raiz, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix=".backuprestore-", dir=raiz, delete=False) as teste:
            teste.write(b"ok")
            teste_path = teste.name
        os.replace(teste_path, teste_path + ".check")
        os.remove(teste_path + ".check")
    except OSError as erro:
        raise ConfiguracaoInvalida(f"não foi possível gravar em {raiz}") from erro
    return raiz


def salvar_raiz_backup(caminho: str) -> str:
    raiz = validar_raiz(caminho)
    temporario = ARQUIVO_CONFIGURACAO + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump({"raiz_backup": raiz}, arquivo, indent=2, ensure_ascii=False)
    os.replace(temporario, ARQUIVO_CONFIGURACAO)
    return raiz
