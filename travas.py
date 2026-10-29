"""Exclusão entre processos para o acervo e os contêineres de origem."""

from __future__ import annotations

import errno
import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import banco

_guarda = threading.Lock()
_travas: dict[str, threading.RLock] = {}
_estado = threading.local()


@contextmanager
def _exclusivo(recurso: str):
    """Espera o ciclo anterior terminar; o sistema libera a trava após crash."""
    chave = hashlib.sha256(recurso.encode("utf-8")).hexdigest()
    pasta = Path(banco.CAMINHO_CATALOGO).resolve().parent / ".backuprestore-locks"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = str(pasta / f"{chave}.lock")
    with _guarda:
        trava = _travas.setdefault(caminho, threading.RLock())
    with trava:
        ativos = getattr(_estado, "ativos", None)
        if ativos is None:
            ativos = _estado.ativos = set()
        if caminho in ativos:
            yield
            return
        with open(caminho, "a+b") as arquivo:
            with _trava_arquivo(arquivo):
                ativos.add(caminho)
                try:
                    yield
                finally:
                    ativos.remove(caminho)


@contextmanager
def _trava_arquivo(arquivo):
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                arquivo.seek(0, os.SEEK_END)
                if arquivo.tell() == 0:
                    arquivo.write(b"\0")
                    arquivo.flush()
                arquivo.seek(0)
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as erro:
                if erro.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                time.sleep(0.1)
        try:
            yield
        finally:
            arquivo.seek(0)
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)


def container_exclusivo(container: str):
    return _exclusivo("container:" + container)


def catalogo_exclusivo():
    return _exclusivo("catalogo")
