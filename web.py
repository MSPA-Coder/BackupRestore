"""Interface — olha e dispara, nunca executa.

Toda operação real acontece em `motor.py` e `restaurar.py`, os mesmos módulos
que a CLI usa. As rotas aqui abrem uma thread e devolvem a página; o progresso
vem do catálogo, gravado pelo motor. Foi exatamente o inverso disso que
inviabilizou o MVP em React: um navegador não executa pg_dump, então o
progresso de lá era `sleep(700ms)` e o SHA-256 era `Math.random()`.

Uso:
    python web.py        →  http://127.0.0.1:5401

Escuta só em 127.0.0.1. Sem login, como o resto do escopo — mas então o
servidor não pode ficar exposto na rede.
"""

from __future__ import annotations

import os
import threading

from datetime import datetime
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

import banco
import motor
import restaurar as restauracao
from configuracao import alvo_vps, raiz_backup, raiz_permitida
from projetos import (
    AMBIENTE_LOCAL,
    AMBIENTE_VPS,
    CONTAINER_SANDBOX,
    PROJETOS,
    RAIZ_PROJETOS,
    por_slug,
)

USUARIO_SANDBOX = "sandbox"
ROTULOS = {"banco": "Banco de dados", "codigo": "Código"}

# A tarefa agendada roda todo dia. Dois dias sem nenhuma execução no catálogo
# não é falha registrada — é ausência de registro, que foi exatamente como uma
# tarefa desabilitada passou despercebida. Dois dias em vez de um para não
# alarmar por um único atraso (host desligado numa noite).
DIAS_DE_SILENCIO_ATE_ALERTAR = 2

# A tarefa agendada faz duas coisas: sincroniza os projetos de origem VPS e faz
# backup dos locais. Uma falha em qualquer outro par projeto/operação veio de
# uma chamada manual — inclusive as recusas de `backup` num projeto de origem
# VPS, que o motor rejeita por projeto. Nada as repetirá, então elas nunca
# sairiam do destaque sozinhas; e um alerta que não some é um alerta que se
# aprende a ignorar.
OPERACAO_DO_AGENDAMENTO = {AMBIENTE_LOCAL: "backup", AMBIENTE_VPS: "sincronizacao"}

app = Flask(__name__)


# --------------------------------------------------------------------------
# Fronteira de origem
# --------------------------------------------------------------------------
#
# Escutar em 127.0.0.1 impede que a rede alcance este servidor. NÃO impede
# duas coisas que passam pelo navegador de quem está no host:
#
# 1. CSRF de origem cruzada -- enquanto `web.py` estiver rodando, qualquer
#    página aberta no navegador pode enviar um POST de formulário para
#    `http://127.0.0.1:5401/...` sem precisar ler a resposta. Isso dispara
#    backups em série ou alterna a marca de "fixado" nos artefatos.
# 2. DNS rebinding -- um domínio hostil que resolva para 127.0.0.1 faz o
#    navegador falar com este servidor mandando `Host: dominio.hostil`, e aí
#    até as rotas GET (o catálogo, o progresso das execuções) viram leitura
#    para fora.
#
# Duas checagens baratas fecham as duas, sem trazer login nem `SECRET_KEY`
# para um utilitário que não tem nenhum dos dois:
#
# - `Host` precisa ser loopback, em toda requisição -- mata o rebinding;
# - `Origin`, QUANDO PRESENTE, precisa ser loopback nos métodos mutantes.
#
# Origin ausente é aceito de propósito: navegador sempre manda `Origin` num
# POST de formulário, então a ausência significa cliente que não é navegador
# (curl, script) -- que não é o cenário de CSRF e é como a CLI conversaria
# com o servidor se um dia precisar.
HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
METODOS_MUTANTES = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _hospedeiro(valor: str) -> str:
    """Extrai o hostname de um `Host:` ou de uma origem, sem a porta."""
    sem_esquema = valor.split("//", 1)[-1] if "//" in valor else valor
    return urlsplit(f"//{sem_esquema}").hostname or ""


@app.before_request
def _recusar_origem_estranha():
    if _hospedeiro(request.host) not in HOSTS_LOOPBACK:
        abort(403, "este servidor só atende em loopback")

    if request.method not in METODOS_MUTANTES:
        return None

    origem = request.headers.get("Origin")
    if origem and _hospedeiro(origem) not in HOSTS_LOOPBACK:
        abort(403, "origem não permitida")
    return None


# --------------------------------------------------------------------------
# Apresentação
# --------------------------------------------------------------------------


@app.template_filter("tamanho")
def filtro_tamanho(bytes_: int | None) -> str:
    if not bytes_:
        return "—"
    valor = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} GB"


@app.template_filter("momento")
def filtro_momento(texto: str | None) -> str:
    if not texto:
        return "nunca"
    data, _, hora = texto.partition("T")
    ano, mes, dia = data.split("-")
    return f"{dia}/{mes} {hora[:5]}"


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------


def _falhas_do_agendamento() -> list:
    """Só o que a tarefa diária repete — e que, portanto, pode se resolver."""
    automaticas = {
        (projeto.slug, OPERACAO_DO_AGENDAMENTO.get(projeto.ambiente))
        for projeto in PROJETOS
    }
    return [
        falha for falha in banco.falhas_atuais()
        if (falha["projeto"], falha["operacao"]) in automaticas
    ]


def _dias_desde(momento: str | None) -> int | None:
    if not momento:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(momento)).days
    except ValueError:
        return None


@app.get("/")
def painel():
    resumos = {p.slug: banco.resumo_projeto(p.slug) for p in PROJETOS}
    artefatos = [a for a in banco.listar_artefatos(limite=1000) if a["tipo"] in ROTULOS]
    problemas = [a for a in artefatos if a["situacao"] in ("corrompido", "ausente")]
    execucoes = banco.listar_execucoes(limite=20)
    # O que está falhando *agora* — não o histórico. Fica no topo do painel
    # porque o código de saída da tarefa agendada não chega a lugar nenhum.
    falhas = _falhas_do_agendamento()
    dias_sem_execucao = _dias_desde(banco.momento_ultima_execucao())
    return render_template(
        "painel.html",
        projetos=PROJETOS,
        resumos=resumos,
        falhas=falhas,
        dias_sem_execucao=dias_sem_execucao,
        silencio=dias_sem_execucao is None or dias_sem_execucao >= DIAS_DE_SILENCIO_ATE_ALERTAR,
        total_artefatos=len([a for a in artefatos if a["situacao"] == "valido"]),
        total_bytes=sum(a["bytes"] for a in artefatos if a["situacao"] == "valido"),
        problemas=problemas,
        espaco_livre=motor.espaco_livre(),
        raiz=raiz_backup(),
        em_andamento=[e for e in execucoes if e["situacao"] in ("fila", "rodando")],
    )


@app.get("/projetos")
def listar_projetos():
    return render_template(
        "projetos.html",
        projetos=PROJETOS,
        resumos={p.slug: banco.resumo_projeto(p.slug) for p in PROJETOS},
    )


@app.get("/projeto/<slug>")
def projeto(slug: str):
    try:
        alvo = por_slug(slug)
    except KeyError:
        abort(404)
    tipo = request.args.get("tipo", "banco")
    if tipo not in ROTULOS:
        tipo = "banco"

    todos = banco.listar_artefatos(slug, limite=500)
    contagens = {t: len([a for a in todos if a["tipo"] == t and a["finalidade"] == "regular"])
                 for t in ROTULOS}
    return render_template(
        "projeto.html",
        projeto=alvo,
        tipo=tipo,
        rotulos=ROTULOS,
        contagens=contagens,
        artefatos=[a for a in todos if a["tipo"] == tipo],
        resumo=banco.resumo_projeto(slug),
        sandbox=CONTAINER_SANDBOX,
    )


@app.get("/backups")
def backups():
    projeto_atual = request.args.get("projeto", "")
    tipo_atual = request.args.get("tipo", "")
    if projeto_atual and projeto_atual not in {p.slug for p in PROJETOS}:
        abort(404)
    if tipo_atual and tipo_atual not in ROTULOS:
        abort(404)

    artefatos = [a for a in banco.listar_artefatos(projeto_atual or None, limite=500) if a["tipo"] in ROTULOS]
    if tipo_atual:
        artefatos = [a for a in artefatos if a["tipo"] == tipo_atual]
    return render_template(
        "backups.html",
        artefatos=artefatos,
        projetos=PROJETOS,
        projeto_atual=projeto_atual,
        tipo_atual=tipo_atual,
        rotulos=ROTULOS,
    )


@app.get("/restaurar")
def restaurar_catalogo():
    artefatos = [
        a for a in banco.listar_artefatos(limite=500)
        if a["tipo"] == "banco" and a["situacao"] == "valido"
    ]
    return render_template("restaurar_catalogo.html", artefatos=artefatos)


@app.get("/integridade")
def integridade():
    artefatos = [a for a in banco.listar_artefatos(limite=500) if a["tipo"] in ROTULOS]
    return render_template(
        "integridade.html",
        artefatos=artefatos,
        inteiros=sum(1 for a in artefatos if a["situacao"] == "valido"),
        problemas=sum(1 for a in artefatos if a["situacao"] in ("corrompido", "ausente")),
    )


@app.get("/retencao")
def retencao():
    contagens: dict[str, dict[str, int]] = {}
    for projeto_atual in PROJETOS:
        contagens[projeto_atual.slug] = {
            tipo: len(banco.artefatos_validos(projeto_atual.slug, tipo))
            for tipo in ROTULOS
        }
    return render_template("retencao.html", projetos=PROJETOS, contagens=contagens)


@app.get("/configuracoes")
def configuracoes():
    return render_template(
        "configuracoes.html",
        raiz_backup=raiz_backup(),
        raiz_permitida=raiz_permitida(),
        raiz_projetos=RAIZ_PROJETOS,
        espaco_livre=motor.espaco_livre(),
        # Só leitura: quem escreve é `cli.py configurar-vps`.
        vps=alvo_vps(),
    )


@app.get("/historico")
def historico():
    return render_template(
        "historico.html",
        execucoes=banco.listar_execucoes(limite=60),
        eventos=banco.listar_eventos(limite=80),
    )


# --------------------------------------------------------------------------
# Ações
# --------------------------------------------------------------------------


def _executar_em_thread(slug: str, execucao_id: int) -> None:
    alvo = por_slug(slug)

    def tarefa() -> None:
        try:
            motor.fazer_backup(alvo, execucao_id=execucao_id)
        except Exception:
            # fazer_backup já registrou a falha no catálogo e no histórico.
            pass

    threading.Thread(target=tarefa, daemon=True, name=f"backup-{slug}").start()


@app.post("/projeto/<slug>/backup")
def disparar_backup(slug: str):
    try:
        por_slug(slug)
    except KeyError:
        abort(404)
    execucao_id = banco.enfileirar_execucao(slug, "backup")
    _executar_em_thread(slug, execucao_id)
    return redirect(url_for("projeto", slug=slug, execucao=execucao_id))


@app.post("/artefato/<int:artefato_id>/fixar")
def fixar(artefato_id: int):
    artefato = banco.obter_artefato(artefato_id)
    if artefato is None:
        abort(404)
    banco.fixar_artefato(artefato_id, not artefato["fixado"])
    return redirect(url_for("projeto", slug=artefato["projeto"], tipo=artefato["tipo"]))


@app.get("/api/execucao/<int:execucao_id>")
def api_execucao(execucao_id: int):
    """Progresso real, gravado pelo motor a cada fase concluída."""
    execucao = banco.obter_execucao(execucao_id)
    if execucao is None:
        abort(404)
    return jsonify(dict(execucao))


# --------------------------------------------------------------------------
# Restauração
# --------------------------------------------------------------------------


@app.get("/restaurar/<int:artefato_id>")
def tela_restaurar(artefato_id: int):
    artefato = banco.obter_artefato(artefato_id)
    if artefato is None:
        abort(404)
    if artefato["tipo"] != "banco":
        abort(400, "só dumps de banco são restauráveis por aqui")
    alvo = por_slug(artefato["projeto"])
    existe, _ = motor.estado_container(CONTAINER_SANDBOX)
    return render_template(
        "restaurar.html",
        artefato=artefato,
        projeto=alvo,
        sandbox=CONTAINER_SANDBOX,
        banco_destino=f"ensaio_{alvo.slug}",
        sandbox_disponivel=existe,
        erro=request.args.get("erro"),
    )


@app.post("/restaurar/<int:artefato_id>")
def executar_restauracao(artefato_id: int):
    artefato = banco.obter_artefato(artefato_id)
    if artefato is None:
        abort(404)
    alvo = por_slug(artefato["projeto"])
    destino = f"ensaio_{alvo.slug}"
    try:
        # O destino é fixo e descartável. A interface não oferece escolha de
        # contêiner, e `restaurar.py` aceita exclusivamente este nome.
        resultado = restauracao.restaurar(
            artefato_id,
            container_destino=CONTAINER_SANDBOX,
            banco_destino=destino,
            usuario_destino=USUARIO_SANDBOX,
            confirmacao=request.form.get("confirmacao", ""),
        )
    except restauracao.RestauracaoRecusada as erro:
        return redirect(url_for("tela_restaurar", artefato_id=artefato_id, erro=str(erro)))
    except Exception as erro:  # falha durante a restauração já ficou no histórico
        return redirect(url_for("tela_restaurar", artefato_id=artefato_id, erro=str(erro)))

    return render_template(
        "restaurado.html",
        resultado=resultado,
        projeto=alvo,
        sandbox=CONTAINER_SANDBOX,
        banco_destino=destino,
    )


if __name__ == "__main__":
    banco.criar_tabelas()
    os.makedirs(raiz_backup(), exist_ok=True)
    app.run(host="127.0.0.1", port=5401, debug=False)
