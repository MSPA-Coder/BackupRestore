"""Linha de comando — é isto que o Agendador de Tarefas do Windows chama.

    python cli.py backup --todos
    python cli.py listar
    python cli.py verificar
    python cli.py ensaio --projeto conforto_termico

A interface usa exatamente as mesmas funções; nada de execução vive nela.
"""

from __future__ import annotations

import argparse
import os
import sys

import banco
import motor
import restaurar as restauracao
import vps
from projetos import (
    AMBIENTE_VPS,
    CONTAINER_SANDBOX,
    PROJETOS,
    por_slug,
)
from configuracao import ConfiguracaoInvalida, configurar_raiz_backup, configurar_vps, raiz_backup

USUARIO_SANDBOX = "sandbox"


def _tamanho(bytes_: int) -> str:
    valor = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            return f"{valor:,.1f} {unidade}".replace(",", "·")
        valor /= 1024
    return f"{valor:.1f} GB"


def comando_backup(args: argparse.Namespace) -> int:
    # "--todos" continua sendo só os projetos locais: motor.fazer_backup
    # recusa qualquer outro ambiente. Os artefatos do VPS chegam pela via
    # separada `sincronizar-vps`; pedir um projeto de outro ambiente por slug
    # explícito segue permitido — e recusado com erro claro pelo motor.
    alvos = [p for p in PROJETOS if p.ambiente == "local"] if args.todos else [por_slug(args.projeto)]
    tipos = tuple(args.tipos.split(",")) if args.tipos else None

    falhas = 0
    for projeto in alvos:
        print(f"\n== {projeto.nome} ==")
        try:
            resultados = motor.fazer_backup(projeto, tipos)
            for resultado in resultados:
                print(f"   {resultado.tipo:7} {_tamanho(resultado.bytes):>12}  "
                      f"{resultado.sha256[:16]}…  {resultado.duracao_ms/1000:.1f}s")
        except Exception as erro:
            falhas += 1
            print(f"   FALHOU: {erro}", file=sys.stderr)
    if falhas:
        print(f"\n{falhas} projeto(s) falharam.", file=sys.stderr)
    return 1 if falhas else 0


def comando_listar(args: argparse.Namespace) -> int:
    linhas = banco.listar_artefatos(args.projeto)
    if not linhas:
        print("Nenhum artefato no catálogo.")
        return 0
    print(f"{'id':>4}  {'projeto':<24} {'tipo':<7} {'situação':<10} {'tamanho':>12}  criado em")
    for linha in linhas:
        marca = " *" if linha["fixado"] else "  "
        finalidade = "" if linha["finalidade"] == "regular" else " [segurança]"
        print(f"{linha['id']:>4}{marca}{linha['projeto']:<24} {linha['tipo']:<7} "
              f"{linha['situacao']:<10} {_tamanho(linha['bytes']):>12}  "
              f"{linha['criado_em']}{finalidade}")
    return 0


def comando_verificar(args: argparse.Namespace) -> int:
    print("Relendo artefatos e conferindo SHA-256…")
    contagem = motor.verificar(args.projeto)
    print(f"   conferidos: {contagem['conferidos']}")
    print(f"   ausentes:   {contagem['ausentes']}")
    print(f"   corrompidos:{contagem['corrompidos']}")
    return 1 if (contagem["ausentes"] or contagem["corrompidos"]) else 0


def comando_configurar_raiz(args: argparse.Namespace) -> int:
    """Única via de escrita da configuração de destino: operador no host."""
    if banco.listar_artefatos(limite=1):
        try:
            atual = os.path.normcase(os.path.normpath(raiz_backup()))
            solicitado = os.path.normcase(os.path.normpath(os.path.abspath(args.caminho)))
        except ConfiguracaoInvalida as erro:
            print(f"Configuração atual inválida: {erro}", file=sys.stderr)
            return 2
        if solicitado != atual:
            print(
                "O destino não pode mudar enquanto existem artefatos catalogados. "
                "Migre arquivos e catálogo conscientemente antes de alterar a raiz.",
                file=sys.stderr,
            )
            return 2
    try:
        raiz = configurar_raiz_backup(args.caminho, permitida=args.permitida)
    except ConfiguracaoInvalida as erro:
        print(f"Configuração recusada: {erro}", file=sys.stderr)
        return 2
    print(f"Destino configurado: {raiz}")
    return 0


def comando_sincronizar_vps(args: argparse.Namespace) -> int:
    """Camada 2: busca, verifica e cataloga os dumps que o servidor produziu
    sozinho (Camada 1). Nunca fala com um contêiner de projeto."""
    alvos = [p for p in PROJETOS if p.ambiente == AMBIENTE_VPS] if args.todos else [por_slug(args.projeto)]

    falhas = 0
    for projeto in alvos:
        print(f"\n== {projeto.nome} ==")
        try:
            resultado = vps.sincronizar_projeto(projeto)
            print(f"   buscados: {resultado.buscados}   já existiam: {resultado.ja_existentes}   "
                  f"reprovados: {resultado.reprovados}")
            print(f"   apagados no servidor: {resultado.apagados}   "
                  f"mantidos (mais recente): {resultado.mantidos}")
            for aviso in resultado.avisos:
                print(f"   AVISO: {aviso}", file=sys.stderr)
        except Exception as erro:
            falhas += 1
            print(f"   FALHOU: {erro}", file=sys.stderr)
    if falhas:
        print(f"\n{falhas} projeto(s) falharam.", file=sys.stderr)
    return 1 if falhas else 0


def comando_configurar_vps(args: argparse.Namespace) -> int:
    """Única via de escrita do alvo SSH do VPS: operador no host."""
    try:
        alvo = configurar_vps(args.host, args.usuario, args.chave)
    except ConfiguracaoInvalida as erro:
        print(f"Configuração recusada: {erro}", file=sys.stderr)
        return 2
    print(f"VPS configurado: {alvo['usuario']}@{alvo['host']}  (chave: {alvo['chave']})")
    return 0


def comando_restaurar(args: argparse.Namespace) -> int:
    try:
        resultado = restauracao.restaurar(
            args.artefato,
            container_destino=args.destino_container,
            banco_destino=args.destino_banco,
            usuario_destino=args.destino_usuario,
            confirmacao=args.confirmar or "",
        )
    except restauracao.RestauracaoRecusada as erro:
        print(f"RECUSADO: {erro}", file=sys.stderr)
        return 2
    print(f"Restaurado em {resultado['destino']}")
    if resultado["dump_de_seguranca"]:
        print(f"Dump de segurança: {resultado['dump_de_seguranca']}")
    print(f"Tabelas com dados: {len(resultado['tabelas'])}")
    return 0


def comando_ensaio(args: argparse.Namespace) -> int:
    """O teste que importa: restaura no sandbox e compara com a origem.

    Nunca toca no projeto original — o destino é sempre o contêiner descartável,
    e `restaurar.py` recusaria qualquer outro."""
    projeto = por_slug(args.projeto)
    dumps = banco.artefatos_validos(projeto.slug, "banco")
    if not dumps:
        print(f"Nenhum dump válido de {projeto.nome} no catálogo.", file=sys.stderr)
        return 1
    artefato = dumps[0]
    destino = f"ensaio_{projeto.slug}"

    print(f"Ensaio de restauração de {projeto.nome}")
    print(f"   artefato: #{artefato['id']} {artefato['caminho_relativo']}")
    print(f"   destino:  {CONTAINER_SANDBOX}/{destino}  (descartável)")

    existe, rodando = motor.estado_container(CONTAINER_SANDBOX)
    if not existe:
        print("Sandbox não existe. Suba com:\n"
              "   docker compose -f compose.teste.yaml up -d", file=sys.stderr)
        return 1
    if not rodando:
        motor._rodar(["docker", "start", CONTAINER_SANDBOX], tempo_limite=120)

    try:
        restauracao.restaurar(
            artefato["id"],
            container_destino=CONTAINER_SANDBOX,
            banco_destino=destino,
            usuario_destino=USUARIO_SANDBOX,
            confirmacao=destino,
        )
    except restauracao.RestauracaoRecusada as erro:
        print(f"RECUSADO: {erro}", file=sys.stderr)
        return 2

    if projeto.ambiente != "local":
        # A origem é outra máquina — `comparar_com_origem` recusaria (ver a
        # trava em restaurar.py), de propósito: o agente da Camada 2 só sabe
        # listar/enviar/apagar/estado, não tem verbo de consulta SQL, e
        # não é para ganhar um só para isto. Aqui só confere o que a
        # restauração produziu; bater com a produção é conferência manual.
        resumo = restauracao.resumo_banco(CONTAINER_SANDBOX, USUARIO_SANDBOX, destino)
        print(f"\n   RESTAURADO — {len(resumo)} tabela(s) com dados, "
              f"{sum(resumo.values()):,} linha(s) no total.".replace(",", "."))
        print(f"   Comparação automática com a origem não existe para ambiente="
              f"{projeto.ambiente!r} (o agente do VPS não oferece consulta SQL).")
        return 0 if resumo else 1

    print("\nComparando com a origem…")
    comparacao = restauracao.comparar_com_origem(
        projeto, CONTAINER_SANDBOX, USUARIO_SANDBOX, destino
    )
    print(f"   tabelas: origem {comparacao['tabelas_origem']} / "
          f"destino {comparacao['tabelas_destino']}")
    print(f"   linhas:  origem {comparacao['linhas_origem']:,} / "
          f"destino {comparacao['linhas_destino']:,}".replace(",", "."))
    if comparacao["confere"]:
        print("\n   CONFERE — este artefato restaura.")
        return 0
    print("\n   RESTAURADO — a origem mudou desde a captura:")
    for tabela, (origem, dest) in list(comparacao["divergencias"].items())[:15]:
        print(f"      {tabela}: origem atual={origem} / artefato={dest}")
    print("   A divergência é informativa; pg_restore e consultas no destino passaram.")
    return 0 if comparacao["restauracao_valida"] else 1


def main(argv: list[str] | None = None) -> int:
    banco.criar_tabelas()

    analisador = argparse.ArgumentParser(
        prog="cli.py", description="BackupRestore — backup e restauração dos projetos locais."
    )
    sub = analisador.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("backup", help="gera os artefatos de um ou de todos os projetos")
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--todos", action="store_true")
    grupo.add_argument("--projeto")
    p.add_argument("--tipos", help="banco,codigo (padrão: ambos)")
    p.set_defaults(funcao=comando_backup)

    p = sub.add_parser("listar", help="mostra o catálogo")
    p.add_argument("--projeto")
    p.set_defaults(funcao=comando_listar)

    p = sub.add_parser("verificar", help="relê os arquivos e confere o SHA-256")
    p.add_argument("--projeto")
    p.set_defaults(funcao=comando_verificar)

    p = sub.add_parser(
        "configurar-raiz",
        help="define localmente a raiz de backup e seu limite permitido",
    )
    p.add_argument("caminho", help="destino dos artefatos")
    p.add_argument(
        "--permitida",
        help="raiz permitida pelo operador; sem ela, o destino é o próprio limite",
    )
    p.set_defaults(funcao=comando_configurar_raiz)

    p = sub.add_parser(
        "sincronizar-vps",
        help="busca, verifica e cataloga os dumps que o VPS produziu (Camada 2)",
    )
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--todos", action="store_true")
    grupo.add_argument("--projeto")
    p.set_defaults(funcao=comando_sincronizar_vps)

    p = sub.add_parser(
        "configurar-vps",
        help="define o alvo SSH do VPS (host, usuário, chave) para a Camada 2 do backup",
    )
    p.add_argument("host", help="endereço ou apelido SSH do VPS")
    p.add_argument("--usuario", default="ubuntu")
    p.add_argument("--chave", required=True, help="caminho da chave SSH dedicada do agente")
    p.set_defaults(funcao=comando_configurar_vps)

    p = sub.add_parser("restaurar", help="restaura um dump somente no sandbox descartável")
    p.add_argument("--artefato", type=int, required=True)
    p.add_argument("--destino-container", required=True)
    p.add_argument("--destino-banco", required=True)
    p.add_argument("--destino-usuario", default=USUARIO_SANDBOX)
    p.add_argument("--confirmar", help="digite o nome do banco de destino")
    p.set_defaults(funcao=comando_restaurar)

    p = sub.add_parser("ensaio", help="restaura no sandbox e compara com a origem")
    p.add_argument("--projeto", required=True)
    p.set_defaults(funcao=comando_ensaio)

    args = analisador.parse_args(argv)
    return int(args.funcao(args))


if __name__ == "__main__":
    sys.exit(main())
