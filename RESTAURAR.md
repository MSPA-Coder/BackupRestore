# Como restaurar

Procedimento verificado em 2026-08-11 nos quatro projetos. Os comandos abaixo
foram executados de verdade, não são exemplo.

Nada aqui depende do BackupRestore estar funcionando. Se sobrou a pasta de
backup e você tem Git e Docker, isto basta.

---

## Onde estão as coisas

```
<raiz-configurada>\projects\<projeto>\
    banco\            <projeto>_banco_<data>.dump      (pg_dump --format=custom)
    codigo\           <projeto>_codigo_<data>.zip      (arquivos não ignorados pelo Git)
    pre_restauracao\  <projeto>_seguranca_<data>.dump  (dumps automáticos pré-restauração)
```

A raiz é definida pelo operador com `python cli.py configurar-raiz` e fica em
`configuracao.local.json` (fora do Git) — confira ali qual é a atual antes de
procurar os arquivos. A interface web apenas a exibe; não pode alterá-la.

Cada artefato tem um `.manifest.json` ao lado com SHA-256, tamanho e origem.
Os `<projeto>` locais são `conforto_termico`, `mega_sena`, `controle_bancario`
e `controle_renda_variavel`.

**Os quatro projetos `_vps` são a mesma coisa, com uma diferença:** o dump vem
do VPS, buscado e verificado pela Camada 2 (`vps.py`/`cli.py sincronizar-vps`)
— não existe pasta `codigo\` para eles, porque o código do VPS é espelho do
`main` no GitHub (não um artefato deste sistema; ver "Reconstrução completa"
abaixo). `<projeto>` vira `conforto_termico_vps`, `mega_sena_vps`,
`controle_bancario_vps` e `controle_renda_variavel_vps`. O `.manifest.json` de
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
docker compose up -d postgres
```

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
projetos de origem VPS (D4 do plano de backup do VPS: o código de produção já
é espelho verificado do `main`, então o próprio Git é a fonte, não uma cópia
adicional aqui).

**1. Código** — clone o repositório do GitHub no commit certo. O
`.manifest.json` do dump não tem essa informação (ele só descreve o próprio
banco); confira o `HEAD` da produção no momento da captura com:

```bash
ssh -i <chave-dedicada> ubuntu@<host-vps> estado
```

(mostra o último backup e a saúde do timer, não o `HEAD` do Git — se precisar
do commit exato que estava em produção, isso é registrado à parte, fora do
escopo deste kit; veja `docs/deployment-vps.md` de cada projeto.)

**2 a 4.** Iguais aos passos acima, com o dump `_vps` no lugar do dump local.
Os segredos e certificados do lado do VPS **não são os mesmos arquivos do lado
local** — veja a seção "VPS (produção)" em
[KIT_RECUPERACAO.md](KIT_RECUPERACAO.md) antes de configurar o destino.

---

## Conferir um artefato antes de confiar nele

O host não tem `pg_restore` — e não precisa ter. Qualquer contêiner
`postgres:17-alpine` serve de ferramenta:

```bash
docker exec -i backuprestore-sandbox pg_restore --list < arquivo.dump
python -c "import zipfile; print(zipfile.ZipFile(r'arquivo.zip').testzip())"
```

Ambos falham em arquivo truncado ou com bytes trocados; foi medido.

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

**Para um projeto `_vps`, o mesmo comando restaura de verdade no sandbox, mas
não compara com a origem automaticamente** — a produção está noutra máquina, e
o agente do VPS só sabe quatro verbos (`listar`/`enviar`/`apagar`/`estado`),
de propósito sem consulta SQL (ver D1 do plano de backup do VPS). O comando
mostra as tabelas e linhas restauradas; para bater com a produção, é
conferência manual do operador (chave de administração, `docker exec
<container> psql ...` no VPS — nunca a chave restrita da Camada 2).

**Restaurar sobre um projeto real não é possível pela ferramenta**, local ou
VPS. Os contêineres reais estão protegidos por par `(ambiente, contêiner)` em
`CONTAINERS_PROTEGIDOS` (`projetos.py`) e `restaurar.py` os recusa antes de
ler qualquer coisa — não há flag que libere, e não existe caminho no código
que restaure no VPS por SSH. Fazer isso é trabalho manual, consciente, com o
passo 4 da seção acima que corresponder à origem do dump.

---

## O que o backup não cobre

- **Arquivos ignorados.** O ZIP segue o `.gitignore`; arquivos como `.env` ficam
  fora quando forem ignorados pelo projeto.
- **Os volumes Docker em si.** Restaura-se o *conteúdo* dos bancos via dump, não
  o volume. É o que se quer: dump é portável entre versões e máquinas.
- **Arquivos ignorados pelo Git.** O backup de código respeita as regras do
  projeto. Se uma reconstrução exigir `.env`, certificados ou outros segredos,
  eles devem ser configurados conscientemente no destino; não entram no ZIP por
  padrão.
- **Segredos do VPS.** Nunca entram em artefato nenhum — cópia manual para o
  cofre, sempre (ver a seção "VPS (produção)" do kit).
- **Código dos projetos `_vps`.** Não é backup deste sistema; é o `main` do
  GitHub, imposto pelo `deploy.sh` do servidor.
- **Ponto no tempo do VPS.** Um dump é uma fotografia do momento em que o
  servidor o produziu (ver o `.manifest.json`), não um estado contínuo — não
  há arquivamento de WAL para voltar a um instante específico entre dois
  dumps.
- **Monitoramento.** Nada aqui avisa em tempo real se o VPS parar de produzir
  backup; `python cli.py sincronizar-vps --todos` (ou a tarefa agendada
  `BackupRestoreVPS`) é como se descobre, não uma notificação ativa.

Veja [KIT_RECUPERACAO.md](KIT_RECUPERACAO.md) para o inventário operacional.
