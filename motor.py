"""O núcleo: produz os dois artefatos (banco, código) e não confia em nenhum
deles sem reler. Um terceiro artefato, `config` (zip de `.env`/`.certs`/
`.secrets`), não faz parte do escopo: segredos exigem proteção independente, e
um backup deste sistema não reconstrói sozinho um projeto do zero. Não
reintroduza esse artefato sem decisão explícita do mantenedor.

As sete regras que este módulo implementa, e por que cada uma existe:

1. Escrita atômica — tudo nasce em `temp/` e só vira nome final por `os.replace`,
   que é atômico no mesmo volume. Um dump interrompido no meio nunca pode deixar
   um arquivo truncado com cara de dump bom.
2. Verificar antes de confiar — código de saída zero não prova nada. Todo
   artefato é lido de volta (`pg_restore --list`, `testzip`) antes de ser
   aceito.
3. Nunca apagar antes de ter o substituto — a retenção roda depois de tudo
   verificado, e nunca remove o último artefato válido de um tipo.
4. Devolver o contêiner ao estado em que estava — em `finally`, inclusive quando
   o dump falha. O backup noturno não pode deixar quatro Postgres ligados, nem
   derrubar um que estava em uso.
7. SHA-256 gravado no catálogo e no manifesto, reconferível por `verificar()`.

As regras 5 e 6 (dump de segurança e confirmação digitada) são da restauração e
vivem em `restaurar.py`.

Sobre versões: `pg_dump` e `pg_restore` rodam dentro do contêiner do próprio
projeto, então a versão da ferramenta é sempre a do servidor. O host não precisa
ter PostgreSQL instalado — e não tem.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

import banco
from configuracao import (
    ConfiguracaoInvalida,
    caminho_catalogo,
    caminho_sob_raiz,
    raiz_backup,
)
from projetos import Projeto, por_slug

TEMPO_LIMITE_PADRAO = 3600  # dumps grandes; o maior hoje leva ~1 min


class FalhaDeBackup(RuntimeError):
    """Erro previsto do fluxo: mensagem já legível para o operador."""


@dataclass
class Resultado:
    tipo: str
    caminho: str
    bytes: int
    sha256: str
    duracao_ms: int


# --------------------------------------------------------------------------
# Infraestrutura
# --------------------------------------------------------------------------


def _rodar(
    comando: list[str],
    *,
    entrada_arquivo: str | None = None,
    saida_arquivo: str | None = None,
    tempo_limite: int = TEMPO_LIMITE_PADRAO,
) -> subprocess.CompletedProcess:
    """Ponto único de execução externa — o lugar para olhar quando algo do
    Docker ou do Git se comportar diferente do esperado."""
    entrada = open(entrada_arquivo, "rb") if entrada_arquivo else None
    saida = open(saida_arquivo, "wb") if saida_arquivo else subprocess.PIPE
    try:
        return subprocess.run(
            comando,
            stdin=entrada,
            stdout=saida,
            stderr=subprocess.PIPE,
            timeout=tempo_limite,
            check=False,
        )
    finally:
        if entrada:
            entrada.close()
        if saida is not subprocess.PIPE:
            saida.close()


def _erro(processo: subprocess.CompletedProcess) -> str:
    bruto = processo.stderr or b""
    texto = bruto.decode("utf-8", errors="replace").strip() if isinstance(bruto, bytes) else str(bruto)
    return texto.splitlines()[-1] if texto else f"código de saída {processo.returncode}"


def estado_container(nome: str) -> tuple[bool, bool]:
    """(existe, está rodando)."""
    processo = _rodar(["docker", "inspect", "-f", "{{.State.Running}}", nome], tempo_limite=30)
    if processo.returncode != 0:
        return (False, False)
    return (True, processo.stdout.decode().strip() == "true")


def _esperar_postgres(projeto: Projeto, tentativas: int = 40) -> None:
    for _ in range(tentativas):
        processo = _rodar(
            ["docker", "exec", projeto.container, "pg_isready", "-U", projeto.usuario, "-d", projeto.banco],
            tempo_limite=30,
        )
        if processo.returncode == 0:
            return
        time.sleep(1)
    raise FalhaDeBackup(f"{projeto.container}: PostgreSQL não ficou pronto a tempo")


def sha256_arquivo(caminho: str) -> str:
    digest = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _carimbo() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _pasta_temp() -> str:
    # No mesmo volume do destino final: é o que torna o rename atômico.
    caminho = caminho_sob_raiz("temp")
    os.makedirs(caminho, exist_ok=True)
    return caminho_sob_raiz("temp")


def _pasta_destino(projeto: Projeto, tipo: str) -> str:
    caminho = caminho_sob_raiz("projects", projeto.slug, tipo)
    os.makedirs(caminho, exist_ok=True)
    return caminho_sob_raiz("projects", projeto.slug, tipo)


def _relativo(caminho_absoluto: str) -> str:
    return os.path.relpath(caminho_absoluto, raiz_backup()).replace("\\", "/")


def caminho_artefato(caminho_relativo: str) -> str:
    """Resolve uma referência do catálogo sem permitir sair da raiz de backup."""
    try:
        return caminho_catalogo(caminho_relativo)
    except ConfiguracaoInvalida as erro:
        raise FalhaDeBackup(f"caminho inseguro no catálogo: {erro}") from erro


# --------------------------------------------------------------------------
# Produção dos artefatos (regra 1: tudo nasce em temp/)
# --------------------------------------------------------------------------


def _gerar_dump(projeto: Projeto, destino_tmp: str) -> None:
    processo = _rodar(
        [
            "docker", "exec", projeto.container,
            "pg_dump", "--format=custom", "--no-owner", "--no-acl",
            "-U", projeto.usuario, "-d", projeto.banco,
        ],
        saida_arquivo=destino_tmp,
    )
    if processo.returncode != 0:
        raise FalhaDeBackup(f"pg_dump falhou: {_erro(processo)}")


def _inventario_codigo(projeto: Projeto) -> list[dict[str, object]]:
    """Arquivos rastreados e não ignorados, usando a própria semântica do Git."""
    if not projeto.e_repo_git:
        raise FalhaDeBackup(f"{projeto.caminho} não é um repositório Git")
    processo = _rodar(
        ["git", "-C", projeto.caminho, "ls-files", "-co", "--exclude-standard", "-z"],
        tempo_limite=600,
    )
    if processo.returncode != 0:
        raise FalhaDeBackup(f"não foi possível ler o inventário Git: {_erro(processo)}")

    raiz = os.path.abspath(projeto.caminho)
    inventario: list[dict[str, object]] = []
    for bruto in processo.stdout.split(b"\0"):
        if not bruto:
            continue
        relativo = os.fsdecode(bruto).replace("\\", "/")
        if not relativo or relativo.startswith("/") or ".." in PurePosixPath(relativo).parts:
            raise FalhaDeBackup(f"caminho inválido no inventário Git: {relativo!r}")
        origem = os.path.abspath(os.path.join(raiz, relativo))
        if os.path.commonpath((raiz, origem)) != raiz:
            raise FalhaDeBackup(f"arquivo fora do projeto recusado: {relativo}")
        if os.path.islink(origem):
            raise FalhaDeBackup(f"link simbólico não é aceito no backup de código: {relativo}")
        if not os.path.isfile(origem):
            raise FalhaDeBackup(f"arquivo desapareceu durante o backup: {relativo}")
        inventario.append({"caminho": relativo, "bytes": os.path.getsize(origem),
                           "sha256": sha256_arquivo(origem)})
    if not inventario:
        raise FalhaDeBackup("nenhum arquivo elegível foi encontrado pelo Git")
    return inventario


def _gerar_zip_codigo(projeto: Projeto, destino_tmp: str) -> dict[str, object]:
    inventario = _inventario_codigo(projeto)
    estado = _estado_git(projeto)
    with zipfile.ZipFile(destino_tmp, "w", zipfile.ZIP_DEFLATED) as pacote:
        for item in inventario:
            relativo = str(item["caminho"])
            origem = os.path.join(projeto.caminho, relativo)
            pacote.write(origem, relativo)
            if sha256_arquivo(origem) != item["sha256"]:
                raise FalhaDeBackup(f"arquivo mudou durante o empacotamento: {relativo}")
        pacote.writestr(
            "backuprestore-manifest.json",
            json.dumps({"versao": 1, "projeto": projeto.slug, "tipo": "codigo",
                        "git": estado, "arquivos": inventario}, ensure_ascii=False, indent=2),
        )
    return {"git": estado, "arquivos": inventario}


def _estado_git(projeto: Projeto) -> dict:
    """O bundle carrega o que está commitado. Trabalho não commitado fica de
    fora — registrar isso no manifesto evita a descoberta tardia."""
    processo = _rodar(["git", "-C", projeto.caminho, "status", "--porcelain"], tempo_limite=120)
    linhas = [l for l in processo.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    cabeca = _rodar(["git", "-C", projeto.caminho, "rev-parse", "HEAD"], tempo_limite=60)
    return {
        "head": cabeca.stdout.decode().strip() if cabeca.returncode == 0 else None,
        "arquivos_nao_commitados": len(linhas),
        "arvore_suja": bool(linhas),
    }


# --------------------------------------------------------------------------
# Verificação (regra 2: reler antes de aceitar)
# --------------------------------------------------------------------------


def verificar_dump(projeto: Projeto, caminho: str) -> None:
    if os.path.getsize(caminho) == 0:
        raise FalhaDeBackup("dump vazio")
    processo = _rodar(
        ["docker", "exec", "-i", projeto.container, "pg_restore", "--list"],
        entrada_arquivo=caminho,
    )
    if processo.returncode != 0:
        raise FalhaDeBackup(f"dump não passou em pg_restore --list: {_erro(processo)}")


# --------------------------------------------------------------------------
# Retenção (regra 3)
# --------------------------------------------------------------------------


def verificar_zip_codigo(caminho: str) -> None:
    try:
        with zipfile.ZipFile(caminho) as pacote:
            if pacote.testzip() is not None:
                raise FalhaDeBackup("zip de código corrompido")
            nomes = pacote.namelist()
            if "backuprestore-manifest.json" not in nomes:
                raise FalhaDeBackup("manifesto interno ausente no zip de código")
            for nome in nomes:
                caminho_zip = PurePosixPath(nome)
                if caminho_zip.is_absolute() or ".." in caminho_zip.parts:
                    raise FalhaDeBackup(f"caminho inseguro no zip: {nome}")
            json.loads(pacote.read("backuprestore-manifest.json"))
    except (zipfile.BadZipFile, json.JSONDecodeError) as erro:
        raise FalhaDeBackup(f"zip de código ilegível: {erro}") from erro


def aplicar_retencao(projeto: Projeto, tipo: str) -> int:
    """Roda só depois que o artefato novo já foi verificado e registrado."""
    validos = banco.artefatos_validos(projeto.slug, tipo)
    if len(validos) <= projeto.retencao:
        return 0

    excedentes = [linha for linha in validos[projeto.retencao :] if not linha["fixado"]]
    # Cinto de segurança: nunca deixar o tipo sem nenhum artefato válido.
    if len(validos) - len(excedentes) < 1:
        return 0

    removidos = 0
    for linha in excedentes:
        try:
            caminho = caminho_artefato(linha["caminho_relativo"])
        except FalhaDeBackup:
            banco.marcar_situacao_artefato(linha["id"], "corrompido")
            continue
        for alvo in (caminho, caminho + ".manifest.json"):
            if os.path.exists(alvo):
                os.remove(alvo)
        banco.marcar_situacao_artefato(linha["id"], "removido")
        removidos += 1
    return removidos


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------


def _promover(tmp: str, final: str, manifesto: dict) -> None:
    """Regra 1: o arquivo só ganha o nome definitivo depois de verificado."""
    os.replace(tmp, final)
    with open(final + ".manifest.json", "w", encoding="utf-8") as arquivo:
        json.dump(manifesto, arquivo, indent=2, ensure_ascii=False)


def _produzir(projeto: Projeto, tipo: str, execucao_id: int | None) -> Resultado:
    inicio = time.monotonic()
    carimbo = _carimbo()
    extensao = {"banco": "dump", "codigo": "zip"}[tipo]
    nome = f"{projeto.slug}_{tipo}_{carimbo}.{extensao}"
    _pasta_temp()
    _pasta_destino(projeto, tipo)
    tmp = caminho_sob_raiz("temp", nome + ".tmp")
    final = caminho_sob_raiz("projects", projeto.slug, tipo, nome)

    extra: dict = {}
    try:
        if tipo == "banco":
            _gerar_dump(projeto, tmp)
            verificar_dump(projeto, tmp)
        elif tipo == "codigo":
            extra = _gerar_zip_codigo(projeto, tmp)
            verificar_zip_codigo(tmp)

        tamanho = os.path.getsize(tmp)
        digest = sha256_arquivo(tmp)
        duracao = int((time.monotonic() - inicio) * 1000)

        _promover(
            tmp,
            final,
            {
                "projeto": projeto.slug,
                "tipo": tipo,
                "arquivo": nome,
                "criado_em": datetime.datetime.now().isoformat(timespec="seconds"),
                "bytes": tamanho,
                "sha256": digest,
                "duracao_ms": duracao,
                "origem": {"container": projeto.container, "banco": projeto.banco}
                if tipo == "banco"
                else {"repositorio": projeto.caminho},
                **extra,
            },
        )
        return Resultado(tipo, final, tamanho, digest, duracao)
    finally:
        # Sobra de tentativa falha não fica ocupando espaço nem confundindo.
        if os.path.exists(tmp):
            os.remove(tmp)


def fazer_backup(
    projeto: Projeto,
    tipos: tuple[str, ...] | None = None,
    execucao_id: int | None = None,
) -> list[Resultado]:
    """Backup completo de um projeto. Devolve o contêiner ao estado original
    aconteça o que acontecer."""
    tipos = tipos or projeto.tipos
    desconhecidos = set(tipos) - {"banco", "codigo"}
    if desconhecidos:
        raise FalhaDeBackup(
            f"tipo(s) de backup inválido(s): {', '.join(sorted(desconhecidos))}"
        )
    proprio = execucao_id is None
    if proprio:
        execucao_id = banco.abrir_execucao(projeto.slug, "backup")

    if projeto.ambiente != "local":
        # Produção por contêiner só faz sentido para o Docker deste host. Um
        # projeto de outro ambiente (hoje, "vps") tem o mesmo nome de
        # contêiner do projeto local por coincidência (ver projetos.py) — sem
        # esta trava, isto dispararia pg_dump no contêiner local e gravaria o
        # resultado no catálogo com o rótulo errado. Os artefatos desse
        # ambiente chegam por outro caminho (sincronização com o servidor).
        # Fecha a execução como as demais travas
        # pré-`try` desta função — senão ela fica presa em "fila" para sempre.
        erro = (
            f"{projeto.slug}: ambiente {projeto.ambiente!r} não produz backup por "
            "contêiner — use a sincronização própria desse ambiente"
        )
        banco.registrar_evento("backup.falha", erro, projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        banco.fechar_execucao(execucao_id, "falha", erro)
        raise FalhaDeBackup(erro)

    precisa_banco = "banco" in tipos
    existe, estava_rodando = estado_container(projeto.container)
    if precisa_banco and not existe:
        erro = f"contêiner {projeto.container} não existe"
        banco.registrar_evento("backup.falha", erro, projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        banco.fechar_execucao(execucao_id, "falha", erro)
        raise FalhaDeBackup(erro)

    resultados: list[Resultado] = []
    try:
        if precisa_banco and not estava_rodando:
            banco.marcar_fase(execucao_id, "Iniciando contêiner", 5)
            processo = _rodar(["docker", "start", projeto.container], tempo_limite=120)
            if processo.returncode != 0:
                raise FalhaDeBackup(f"não foi possível iniciar {projeto.container}: {_erro(processo)}")
            _esperar_postgres(projeto)

        total = len(tipos)
        for indice, tipo in enumerate(tipos):
            rotulo = {"banco": "Dump do banco", "codigo": "ZIP do código"}[tipo]
            banco.marcar_fase(execucao_id, rotulo, int(10 + (indice / total) * 80))
            resultado = _produzir(projeto, tipo, execucao_id)
            banco.registrar_artefato(
                projeto=projeto.slug,
                tipo=tipo,
                caminho_relativo=_relativo(resultado.caminho),
                bytes_=resultado.bytes,
                sha256=resultado.sha256,
                duracao_ms=resultado.duracao_ms,
                execucao_id=execucao_id,
            )
            resultados.append(resultado)

        # Regra 3: só agora, com os novos artefatos verificados e no catálogo.
        banco.marcar_fase(execucao_id, "Aplicando retenção", 95)
        for tipo in tipos:
            removidos = aplicar_retencao(projeto, tipo)
            if removidos:
                banco.registrar_evento(
                    "retencao", f"{removidos} artefato(s) de {tipo} removido(s)",
                    projeto=projeto.slug, execucao_id=execucao_id,
                )

        banco.fechar_execucao(execucao_id, "sucesso")
        banco.registrar_evento(
            "backup.sucesso",
            f"{len(resultados)} artefato(s): " + ", ".join(r.tipo for r in resultados),
            projeto=projeto.slug, execucao_id=execucao_id,
        )
        return resultados

    except Exception as erro:
        banco.fechar_execucao(execucao_id, "falha", str(erro))
        banco.registrar_evento("backup.falha", str(erro), projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        raise
    finally:
        # Regra 4.
        if precisa_banco and existe and not estava_rodando:
            _rodar(["docker", "stop", projeto.container], tempo_limite=120)


def verificar(projeto_slug: str | None = None) -> dict[str, int]:
    """Regra 7: relê os arquivos e confere contra o SHA-256 do catálogo.
    É o que detecta corrupção silenciosa no destino."""
    contagem = {"conferidos": 0, "ausentes": 0, "corrompidos": 0}
    for linha in banco.listar_artefatos(projeto_slug, limite=10000):
        if linha["situacao"] not in ("valido", "corrompido", "ausente"):
            continue
        try:
            caminho = caminho_artefato(linha["caminho_relativo"])
        except FalhaDeBackup:
            banco.marcar_situacao_artefato(linha["id"], "corrompido")
            contagem["corrompidos"] += 1
            continue
        if not os.path.exists(caminho):
            banco.marcar_situacao_artefato(linha["id"], "ausente")
            contagem["ausentes"] += 1
            continue
        if sha256_arquivo(caminho) != linha["sha256"]:
            banco.marcar_situacao_artefato(linha["id"], "corrompido")
            contagem["corrompidos"] += 1
            continue
        try:
            projeto = por_slug(linha["projeto"])
            if linha["tipo"] == "banco":
                verificar_dump(projeto, caminho)
            elif linha["tipo"] == "codigo":
                verificar_zip_codigo(caminho)
        except (FalhaDeBackup, KeyError):
            banco.marcar_situacao_artefato(linha["id"], "corrompido")
            contagem["corrompidos"] += 1
            continue
        banco.marcar_situacao_artefato(linha["id"], "valido")
        contagem["conferidos"] += 1
    return contagem


def espaco_livre() -> int:
    return shutil.disk_usage(raiz_backup()).free
