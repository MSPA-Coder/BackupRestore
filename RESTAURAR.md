# Como restaurar

Este é o procedimento vivo de recuperação. Antes de uma emergência, valide-o
periodicamente no sandbox e confira o `README.md` e o `compose.yaml` da versão
do projeto que será restaurada.

Nada aqui depende do BackupRestore estar funcionando. Se sobrou a pasta de
backup, use Docker, Git e uma ferramenta para extrair ZIPs; o exemplo abaixo
usa Python para a extração.

---

## Onde estão as coisas

```
<raiz-configurada>\projects\<projeto>\
    banco\            <projeto>_banco_<data>.dump      (pg_dump --format=custom)
    codigo\           <projeto>_codigo_<data>.zip      (arquivos não ignorados pelo Git)
    volume\           <slug>_volume_<data>.tar.gz      (diretório de dados de um contêiner, só VPS)
    pre_restauracao\  <projeto>_seguranca_<data>.dump  (dumps automáticos pré-restauração)
```

A raiz efetiva segue a precedência documentada no `README.md`. Quando o
operador executa `python cli.py configurar-raiz`, o destino fica em
`configuracao.local.json` (fora do Git); sem configuração ou ambiente, vale o
padrão portátil irmão de `VSCodeProjects`. A interface web apenas exibe o
destino efetivo; não pode alterá-lo.

Cada artefato tem um `.manifest.json` ao lado com SHA-256, tamanho e origem.
Os `<projeto>` locais são `conforto_termico`, `mega_sena`, `controle_bancario`
e `controle_renda_variavel`.

**Os projetos `_vps` são a mesma coisa, com uma diferença:** o dump vem
do VPS, buscado e verificado pela Camada 2 (`vps.py`/`cli.py sincronizar-vps`)
— não existe pasta `codigo\` para eles, porque o código do VPS é espelho do
`main` no GitHub (não um artefato deste sistema; ver "Reconstrução completa"
abaixo). `<projeto>` vira `conforto_termico_vps`, `mega_sena_vps`,
`controle_bancario_vps`, `controle_renda_variavel_vps`, `wealthfolio_teste_vps`
e `mp_portal_vps` — este último vem do VPS dedicado do portal. O Wealthfolio
não tem dump: tem `volume\`, com o procedimento próprio descrito em "Wealthfolio a
partir da cópia do volume", abaixo. O `.manifest.json` de
um dump VPS tem `"origem": {"servidor": ..., "arquivo_remoto": ...}` em vez de
`{"container": ..., "banco": ...}` — é assim que se distingue um artefato
produzido aqui de um buscado de lá.

---

## Reconstrução completa, do zero

O ZIP permite recuperar o código rastreado e o estado Git registrado no
manifesto. A reconstrução completa também depende dos arquivos externos
enumerados em "O que o backup não cobre". Antes de uma emergência, mantenha o
[kit externo de recuperação](KIT_RECUPERACAO.md) em cofre ou mídia cifrada
independente da raiz de backup.

**1. Código**

```bash
python -c "import zipfile; zipfile.ZipFile(r'<backup>\projects\<projeto>\codigo\<arquivo>.zip').extractall(r'<PastaDoProjeto>')"
```

Isso restaura só o que o Git rastreava e não ignorava — `.env`, certificados e
outros segredos ficam fora (ver "O que o backup não cobre" abaixo) e precisam
ser configurados conscientemente no destino antes do próximo passo.

**2. Subir só o banco**, com o volume vazio:

```bash
docker compose up -d <servico-postgres>
```

O serviço se chama `postgres` em Controle Bancário, Mega-Sena, Conforto
Térmico e MP Portal, e `db` em Controle de Renda Variável.

Antes do resto: se a aplicação subir junto, as migrações criam um esquema vazio
que o `pg_restore` vai derrubar em seguida. Funciona, mas restaurar sobre banco
limpo é mais previsível.

**3. Restaurar os dados**

```bash
docker exec -i <container-postgres> pg_restore --no-owner --no-acl --exit-on-error -U <usuario> -d <banco> < <arquivo>.dump
```

Contêineres e usuários de cada projeto estão em `projetos.py`.

**4. Subir a aplicação**

```bash
docker compose up -d
```

---

## Reconstrução completa a partir de um dump `_vps`

Mesmo procedimento acima, com uma troca no passo 1: **não existe ZIP de
código para clonar** — não é um artefato que este sistema produz para
projetos de origem VPS. O código de produção é espelho do `main`, então o
próprio Git é a fonte, não uma cópia adicional aqui.

**1. Código** — clone o repositório e parta do `main`. O `.manifest.json` do
dump descreve o banco e não incorpora o SHA do código. Se o kit preservou
`/home/ubuntu/.local/state/mspa-deploy/<projeto>.commit` — ou um registro
externo equivalente — use-o para identificar o commit saudável mais recente
registrado pelo deploy. Esse registro ajuda na reconstrução, mas não prova que
o dump e o commit foram capturados exatamente no mesmo instante.

Trate código e schema como um conjunto compatível. Voltar o checkout para um
commit anterior não desfaz migrations já aplicadas. Para rollback, use um dump
compatível restaurado em banco limpo e siga o procedimento de migrations da
versão escolhida; não tente “reverter” o banco apenas trocando o código.

**2 a 4.** Iguais aos passos acima, com o dump `_vps` no lugar do dump local.
Os segredos e certificados do lado do VPS **não são os mesmos arquivos do lado
local** — veja a seção "VPS (produção)" em
[KIT_RECUPERACAO.md](KIT_RECUPERACAO.md) antes de configurar o destino.

---

## Wealthfolio a partir da cópia do volume

O Wealthfolio guarda tudo num diretório (`/data` no contêiner, volume
`wealthfolio-teste-data`): o SQLite cifrado, o `-wal` dele e o cofre de
segredos, onde ficam os tokens do add-on para o CB e o CRV. O VPS copia esse
diretório todo dia com o contêiner pausado por segundos
(`_manutencao/vps/backup-db.sh`), e o `sincronizar-vps` traz a cópia para
`projects\wealthfolio_teste_vps\volume\`.

**Sem a chave mestra, a cópia não abre.** O banco e o cofre são cifrados com o
arquivo apontado por `WF_SECRET_KEY_HOST_PATH` (ver
[KIT_RECUPERACAO.md](KIT_RECUPERACAO.md)). Confira que o kit tem a chave antes
de qualquer outro passo.

**1. Imagem.** O `compose.yaml` do WealthfolioTeste constrói a imagem de uma
pasta fora do Git (`../CodexTemp/wealthfolio-v3.9.1`). Num servidor novo,
baixe o Wealthfolio `v3.9.1` nesse caminho e aplique
`scripts/apply-upstream-patches.sh` antes do `docker compose build`.

**2. Configuração.** Recoloque o `.env` e a chave mestra do kit, com o
`WF_SECRET_KEY_HOST_PATH` apontando para a chave.

**3. Volume.** Com o serviço parado (`docker compose stop wealthfolio`, ou ainda
não criado), copie o `.tar.gz` para o servidor e extraia no volume. O arquivo
tem o diretório `data/` na raiz, como o `docker cp` o produziu:

```bash
docker volume create wealthfolio-teste-data
docker run --rm -v wealthfolio-teste-data:/data -v "$PWD:/copia:ro" alpine:3 \
    sh -c 'find /data -mindepth 1 -delete && tar -xzf /copia/<arquivo>.tar.gz -C /'
```

O `find ... -delete` esvazia o volume antes: sobra de um banco anterior junto
com o `-wal` da cópia é exatamente a mistura que a pausa existe para evitar.

**4. Subir e conferir.** `docker compose up -d` (no VPS, com o
`compose.patrimonio-internal.yaml`, como faz o `deploy.sh`), entrar com a senha
de sempre e conferir contas, histórico e a tela do add-on. Se o CB ou o CRV
tiverem trocado o `patrimonio_integration_token` depois da data da cópia, cole
o valor atual na tela do add-on.

---

## Conferir um artefato antes de confiar nele

O host não tem `pg_restore` — e não precisa ter. Qualquer contêiner
`postgres:17-alpine` serve de ferramenta:

```bash
docker exec -i backuprestore-sandbox pg_restore --list < arquivo.dump
python -c "import zipfile; print(zipfile.ZipFile(r'arquivo.zip').testzip())"
tar -tzvf arquivo.tar.gz
```

Os três comandos precisam terminar sem erro antes que o artefato seja aceito.
O `tar` vem com o Windows 10 em diante; o `verificar` faz a mesma releitura da
cópia de volume em Python, sem Docker.

Para conferir tudo de uma vez contra o SHA-256 do catálogo:

```bash
python cli.py verificar
```

---

## Ensaiar sem risco

O sandbox (`compose.teste.yaml`) é um PostgreSQL descartável na porta 5439. O
comando abaixo restaura o dump mais recente lá dentro e compara as contagens de
linha com o projeto original:

```bash
python cli.py ensaio --projeto conforto_termico
```

Isso ensaia **só os bancos**. O ensaio de reconstruir o servidor inteiro, com
provisionamento, segredos, clone, deploy e contrato entre os sistemas, sem
custo e numa distro WSL descartável, está em
[docs/ROTEIRO_ENSAIO_RTO.md](docs/ROTEIRO_ENSAIO_RTO.md); o resultado do
primeiro ensaio (parcial) está em
[docs/ENSAIO_RTO_2026-10-03.md](docs/ENSAIO_RTO_2026-10-03.md).

**Para um projeto `_vps`, o mesmo comando restaura de verdade no sandbox, mas
não compara com a origem automaticamente** — a produção está noutra máquina, e
o agente do VPS só sabe quatro verbos (`listar`/`enviar`/`apagar`/`estado`),
de propósito sem consulta SQL. O comando
mostra as tabelas e linhas restauradas; para bater com a produção, é
conferência manual do operador (chave de administração, `docker exec
<container> psql ...` no VPS — nunca a chave restrita da Camada 2).

**A restauração automática aceita exclusivamente `backuprestore-sandbox`.**
`restaurar.py` recusa qualquer outro nome antes de ler o catálogo, consultar o
Docker ou criar o dump de segurança — não há flag que libere, e não existe
caminho no código que restaure no VPS por SSH. Restaurar em outro destino é
trabalho manual, consciente, com o passo 3 da seção acima que corresponder à
origem do dump.

---

## O que o backup não cobre

- **Arquivos ignorados.** O ZIP segue o `.gitignore`; arquivos como `.env` ficam
  fora quando forem ignorados pelo projeto.
- **Os volumes Docker em si.** Restaura-se o *conteúdo* dos bancos via dump, não
  seus volumes. Dumps são portáveis entre versões e máquinas, mas outros
  volumes precisam de uma estratégia própria. A exceção é o do Wealthfolio,
  que não tem banco à parte: ele é copiado inteiro (`volume\`, acima), e é a
  aplicação que declara isso, com o rótulo `mspa.backup.volume` no Compose.
- **Comprovantes do Controle Bancário.** Os arquivos ficam em `media_volume`;
  o dump contém referências do banco, não o conteúdo enviado. Preserve-os pela
  cópia independente descrita no kit.
- **Segredos do VPS.** Nunca entram em artefato nenhum — cópia manual para o
  cofre, sempre (ver a seção "VPS (produção)" do kit).
- **Código dos projetos `_vps`.** Não é backup deste sistema; é o `main` do
  GitHub, imposto pelo `deploy.sh` do servidor.
- **Ponto no tempo do VPS.** Um dump é uma fotografia do momento em que o
  servidor o produziu (ver o `.manifest.json`), não um estado contínuo — não
  há arquivamento de WAL para voltar a um instante específico entre dois
  dumps.
- **Quanto se perde (RPO) é a idade do artefato mais recente no catálogo
  LOCAL, não a do último `backup-db.sh`.** O servidor produz o dump às 03:00
  (São Paulo) e a tarefa `BackupRestoreVPS` o busca às 03:30, mas se o PC
  estiver desligado ou a busca falhar, as cópias mais novas ficam só no VPS —
  exatamente o que se perderia junto com ele. No ensaio de 03/10/2026 o
  catálogo local estava cerca de 11 horas atrás (e cerca de 4 horas atrás do
  que o próprio VPS já tinha). Para saber o RPO de hoje, compare
  `python cli.py listar` com o `estado` do agente do VPS.
- **Monitoramento.** Nada aqui avisa em tempo real se o VPS parar de produzir
  backup; `python cli.py sincronizar-vps --todos` (ou a tarefa agendada
  `BackupRestoreVPS`) é como se descobre, não uma notificação ativa.

Veja [KIT_RECUPERACAO.md](KIT_RECUPERACAO.md) para o inventário operacional.
