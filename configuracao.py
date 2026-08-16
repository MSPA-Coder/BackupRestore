"""Configuração local mínima do BackupRestore."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


PASTA_APLICACAO = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_CONFIGURACAO = os.path.join(PASTA_APLICACAO, "configuracao.local.json")
RAIZ_PADRAO = r"C:\Users\MSPA\Dropbox\BackpsDB"
VARIAVEL_RAIZ_PERMITIDA = "BACKUPRESTORE_RAIZ_PERMITIDA"


class ConfiguracaoInvalida(ValueError):
    pass


def _normalizar(caminho: str) -> str:
    texto = os.path.expanduser(os.path.expandvars(caminho.strip()))
    if not texto:
        raise ConfiguracaoInvalida("informe uma pasta para os backups")
    return str(Path(texto).resolve(strict=False))


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


def _dentro_da_raiz(caminho: str, raiz: str) -> str:
    """Normaliza e exige que ``caminho`` permaneça sob ``raiz``.

    ``Path.resolve`` é intencional: ``normpath`` não enxerga um link simbólico
    que sai da árvore. A nova resolução depois de criar a pasta, em
    :func:`validar_raiz`, também detecta um link já existente no destino.
    """
    destino = Path(caminho).resolve(strict=False)
    limite = Path(raiz).resolve(strict=False)
    try:
        comum = os.path.commonpath((str(limite), str(destino)))
        if os.path.normcase(comum) != os.path.normcase(str(limite)):
            raise ValueError
        destino.relative_to(limite)
    except ValueError as erro:
        raise ConfiguracaoInvalida("o caminho precisa ficar dentro da raiz permitida") from erro
    return str(destino)


def raiz_permitida() -> str:
    """Limite definido fora da interface HTTP por um operador confiável.

    A variável de ambiente é útil para tarefas agendadas e impõe o limite
    mesmo se o arquivo local contiver uma raiz mais ampla. Em instalações
    legadas, a raiz já gravada vira o próprio limite até que o operador execute
    ``cli.py configurar-raiz``; isso mantém os artefatos existentes acessíveis.
    """
    dados = _ler()
    valor = (
        os.environ.get(VARIAVEL_RAIZ_PERMITIDA)
        or dados.get("raiz_permitida")
        or dados.get("raiz_backup")
        or RAIZ_PADRAO
    )
    return _normalizar(str(valor))


def raiz_backup() -> str:
    dados = _ler()
    valor = dados.get("raiz_backup") or os.environ.get("BACKUPRESTORE_RAIZ_BACKUP") or RAIZ_PADRAO
    return _dentro_da_raiz(_normalizar(str(valor)), raiz_permitida())


def validar_raiz(caminho: str, *, permitida: str | None = None) -> str:
    raiz = _dentro_da_raiz(_normalizar(caminho), _normalizar(permitida) if permitida else raiz_permitida())
    os.makedirs(raiz, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix=".backuprestore-", dir=raiz, delete=False) as teste:
            teste.write(b"ok")
            teste_path = teste.name
        os.replace(teste_path, teste_path + ".check")
        os.remove(teste_path + ".check")
    except OSError as erro:
        raise ConfiguracaoInvalida(f"não foi possível gravar em {raiz}") from erro
    return _dentro_da_raiz(raiz, _normalizar(permitida) if permitida else raiz_permitida())


def configurar_raiz_backup(caminho: str, *, permitida: str | None = None) -> str:
    """Persiste a raiz somente pelo comando local do operador.

    Sem ``permitida``, a própria raiz escolhida é o limite. A variável
    ``BACKUPRESTORE_RAIZ_PERMITIDA`` prevalece sobre o limite persistido.
    """
    limite = _normalizar(os.environ.get(VARIAVEL_RAIZ_PERMITIDA) or permitida or caminho)
    raiz = validar_raiz(caminho, permitida=limite)
    dados = _ler()
    dados["raiz_backup"] = raiz
    if not os.environ.get(VARIAVEL_RAIZ_PERMITIDA):
        dados["raiz_permitida"] = limite
    temporario = ARQUIVO_CONFIGURACAO + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=2, ensure_ascii=False)
    os.replace(temporario, ARQUIVO_CONFIGURACAO)
    return raiz


def caminho_catalogo(caminho_relativo: str) -> str:
    """Converte um caminho POSIX do catálogo em arquivo contido na raiz."""
    if not isinstance(caminho_relativo, str) or not caminho_relativo:
        raise ConfiguracaoInvalida("caminho relativo do catálogo ausente")
    posix = PurePosixPath(caminho_relativo.replace("\\", "/"))
    windows = PureWindowsPath(caminho_relativo)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or not posix.parts
    ):
        raise ConfiguracaoInvalida("caminho relativo do catálogo inválido")
    raiz = raiz_backup()
    return _dentro_da_raiz(str(Path(raiz, *posix.parts)), raiz)


def caminho_sob_raiz(*partes: str) -> str:
    """Monta um destino interno e recusa links que escapem da raiz."""
    raiz = raiz_backup()
    return _dentro_da_raiz(str(Path(raiz, *partes)), raiz)
