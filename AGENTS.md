# BackupRestore — guia de trabalho

<!-- comum: início. Cópia de _manutencao/compartilhados/AGENTS-comum.md; mude lá e rode sincronizar.py --aplicar. -->
## Regras comuns aos repositórios

**O que espera o "sim" do mantenedor:** deploy ou publicação, qualquer ação no
VPS, e apagar dados, volumes ou backups (`docker compose down -v` inclusive).
Commit e push não esperam. Na dúvida sobre algo irreversível, pergunte; no
resto, decida e diga o que decidiu.

**Ciclo.** Altere, rode a verificação completa deste repositório (seção
"Validação"), commite direto no `main` quando o bloco de trabalho estiver
pronto e dê `git push`: sem PR, sem push forçado, sem rebase do `main`
publicado. A CI roda depois, só como aviso, mas vermelho no `main` se
investiga. O Dependabot chega por PR, com merge manual. Ao terminar, diga o que
rodou, o resultado e o que deixou de validar.

**Dados e segredos.** Os bancos desta máquina são de teste: não provam nada
sobre valores reais e não pedem backup. Segredos ficam fora do Git (`.secrets/`
ou configuração do usuário) e não aparecem em código, imagem, log, diff,
documento nem relatório.

**Dependências.** Alargue o teto e preserve o piso já verificado: elevar o piso
afirma uma incompatibilidade, e isso exige prova. Vulnerabilidade se corrige
atualizando; exceção só explícita, com motivo e data. O Dependabot propõe
atualização do `uv.lock` por PR (PRs de 08 e 09/11/2026), mas não se confirmou
que o grafo de dependências do GitHub acompanha o lock (última conferência:
parado no lock de 13/10/2026): não conte só com o alerta do GitHub, confira
pela versão travada no repositório e atualize à mão
(`uv lock --upgrade-package <pacote>`, onde há `uv.lock`) quando o `pip-audit`
ou o Trivy apontarem.

**Testes.** `docs/TESTES.md`, onde existe, diz o que testar e como. Diante de
vermelho, decida de quem é o defeito; nunca escreva código para um teste
passar. Não rode duas verificações do mesmo repositório ao mesmo tempo: a
segunda recria o ambiente de teste e derruba a primeira.

**Cópias.** Este bloco, `docs/TESTES.md` e os arquivos listados em
`_manutencao/compartilhados/sincronizar.py` são cópias: mude a canônica lá e
rode `python compartilhados/sincronizar.py --aplicar` no `_manutencao`.

**Este arquivo.** Guarde aqui só o que o código não diz: limites, decisões com
o motivo, armadilhas e comandos. Cada regra mora num lugar só: o que este bloco
ou `docs/` já dizem não se repete. História fica no `git log`; decisão com
alternativas vira ADR, que abre com a linha `Estado:` (vigente, ou substituído
por qual); detalhe de implementação fica no código. Para acrescentar uma
regra, retire ou funda
outra: `sincronizar.py` reprova este arquivo acima de 900 palavras fora deste
bloco.
<!-- comum: fim -->

## O sistema

Roda no host Windows e coordena backup e ensaio de restauração dos projetos
web locais, além de buscar e catalogar os dumps que o VPS produz sozinho
(Camada 2). Não é containerizado porque isso exigiria dar a um
contêiner acesso privilegiado ao daemon Docker.

- `README.md`: instalação, operação e formato dos artefatos;
- `RESTAURAR.md`: recuperação manual e limites do backup;
- `projetos.py`: os projetos (locais e de origem VPS, campos `ambiente` e
  `servidor`) e os contêineres protegidos;
- `motor.py` e `restaurar.py`: contratos de integridade e restauração locais;
- `reconstruir.py`: recria o catálogo a partir dos manifestos, relendo cada
  artefato; simula por padrão;
- `vps.py`: busca, verifica e cataloga dumps e cópias de volume dos VPS por
  SSH, através de um agente restrito no servidor
  (`listar`/`enviar`/`apagar`/`estado`). Nunca dispara `pg_dump` remoto nem
  toca em contêiner de produção.

O runtime é Python 3.15 no `.venv`; `iniciar.bat` escolhe, nesta ordem,
`BACKUPRESTORE_PYTHON`, `.venv\Scripts\python.exe` e o wrapper
`%LOCALAPPDATA%\Python\bin\python.cmd` (nunca o alias da Microsoft Store). Se o
runtime faltar, diagnostique e proponha instalação reproduzível; não instale
nada em silêncio no host.

## Validação

```powershell
.\scripts\quality.ps1
& .\.venv\Scripts\python.exe cli.py verificar
& .\.venv\Scripts\python.exe cli.py ensaio --projeto <slug>
```

O script (suíte `quality` e `audit`, passos em `scripts/quality.passos`) roda
antes de todo commit de código; `verificar` e `ensaio`, conforme a mudança:

- Catálogo e regras puras: a suíte basta.
- Backup, retenção, restauração ou seleção de alvo: a suíte, `verificar` e ao
  menos um `ensaio` real no sandbox. Mock não substitui o ensaio; testes
  pequenos protegem seleção de alvo, bloqueios, retenção e falhas antes dele.
- Sandbox: `docker compose -f compose.yaml config --quiet`, subir, esperar o
  health check e descartar só o volume do sandbox.
- Só documentação: confira links, comandos e o fluxo afetado, e rode
  `python ../_manutencao/compartilhados/sincronizar.py`.

## Contratos que toda mudança preserva

Em `motor.py`, `restaurar.py`, `banco.py`, `projetos.py` e `vps.py`:

1. Artefato nasce em diretório temporário e só ganha o nome final por troca
   atômica, depois de verificado.
2. Dump, ZIP e cópia de volume são relidos; código de saída zero não basta.
3. Retenção só depois de existir substituto válido, e nunca remove o último
   artefato válido de um tipo.
4. O estado original do contêiner volta em `finally`, inclusive em falha.
5. Restauração começa por dump de segurança verificado do destino.
6. O nome do banco é confirmado, e só `backuprestore-sandbox` pode ser destino
   de restauração automática, sem flag de bypass.
7. SHA-256, tamanho e origem continuam verificáveis pelo catálogo.

A raiz de backup e o seu limite são definidos só pelo operador no host
(`cli.py configurar-raiz` ou `BACKUPRESTORE_RAIZ_PERMITIDA`); a interface em
`127.0.0.1` só os lê. Caminho relativo do catálogo se resolve e se confirma sob
a raiz antes de ler, remover ou restaurar. `down -v` só no Compose descartável
`compose.yaml`, depois de confirmar o nome do projeto Compose.

Segredos, certificados e arquivos ignorados não entram no ZIP de código sem
uma estratégia aprovada de cifra, acesso e recuperação. Mudança de formato do
catálogo ou do artefato precisa de compatibilidade, migração ou rollback
explícito, e README/RESTAURAR acompanham quando operação ou recuperação mudam.
