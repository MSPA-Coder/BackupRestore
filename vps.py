"""Camada 2: busca, verificação e catalogação de dumps do VPS.

O BackupRestore não fala mais com a produção diretamente — não dispara
`pg_dump`, não consulta nem liga contêiner de projeto real. Fala só com o
agente restrito do servidor (`_manutencao/vps/backup-agent.sh`, instalado com
uma chave SSH dedicada travada por `command=`), que só sabe quatro verbos:
`listar`, `enviar`, `apagar`, `estado`. Ver PLANO_BACKUPRESTORE_VPS.md, seção 5.

O ciclo, por projeto, para cada dump que existe lá e ainda não está aqui:

1. buscar o arquivo (`enviar`);
2. conferir o SHA-256 contra o que `listar` informou;
3. reler no **sandbox** local com `pg_restore --list` — não no contêiner do
   projeto: a origem é outra máquina, o sandbox é o destino de leitura neutro
   que este projeto já usa para todo ensaio;
4. registrar no catálogo, com o carimbo de tempo do **servidor**, não da hora
   do download — é o que faz a retenção continuar correta depois de um PC que
   ficou dias fora buscar vários de uma vez;
5. só então pedir a remoção lá (`apagar`) — o servidor decide sozinho se
   recusa por ser o mais recente (D8); essa recusa é esperada, não é falha.

Um dump que reprova o SHA-256 ou a releitura não entra no catálogo e não tem
remoção pedida — regra 3 do projeto ("nunca apagar antes de ter o
substituto"), atravessando a rede.
"""

from __future__ import annotations

import datetime
import os
import re
import shlex
import time
from dataclasses import dataclass, field

import banco
import motor
from configuracao import alvo_vps, caminho_sob_raiz
from projetos import AMBIENTE_VPS, CONTAINER_SANDBOX, Projeto

TEMPO_LIMITE_COMANDO = 60
TEMPO_LIMITE_ENVIO = 600

# NOTA SOBRE OS ALERTAS DO CODEQL NESTE MÓDULO
#
# O nome de arquivo dos dumps nasce da saída de um comando SSH (o agente no
# VPS), então o CodeQL o trata como entrada não confiável e abre alertas em
# todo ponto onde ele vira caminho ou volta para uma linha de comando:
# `py/path-injection` aqui e em `tests/test_vps.py` (10, severidade alta) e
# `py/command-line-injection` em `motor.py`, no `subprocess.run` (1, crítico).
#
# `_argumento` (abaixo) passa o nome por `shlex.quote` antes de ele entrar na
# linha do `ssh`. É a correção certa por si só — o `ssh` entrega a string a um
# shell remoto, e é o único ponto deste projeto onde texto de fora encosta em
# contexto de shell —, mas **não silencia o alerta**, e vale registrar por quê
# para ninguém tentar de novo:
#
# O CodeQL não sabe que o `ssh` roda um shell do outro lado. O que ele
# reclama é mais simples e anterior a isso: dado não confiável chegando a um
# `subprocess.run`, ponto. Um argumento controlado por terceiro pode virar
# *flag* do programa invocado (um nome começando com `-`), e para essa
# preocupação `shlex.quote` não é barreira — ele escapa metacaractere de
# shell, não injeção de argumento. Verificado empiricamente: o alerta foi
# reaberto de propósito depois do `shlex.quote` entrar, a análise rodou no
# commit com a correção, e ele continuou apontando `motor.py:88`.
#
# Então os 11 alertas seguem dispensados como falso positivo. O que sustenta
# a dispensa — conferido no servidor, não deduzido da documentação:
#
# 1. O `subprocess.run` de `motor._rodar` recebe **lista**, sem `shell=True`:
#    não há shell local interpretando nada.
# 2. A chave dedicada em `authorized_keys` do VPS está presa por
#    `command="/home/ubuntu/backup-agent.sh"`, com `no-pty`,
#    `no-port-forwarding`, `no-agent-forwarding` e `no-X11-forwarding`. A
#    string que este módulo monta nunca roda como comando remoto: o sshd
#    executa só o agente e deixa a intenção em `SSH_ORIGINAL_COMMAND`. O
#    agente a quebra com `read -r -a` (divisão em palavras, sem `eval` e sem
#    substituição), aceita quatro verbos e recusa o resto; o argumento passa
#    por `resolver_dump`, que faz a própria checagem com `realpath`.
#
# E, para os caminhos locais, mais duas:
#
# 3. `_PADRAO_LISTAGEM`, abaixo: o nome só é aceito se casar
#    `[a-z_]+_banco_\d{8}_\d{6}\.dump`. Não cabe barra, `..`, ponto extra
#    nem metacaractere de shell; uma linha fora do formato levanta
#    `FalhaDeSincronizacao` e nada é lido.
# 4. `configuracao.caminho_sob_raiz`, que resolve o caminho (seguindo links
#    simbólicos) e recusa qualquer destino fora da raiz de backup.
#
# Comentário `# codeql[...]` NÃO resolve: o code scanning do GitHub ignora
# supressão por comentário (só a CLI do CodeQL a honra) — testado neste
# repositório, o alerta continua. O caminho é dispensar os alertas na
# interface, como já foi feito para os de `configuracao.py`.
#
# O que invalidaria as dispensas restantes, e precisa disparar reavaliação:
# afrouxar `_PADRAO_LISTAGEM`, tirar o `command=` do `authorized_keys`, ou o
# agente passar a avaliar `SSH_ORIGINAL_COMMAND` em vez de só quebrá-lo em
# palavras.

# Espelha o formato que `backup-agent.sh verbo_listar` imprime: uma linha por
# dump, "<slug>/<arquivo> <bytes> <sha256-ou-sem-hash>".
_PADRAO_LISTAGEM = re.compile(
    r"^(?P<slug>[a-z_]+)/(?P<arquivo>[a-z_]+_banco_(?P<carimbo>\d{8}_\d{6})\.dump)"
    r" (?P<bytes>\d+) (?P<sha256>[0-9a-f]{64}|sem-hash)$"
)


class FalhaDeSincronizacao(RuntimeError):
    """Erro previsto do ciclo de busca: mensagem já legível para o operador."""


@dataclass
class DumpRemoto:
    slug_servidor: str
    arquivo: str
    caminho_remoto: str  # "<slug>/<arquivo>" — o que enviar/apagar esperam
    bytes: int
    sha256: str
    carimbo: str


@dataclass
class ResultadoSincronizacao:
    buscados: int = 0
    ja_existentes: int = 0
    reprovados: int = 0
    apagados: int = 0
    mantidos: int = 0
    avisos: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# O agente, por SSH
# --------------------------------------------------------------------------


def _argumento(caminho_remoto: str) -> str:
    """Escapa o argumento para o shell do outro lado do SSH.

    O `ssh` entrega a string ao shell remoto — é o único ponto deste projeto
    onde texto vindo de fora encosta num contexto de shell, e por isso o
    escape é feito aqui em vez de se confiar só nas barreiras de fora.

    Para todo nome legítimo isto é uma função identidade: `shlex.quote` só
    aspa quando aparece caractere fora de `[a-zA-Z0-9_@%+=:,./-]`, e o formato
    aceito por `_PADRAO_LISTAGEM` cabe inteiro nesse conjunto. Um nome
    malformado vira uma palavra literal e o agente recusa por verbo/argumento
    inválido — em vez de o shell remoto interpretar qualquer coisa.
    """
    return shlex.quote(caminho_remoto)


def _ssh(alvo: dict[str, str], comando: str, *, saida_arquivo: str | None = None,
         tempo_limite: int = TEMPO_LIMITE_COMANDO):
    return motor._rodar(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-i", alvo["chave"], f"{alvo['usuario']}@{alvo['host']}", comando,
        ],
        saida_arquivo=saida_arquivo,
        tempo_limite=tempo_limite,
    )


def _alvo_configurado() -> dict[str, str]:
    alvo = alvo_vps()
    if alvo is None:
        raise FalhaDeSincronizacao(
            "VPS não configurado — rode: python cli.py configurar-vps <host> --chave <caminho>"
        )
    return alvo


def listar_remoto(alvo: dict[str, str]) -> list[DumpRemoto]:
    processo = _ssh(alvo, "listar")
    if processo.returncode != 0:
        raise FalhaDeSincronizacao(f"listar falhou: {motor._erro(processo)}")

    dumps: list[DumpRemoto] = []
    for linha in processo.stdout.decode("utf-8", "replace").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        m = _PADRAO_LISTAGEM.match(linha)
        if not m:
            raise FalhaDeSincronizacao(f"linha de 'listar' com formato inesperado: {linha!r}")
        dumps.append(DumpRemoto(
            slug_servidor=m["slug"],
            arquivo=m["arquivo"],
            caminho_remoto=f"{m['slug']}/{m['arquivo']}",
            bytes=int(m["bytes"]),
            sha256=m["sha256"],
            carimbo=m["carimbo"],
        ))
    return dumps


def enviar_remoto(alvo: dict[str, str], dump: DumpRemoto, destino: str) -> None:
    processo = _ssh(alvo, f"enviar {_argumento(dump.caminho_remoto)}",
                     saida_arquivo=destino, tempo_limite=TEMPO_LIMITE_ENVIO)
    if processo.returncode != 0:
        raise FalhaDeSincronizacao(f"enviar {dump.caminho_remoto} falhou: {motor._erro(processo)}")


def _apagar_remoto(alvo: dict[str, str], dump: DumpRemoto) -> str:
    """Pede a remoção no servidor. Recusa por ser o mais recente é esperada
    (D8) — quem decide é o servidor, não este cliente."""
    processo = _ssh(alvo, f"apagar {_argumento(dump.caminho_remoto)}")
    if processo.returncode == 0:
        return "apagado"
    if "é o dump mais recente" in motor._erro(processo):
        return "mantido"
    return f"aviso:{motor._erro(processo)}"


# --------------------------------------------------------------------------
# Verificação (regra 2, do lado de cá): sandbox, não o contêiner do projeto
# --------------------------------------------------------------------------


def verificar_dump_no_sandbox(caminho: str) -> None:
    existe, rodando = motor.estado_container(CONTAINER_SANDBOX)
    if not existe:
        raise FalhaDeSincronizacao(
            f"sandbox {CONTAINER_SANDBOX} não existe. Suba com: "
            "docker compose -f compose.teste.yaml up -d"
        )
    if not rodando:
        motor._rodar(["docker", "start", CONTAINER_SANDBOX], tempo_limite=120)
    processo = motor._rodar(
        ["docker", "exec", "-i", CONTAINER_SANDBOX, "pg_restore", "--list"],
        entrada_arquivo=caminho,
    )
    if processo.returncode != 0:
        raise FalhaDeSincronizacao(f"dump não passou em pg_restore --list: {motor._erro(processo)}")


def _criado_em_do_carimbo(carimbo: str) -> str:
    """O carimbo do servidor é UTC (`date -u` em `backup-db.sh`). Converte
    para hora local no mesmo formato que `banco.agora()` produz, para que a
    ordenação por `criado_em` continue comparável entre artefatos locais e
    de origem VPS."""
    utc = datetime.datetime.strptime(carimbo, "%Y%m%d_%H%M%S").replace(
        tzinfo=datetime.timezone.utc
    )
    local = utc.astimezone().replace(tzinfo=None)
    return local.isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------


def sincronizar_projeto(projeto: Projeto, execucao_id: int | None = None) -> ResultadoSincronizacao:
    """O ciclo da Camada 2 para um projeto: busca o que falta, verifica,
    cataloga e só então pede a limpeza no servidor."""
    proprio = execucao_id is None
    if proprio:
        execucao_id = banco.abrir_execucao(projeto.slug, "sincronizacao")

    if projeto.ambiente != AMBIENTE_VPS:
        erro = f"{projeto.slug}: sincronização com o VPS só existe para projetos ambiente='vps'"
        banco.registrar_evento("sincronizacao.falha", erro, projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        banco.fechar_execucao(execucao_id, "falha", erro)
        raise FalhaDeSincronizacao(erro)

    resultado = ResultadoSincronizacao()
    try:
        alvo = _alvo_configurado()
        banco.marcar_fase(execucao_id, "Consultando o servidor", 5)
        remotos = [d for d in listar_remoto(alvo) if d.slug_servidor == projeto.slug_servidor]

        total = len(remotos) or 1
        for indice, dump in enumerate(remotos):
            banco.marcar_fase(
                execucao_id, f"Processando {dump.arquivo}", int(5 + (indice / total) * 85)
            )

            if dump.sha256 == "sem-hash":
                resultado.reprovados += 1
                banco.registrar_evento(
                    "sincronizacao.reprovado",
                    f"{dump.arquivo}: servidor não tem SHA-256 para conferir — não buscado",
                    projeto=projeto.slug, execucao_id=execucao_id, severidade="erro",
                )
                continue

            final = caminho_sob_raiz("projects", projeto.slug, "banco", dump.arquivo)
            if os.path.exists(final):
                resultado.ja_existentes += 1
            elif not _buscar_e_catalogar(projeto, alvo, dump, execucao_id):
                resultado.reprovados += 1
                continue
            else:
                resultado.buscados += 1

            # Regra 3, seção 5.2: só chega aqui um dump já verificado — agora
            # ou num ciclo anterior. O servidor decide se apaga.
            marca = _apagar_remoto(alvo, dump)
            if marca == "apagado":
                resultado.apagados += 1
            elif marca == "mantido":
                resultado.mantidos += 1
            else:
                mensagem = f"{dump.arquivo}: {marca[len('aviso:'):]}"
                resultado.avisos.append(mensagem)
                banco.registrar_evento("sincronizacao.aviso", mensagem, projeto=projeto.slug,
                                       execucao_id=execucao_id, severidade="aviso")

        banco.fechar_execucao(execucao_id, "sucesso")
        banco.registrar_evento(
            "sincronizacao.sucesso",
            f"{resultado.buscados} buscado(s), {resultado.ja_existentes} já existia(m), "
            f"{resultado.reprovados} reprovado(s), {resultado.apagados} apagado(s) do "
            f"servidor, {resultado.mantidos} mantido(s) (mais recente)",
            projeto=projeto.slug, execucao_id=execucao_id,
        )
        return resultado
    except Exception as erro:
        banco.fechar_execucao(execucao_id, "falha", str(erro))
        banco.registrar_evento("sincronizacao.falha", str(erro), projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        raise


def _buscar_e_catalogar(
    projeto: Projeto, alvo: dict[str, str], dump: DumpRemoto, execucao_id: int | None
) -> bool:
    """Busca, confere e cataloga um dump. Devolve se deu certo; nunca deixa
    um `.tmp` para trás, dê certo ou não."""
    motor._pasta_temp()
    tmp = caminho_sob_raiz("temp", dump.arquivo + ".tmp")
    inicio = time.monotonic()
    try:
        enviar_remoto(alvo, dump, tmp)

        digest = motor.sha256_arquivo(tmp)
        if digest != dump.sha256:
            raise FalhaDeSincronizacao(
                f"SHA-256 não confere para {dump.arquivo}: "
                f"esperado {dump.sha256[:16]}…, recebido {digest[:16]}…"
            )
        verificar_dump_no_sandbox(tmp)

        tamanho = os.path.getsize(tmp)
        duracao = int((time.monotonic() - inicio) * 1000)
        criado_em = _criado_em_do_carimbo(dump.carimbo)
        final = caminho_sob_raiz("projects", projeto.slug, "banco", dump.arquivo)
        motor._pasta_destino(projeto, "banco")
        motor._promover(
            tmp, final,
            {
                "projeto": projeto.slug,
                "tipo": "banco",
                "arquivo": dump.arquivo,
                "criado_em": criado_em,
                "bytes": tamanho,
                "sha256": digest,
                "duracao_ms": duracao,
                "origem": {"servidor": alvo["host"], "arquivo_remoto": dump.caminho_remoto},
            },
        )
        banco.registrar_artefato(
            projeto=projeto.slug, tipo="banco",
            caminho_relativo=motor._relativo(final),
            bytes_=tamanho, sha256=digest, duracao_ms=duracao,
            execucao_id=execucao_id, criado_em=criado_em,
        )
        return True
    except FalhaDeSincronizacao as erro:
        banco.registrar_evento("sincronizacao.reprovado", str(erro), projeto=projeto.slug,
                               execucao_id=execucao_id, severidade="erro")
        return False
    finally:
        # Sobra de tentativa falha (ou o `.tmp` já promovido, que simplesmente
        # não existe mais neste caminho) não fica ocupando espaço.
        if os.path.exists(tmp):
            os.remove(tmp)
