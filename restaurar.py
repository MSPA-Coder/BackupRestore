"""Restauração — a única operação destrutiva do sistema.

Três travas, nesta ordem, antes de qualquer escrita:

- **Destino proibido.** Os contêineres dos projetos reais estão em
  `CONTAINERS_PROTEGIDOS` e são recusados aqui. Não existe flag que libere: o
  erro caro não é escolher o arquivo errado, é acertar o arquivo e errar o
  destino. Restaurar sobre um projeto real é trabalho manual, consciente, com o
  procedimento do RESTAURAR.md na tela.
- **Confirmação digitada (regra 6).** O nome do banco de destino, digitado à
  mão. Sem "tem certeza? [Sim]" — a fricção é o recurso de segurança.
- **Dump de segurança (regra 5).** Antes de tocar no destino, dump do estado
  atual, verificado como qualquer outro artefato. Sem opção de pular.

Só depois disso o banco de destino é recriado e o dump aplicado.
"""

from __future__ import annotations

import datetime
import os
import time

import banco
import motor
from configuracao import caminho_sob_raiz
from projetos import CONTAINERS_PROTEGIDOS, Projeto, por_slug


class RestauracaoRecusada(RuntimeError):
    """Uma das travas barrou a operação. Nada foi escrito."""


def _literal_sql(valor: str) -> str:
    """Representa texto como literal PostgreSQL sem permitir injeção."""
    if "\x00" in valor:
        raise RestauracaoRecusada("nome de banco contém caractere nulo")
    return "'" + valor.replace("'", "''") + "'"


def _identificador_sql(valor: str) -> str:
    """Representa banco/identificador PostgreSQL preservando nomes válidos."""
    if not valor or "\x00" in valor:
        raise RestauracaoRecusada("nome de banco vazio ou inválido")
    return '"' + valor.replace('"', '""') + '"'


def _psql(container: str, usuario: str, base: str, sql: str) -> str:
    processo = motor._rodar(
        ["docker", "exec", container, "psql", "-U", usuario, "-d", base, "-tAc", sql]
    )
    if processo.returncode != 0:
        raise RuntimeError(f"psql falhou: {motor._erro(processo)}")
    return processo.stdout.decode("utf-8", "replace").strip()


def banco_existe(container: str, usuario: str, nome: str) -> bool:
    saida = _psql(container, usuario, "postgres",
                  f"SELECT 1 FROM pg_database WHERE datname = {_literal_sql(nome)}")
    return saida == "1"


def resumo_banco(container: str, usuario: str, nome: str) -> dict[str, int]:
    """Contagem de linhas por tabela — é assim que se prova que a restauração
    trouxe os dados, e não só criou o esquema."""
    sql = (
        "SELECT relname || '=' || n_live_tup FROM pg_stat_user_tables "
        "ORDER BY relname"
    )
    _psql(container, usuario, nome, "ANALYZE")
    saida = _psql(container, usuario, nome, sql)
    resumo: dict[str, int] = {}
    for linha in saida.splitlines():
        if "=" in linha:
            tabela, _, valor = linha.partition("=")
            resumo[tabela.strip()] = int(valor)
    return resumo


def _dump_de_seguranca(
    container: str, usuario: str, base: str, slug_projeto: str, execucao_id: int | None
) -> str | None:
    """Regra 5. Devolve o caminho, ou None se não havia o que preservar."""
    if not banco_existe(container, usuario, base):
        return None

    pasta = caminho_sob_raiz("projects", slug_projeto, "pre_restauracao")
    os.makedirs(pasta, exist_ok=True)
    pasta = caminho_sob_raiz("projects", slug_projeto, "pre_restauracao")
    carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"{slug_projeto}_seguranca_{carimbo}.dump"
    motor._pasta_temp()
    tmp = caminho_sob_raiz("temp", nome + ".tmp")
    final = caminho_sob_raiz("projects", slug_projeto, "pre_restauracao", nome)

    inicio = time.monotonic()
    processo = motor._rodar(
        ["docker", "exec", container, "pg_dump", "--format=custom",
         "--no-owner", "--no-acl", "-U", usuario, "-d", base],
        saida_arquivo=tmp,
    )
    if processo.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RestauracaoRecusada(
            f"dump de segurança falhou, restauração abortada: {motor._erro(processo)}"
        )

    # Verificado como qualquer outro artefato: um seguro não conferido não é seguro.
    processo = motor._rodar(
        ["docker", "exec", "-i", container, "pg_restore", "--list"], entrada_arquivo=tmp
    )
    if processo.returncode != 0:
        os.remove(tmp)
        raise RestauracaoRecusada(
            "dump de segurança não passou na verificação, restauração abortada"
        )

    digest = motor.sha256_arquivo(tmp)
    tamanho = os.path.getsize(tmp)
    os.replace(tmp, final)
    banco.registrar_artefato(
        projeto=slug_projeto,
        tipo="banco",
        caminho_relativo=motor._relativo(final),
        bytes_=tamanho,
        sha256=digest,
        duracao_ms=int((time.monotonic() - inicio) * 1000),
        execucao_id=execucao_id,
        finalidade="pre_restauracao",
    )
    return final


def restaurar(
    artefato_id: int,
    *,
    container_destino: str,
    banco_destino: str,
    usuario_destino: str,
    confirmacao: str,
) -> dict:
    """Restaura um dump do catálogo num destino que não seja um projeto real."""

    # Trava 1 — destino proibido. Antes de tudo, inclusive de ler o artefato.
    # A restauração só fala com o Docker local, nunca com o VPS por SSH — daqui
    # o ambiente do destino é sempre "local". Comparar por par, não só pelo
    # nome, é o que faz a trava valer por desenho: os contêineres do VPS usam
    # os mesmos nomes dos daqui, e um conjunto só de nomes os protegeria por
    # coincidência.
    if ("local", container_destino) in CONTAINERS_PROTEGIDOS:
        raise RestauracaoRecusada(
            f"{container_destino} é o contêiner de um projeto real e não é destino "
            "aceito. Use o sandbox (compose.teste.yaml) ou siga o RESTAURAR.md à mão."
        )

    # Trava 2 — confirmação digitada.
    if confirmacao.strip() != banco_destino:
        raise RestauracaoRecusada(
            f"confirmação não confere: digite exatamente {banco_destino!r}"
        )

    artefato = banco.obter_artefato(artefato_id)
    if artefato is None:
        raise RestauracaoRecusada(f"artefato {artefato_id} não existe no catálogo")
    if artefato["tipo"] != "banco":
        raise RestauracaoRecusada(f"artefato {artefato_id} é do tipo {artefato['tipo']}")

    try:
        origem = motor.caminho_artefato(artefato["caminho_relativo"])
    except motor.FalhaDeBackup as erro:
        banco.marcar_situacao_artefato(artefato_id, "corrompido")
        raise RestauracaoRecusada("artefato tem caminho inseguro no catálogo") from erro
    if not os.path.exists(origem):
        banco.marcar_situacao_artefato(artefato_id, "ausente")
        raise RestauracaoRecusada(f"arquivo não encontrado: {origem}")

    # Reconferir o hash agora, não confiar no que o catálogo diz que era.
    if motor.sha256_arquivo(origem) != artefato["sha256"]:
        banco.marcar_situacao_artefato(artefato_id, "corrompido")
        raise RestauracaoRecusada(
            "SHA-256 não confere com o catálogo — artefato marcado como corrompido"
        )

    execucao_id = banco.abrir_execucao(artefato["projeto"], "restauracao")
    try:
        banco.marcar_fase(execucao_id, "Dump de segurança do destino", 15)
        seguranca = _dump_de_seguranca(
            container_destino, usuario_destino, banco_destino,
            artefato["projeto"], execucao_id,
        )
        banco.registrar_evento(
            "restauracao.seguranca",
            f"dump de segurança: {os.path.basename(seguranca)}" if seguranca
            else "destino não existia; nada a preservar",
            projeto=artefato["projeto"], execucao_id=execucao_id,
        )

        banco.marcar_fase(execucao_id, "Recriando banco de destino", 40)
        banco_literal = _literal_sql(banco_destino)
        banco_identificador = _identificador_sql(banco_destino)
        _psql(container_destino, usuario_destino, "postgres",
              f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
              f"WHERE datname = {banco_literal} AND pid <> pg_backend_pid()")
        _psql(container_destino, usuario_destino, "postgres",
              f"DROP DATABASE IF EXISTS {banco_identificador}")
        _psql(container_destino, usuario_destino, "postgres",
              f"CREATE DATABASE {banco_identificador}")

        banco.marcar_fase(execucao_id, "Aplicando pg_restore", 65)
        processo = motor._rodar(
            ["docker", "exec", "-i", container_destino, "pg_restore",
             "--no-owner", "--no-acl", "--exit-on-error",
             "-U", usuario_destino, "-d", banco_destino],
            entrada_arquivo=origem,
        )
        if processo.returncode != 0:
            raise RuntimeError(f"pg_restore falhou: {motor._erro(processo)}")

        banco.marcar_fase(execucao_id, "Conferindo destino", 90)
        conteudo = resumo_banco(container_destino, usuario_destino, banco_destino)

        banco.fechar_execucao(execucao_id, "sucesso")
        banco.registrar_evento(
            "restauracao.sucesso",
            f"{artefato['caminho_relativo']} restaurado em "
            f"{container_destino}/{banco_destino}: {len(conteudo)} tabela(s) com dados",
            projeto=artefato["projeto"], execucao_id=execucao_id,
        )
        return {
            "artefato": dict(artefato),
            "dump_de_seguranca": seguranca,
            "destino": f"{container_destino}/{banco_destino}",
            "tabelas": conteudo,
        }

    except Exception as erro:
        banco.fechar_execucao(execucao_id, "falha", str(erro))
        banco.registrar_evento("restauracao.falha", str(erro),
                               projeto=artefato["projeto"], execucao_id=execucao_id,
                               severidade="erro")
        raise


def comparar_com_origem(projeto: Projeto, container_destino: str, usuario_destino: str,
                        banco_destino: str) -> dict:
    """O ensaio: as mesmas tabelas, com as mesmas contagens, dos dois lados."""
    if projeto.ambiente != "local":
        # `projeto.container` é só um nome — para um projeto "vps" ele colide
        # de propósito com o contêiner local do mesmo nome (ver projetos.py).
        # Ler a "origem" por aqui compararia com o banco local errado. A
        # origem VPS ainda não tem um caminho de leitura (Fase 6 do plano).
        raise RuntimeError(
            f"{projeto.slug}: comparação com a origem via contêiner local não vale "
            f"para ambiente={projeto.ambiente!r} — o nome do contêiner colide com o "
            "do projeto local"
        )
    existe, rodando = motor.estado_container(projeto.container)
    if not existe:
        raise RuntimeError(f"contêiner de origem {projeto.container} não existe")
    try:
        if not rodando:
            motor._rodar(["docker", "start", projeto.container], tempo_limite=120)
            motor._esperar_postgres(projeto)
        origem = resumo_banco(projeto.container, projeto.usuario, projeto.banco)
    finally:
        if not rodando:
            motor._rodar(["docker", "stop", projeto.container], tempo_limite=120)

    destino = resumo_banco(container_destino, usuario_destino, banco_destino)
    tabelas = sorted(set(origem) | set(destino))
    divergencias = {
        tabela: (origem.get(tabela), destino.get(tabela))
        for tabela in tabelas
        if origem.get(tabela) != destino.get(tabela)
    }
    return {
        "tabelas_origem": len(origem),
        "tabelas_destino": len(destino),
        "linhas_origem": sum(origem.values()),
        "linhas_destino": sum(destino.values()),
        "divergencias": divergencias,
        # O dump é uma fotografia. Em sistemas ativos, a origem pode mudar
        # entre a captura e o ensaio; divergência contemporânea é diagnóstico,
        # não prova de falha do artefato que acabou de restaurar.
        "confere": not divergencias,
        "restauracao_valida": bool(destino),
    }
