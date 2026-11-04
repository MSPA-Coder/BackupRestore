"""Vigia do backup: avisa no Windows quando o VPS acumula dumps que este PC não baixou.

São duas falhas distintas, e só uma o backup relata sozinho. A sincronização
que falha (Tailscale fora, por exemplo) roda, grava `falha` e aparece no
painel e em `ultima-execucao.txt`. A tarefa `BackupRestore` desabilitada não
roda, logo não relata nada: aconteceu cinco vezes entre 02/09 e 20/09/2026, e
o único sinal era o alerta de silêncio do painel, que só vê quem o abre. Por
isso este vigia é uma tarefa agendada à parte ("BackupRestore Vigia",
`scripts/registrar-vigia.ps1`), e não um passo da tarefa de backup.

A evidência mora no VPS, sem código novo em produção: a sincronização apaga
do servidor cada dump depois de baixá-lo e conferi-lo (o servidor só guarda o
mais recente), então um dump antigo lá que o catálogo daqui não conhece é
prova de que nada está sendo baixado. O verbo `listar` do agente já devolve
esses dumps com carimbo, bytes e SHA-256. O vigia só lê: não busca, não apaga
e não escreve no catálogo.

O canal é a notificação do Windows, escolhida pelo mantenedor em 20/09/2026.
O VPS não consegue empurrar nada para esta máquina (ela não roda servidor
SSH), então quem busca o veredito é este leitor, rodando aqui. Limite honesto:
ele é uma segunda tarefa agendada e pode ser desabilitada pelo mesmo agente
que já desligou a de backup; a auditoria do Agendador registra quem fizer isso.

Saída 0 quando está tudo em dia, 1 quando há o que avisar. O veredito, com ou
sem aviso, fica em `vigia-ultimo.txt`.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape

import agendamento
import banco
import configuracao
import vps
from projetos import AMBIENTE_VPS, PROJETOS, Projeto

RAIZ_PROJETO = Path(__file__).resolve().parent
ARQUIVO_VEREDITO = RAIZ_PROJETO / "vigia-ultimo.txt"

#: Idade a partir da qual um dump que continua no servidor sem ter sido
#: baixado vira aviso. O VPS produz às 03:00 de São Paulo e a tarefa busca às
#: 03:30; 30 h cobrem um dia inteiro perdido mais a folga de um PC ligado
#: tarde, sem esperar uma semana para avisar.
LIMITE_HORAS = 30

#: Identificador do PowerShell para o `ToastNotificationManager`: a notificação
#: sai em nome dele, sem registrar um aplicativo novo no Windows.
APP_ID_POWERSHELL = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Pendente:
    """Um dump que está no servidor há mais de `LIMITE_HORAS` e não chegou aqui."""

    projeto: str
    arquivo: str
    produzido_em: datetime

    def horas(self, agora: datetime) -> int:
        return int((agora - self.produzido_em).total_seconds() // 3600)


@dataclass
class Veredito:
    verificado_em: datetime
    pendentes: list[Pendente] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)
    projetos_consultados: int = 0

    @property
    def em_dia(self) -> bool:
        return not self.pendentes and not self.falhas


def carimbo_utc(carimbo: str) -> datetime:
    """`backup-db.sh` carimba o nome do dump em UTC (`AAAAMMDD_HHMMSS`)."""
    return datetime.strptime(carimbo, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)


def catalogado(projeto: str, dump: vps.DumpRemoto) -> bool:
    """Se o catálogo já conhece este arquivo do projeto, em qualquer situação.

    Qualquer situação, e não só `valido`: um dump baixado que a retenção local
    já removeu continua no servidor quando o `apagar` de lá falhou, e isso não
    é backup parado. Compara pelo SHA-256 e, quando o servidor não o tem, pelo
    nome do arquivo."""
    for linha in banco.artefatos_do_projeto(projeto):
        if dump.sha256 != "sem-hash" and linha["sha256"] == dump.sha256:
            return True
        if Path(linha["caminho_relativo"]).name == dump.arquivo:
            return True
    return False


def avaliar(
    agora: datetime,
    *,
    servidores: Mapping[str, Mapping[str, str]],
    projetos: Iterable[Projeto] = PROJETOS,
    listar: Callable[[Mapping[str, str], str], list[vps.DumpRemoto]] = vps.listar_remoto,
    ja_baixado: Callable[[str, vps.DumpRemoto], bool] = catalogado,
    limite: timedelta = timedelta(hours=LIMITE_HORAS),
) -> Veredito:
    """Consulta cada projeto de origem VPS e separa o que ficou para trás.

    Uma consulta por projeto, como a sincronização faz: uma linha estranha na
    pasta de um projeto não esconde o estado dos outros. Projeto cujo servidor
    não está configurado, ou que não responde, vira falha, porque dele também
    não vem backup."""
    veredito = Veredito(verificado_em=agora)
    for projeto in projetos:
        if projeto.ambiente != AMBIENTE_VPS:
            continue
        alvo = servidores.get(projeto.servidor)
        if alvo is None:
            veredito.falhas.append(f"{projeto.slug}: servidor {projeto.servidor!r} não configurado")
            continue
        try:
            remotos = listar(alvo, projeto.slug_servidor)
        except vps.FalhaDeSincronizacao as erro:
            veredito.falhas.append(f"{projeto.slug}: {erro}")
            continue
        veredito.projetos_consultados += 1
        for dump in remotos:
            produzido_em = carimbo_utc(dump.carimbo)
            if agora - produzido_em < limite or ja_baixado(projeto.slug, dump):
                continue
            veredito.pendentes.append(Pendente(projeto.slug, dump.arquivo, produzido_em))
    veredito.pendentes.sort(key=lambda p: p.produzido_em)
    return veredito


def _local(momento: datetime) -> str:
    return momento.astimezone().strftime("%d/%m/%Y %H:%M")


def mensagem(veredito: Veredito) -> tuple[str, str]:
    """Título e corpo curtos, para caber na notificação."""
    partes = []
    if veredito.pendentes:
        antigo = veredito.pendentes[0]
        quantos = len(veredito.pendentes)
        partes.append(
            f"{quantos} dump{'s' if quantos > 1 else ''} do VPS sem baixar há mais de "
            f"{LIMITE_HORAS} h; o mais antigo é de {antigo.projeto}, de "
            f"{_local(antigo.produzido_em)}."
        )
    if veredito.falhas:
        quantos = len(veredito.falhas)
        partes.append(
            f"{quantos} projeto{'s' if quantos > 1 else ''} sem resposta do servidor: "
            f"{veredito.falhas[0]}"
        )
    partes.append("Confira se a tarefa BackupRestore está habilitada e o Tailscale, ligado.")
    return "Backup do VPS parado", " ".join(partes)


def montar_relatorio(veredito: Veredito) -> str:
    agora = veredito.verificado_em
    linhas = [
        "BackupRestore — vigia do backup do VPS",
        "=" * 64,
        f"Verificado em: {_local(agora)}",
        f"Projetos consultados: {veredito.projetos_consultados}",
        f"RESULTADO: {'EM DIA' if veredito.em_dia else 'ATENÇÃO'}",
    ]
    if veredito.pendentes:
        linhas += ["", f"NÃO BAIXADOS HÁ MAIS DE {LIMITE_HORAS} H ({len(veredito.pendentes)})",
                   "-" * 64]
        linhas += [
            f"  {p.projeto}  {p.arquivo}  ({p.horas(agora)} h)" for p in veredito.pendentes
        ]
    if veredito.falhas:
        linhas += ["", f"SEM RESPOSTA ({len(veredito.falhas)})", "-" * 64]
        linhas += [f"  {falha}" for falha in veredito.falhas]
    if not veredito.em_dia:
        linhas += [
            "",
            "O que conferir, nesta ordem:",
            "  1. Get-ScheduledTaskInfo -TaskName BackupRestore (State e NumberOfMissedRuns);",
            "  2. tailscale status;",
            "  3. ultima-execucao.txt e o painel (falhas da última sincronização).",
        ]
    return "\n".join(linhas) + "\n"


def comando_notificacao(titulo: str, texto: str) -> list[str]:
    """PowerShell 5.1 que mostra uma notificação persistente do Windows.

    `scenario="reminder"` a mantém na tela até ser fechada: o alerta de
    silêncio do painel já existia e passou despercebido por ser passivo.
    `-EncodedCommand` evita qualquer problema de aspas no texto."""
    xml = (
        '<toast scenario="reminder"><visual><binding template="ToastGeneric">'
        f"<text>{escape(titulo)}</text><text>{escape(texto)}</text>"
        '</binding></visual><actions>'
        '<action content="Fechar" arguments="dismiss" activationType="system"/>'
        "</actions></toast>"
    ).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$xml.LoadXml('{xml}')\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"'{APP_ID_POWERSHELL}').Show($toast)\n"
    )
    codificado = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", codificado]


def notificar(
    titulo: str,
    texto: str,
    relatorio: Path,
    *,
    executar: Callable[..., Any] = subprocess.run,
    janela: Callable[[Path], None] = agendamento.abrir_janela_resumo,
) -> bool:
    """Mostra a notificação; se o Windows recusar, abre o relatório numa
    janela, como o resumo do backup. Devolve se a notificação saiu."""
    try:
        processo = executar(
            comando_notificacao(titulo, texto),
            capture_output=True, timeout=60, check=False, creationflags=CREATE_NO_WINDOW,
        )
        if processo.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    janela(relatorio)
    return False


def main(
    argv: Sequence[str] = (),
    *,
    obter_agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    servidores: Callable[[], Mapping[str, Mapping[str, str]]] = configuracao.servidores_vps,
    avaliar_: Callable[..., Veredito] = avaliar,
    avisar: Callable[[str, str, Path], bool] = notificar,
    arquivo_veredito: Path = ARQUIVO_VEREDITO,
) -> int:
    analisador = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analisador.add_argument(
        "--testar-notificacao", action="store_true",
        help="mostra uma notificação de exemplo e sai, sem consultar o VPS",
    )
    args = analisador.parse_args(argv)

    if args.testar_notificacao:
        exemplo = (
            "Teste do vigia: se esta notificação apareceu e ficou na tela, o canal funciona."
        )
        # Arquivo à parte: o teste não pode sobrescrever o último veredito real.
        arquivo_teste = arquivo_veredito.with_name("vigia-teste.txt")
        agendamento._gravar_texto(arquivo_teste, exemplo + "\n")
        return 0 if avisar("BackupRestore — teste do vigia", exemplo, arquivo_teste) else 1

    veredito = avaliar_(obter_agora(), servidores=servidores())
    relatorio = montar_relatorio(veredito)
    agendamento._gravar_texto(arquivo_veredito, relatorio)
    if sys.stdout is not None:
        agendamento._imprimir(relatorio)
    if veredito.em_dia:
        return 0
    avisar(*mensagem(veredito), arquivo_veredito)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
