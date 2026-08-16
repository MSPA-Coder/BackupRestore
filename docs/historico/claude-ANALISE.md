> **Histórico.** Análise que levou ao desenho de `claude-PLANO.md`, que por
> sua vez diverge do que foi implementado em alguns pontos (ver banner
> daquele arquivo). Valor de referência: o *porquê* de várias decisões. Não
> é lido pelo código. Movido para `docs/historico/` em 2026-08-15.

# BackupRestore — análise da especificação

Revisão de `ESPECIFICACAO.md` e do protótipo (`index.html`, `app.js`, `styles.css`),
confrontados com o ambiente real dos 4 projetos em 2026-08-11.

Resumo: a especificação está bem escrita, mas foi desenhada contra um ambiente que
não é o seu. Três premissas centrais não se sustentam, e o conjunto de telas custa
mais código do que o backup em si.

---

## 1. Os bancos não estão em `localhost` — estão parados

**O problema mais sério.** O protótipo mostra `localhost:5432 / controle_bancario`, e a
especificação fala em "conexão PostgreSQL" como se houvesse um servidor escutando.

Na prática, os 4 Postgres são contêineres que passam a maior parte do tempo `Exited`:

| Projeto | Contêiner | Porta publicada | Estado normal |
|---|---|---|---|
| ConfortoTermico | `conforto-termico-postgres-1` | 127.0.0.1:5432 | parado |
| MegaSena | `mega-sena-postgres-1` | 127.0.0.1:5433 | parado |
| ControleBancario | `sistema-financeiro-postgres-1` | 127.0.0.1:5434 | parado |
| ControleRendaVariavel | `controle-renda-variavel-db-1` | 127.0.0.1:5435 | parado |

Um aplicativo que abre conexão em `localhost:PORT` vai falhar em todos, quase sempre,
porque não há nada escutando. O backup precisa **subir o contêiner, dumpar e devolver
ao estado anterior** — foi o que fiz manualmente para gerar os dumps de hoje.

Correção: falar com o Docker, não com a porta.

```
docker start <container>          # só se estava parado
docker exec <container> pg_isready # esperar ficar pronto
docker exec <container> pg_dump ...
docker stop <container>           # devolver ao estado anterior
```

**Efeito colateral bom:** rodando `pg_dump` *dentro* do contêiner, a versão do
`pg_dump` é sempre a mesma do servidor. Se o app rodasse `pg_dump` do lado de fora,
qualquer upgrade de Postgres num projeto quebraria o backup com
`server version mismatch`.

---

## 2. Duas coisas diferentes chamadas "segredo"

A especificação prevê "guardar localmente os parâmetros e segredos necessários para
conectar aos bancos". Vale separar dois usos que a frase mistura, porque a resposta é
oposta para cada um.

### 2a. Para *fazer* o backup: não precisa de senha nenhuma

Via `docker exec`, a imagem `postgres:17-alpine` confia em conexões por socket local.
Os cinco dumps de hoje saíram com `docker exec <container> pg_dump -U <user> -d <db>`,
sem `PGPASSWORD`, sem ler `.env`, sem nada.

Logo, **um cofre de credenciais dentro do aplicativo não se justifica**: seria uma
segunda cópia de senhas vivas, que envelhece sozinha quando você troca a senha no
projeto, e que precisa ser protegida — tudo isso para um dado que a operação não usa.
O que sobra para configurar por projeto é uma linha: contêiner, usuário, banco.

### 2b. Para *reconstruir do zero*: precisa, e o `git bundle` não os carrega

Cenário de perda total. Verifiquei arquivo por arquivo — **todos os segredos são
gitignored**, ou seja, ficam de fora do bundle *e* de qualquer ZIP que respeite as
regras do Git (como a própria especificação exige, na linha "arquivos ignorados pelo
Git, inclusive `.env`, não entrarão no ZIP"):

| Projeto | Fora do bundle |
|---|---|
| ConfortoTermico | `.env.docker`, `.secrets/`, `.certs/` |
| ControleBancario | `.env.docker`, `.certs/` |
| ControleRendaVariavel | `.env`, `.docker-local/` |
| MegaSena | `.env.docker`, `.certs/` |

Restaurar só o bundle devolve o código e nada mais: `docker compose up` falha de
imediato, porque `compose.yaml` declara `secrets: local_ca: file: .certs/local-root-ca.crt`
e o Compose exige que o arquivo exista para sequer construir a imagem.

**Correção:** o backup precisa de um terceiro artefato por projeto — uma cópia literal
dos arquivos de configuração não rastreados. São ~200 KB no total dos quatro projetos
(15K + 5K + 172K + 5K), contra os 12 MB dos bundles. Custo irrelevante.

Note que isso é diferente do cofre da especificação: não é um banco de credenciais que o
aplicativo consulta para operar, é carga inerte, escrita no backup e lida só num
desastre. Vida útil diferente, risco diferente.

---

## 3. Use `--format=custom`; o destino é o Dropbox

Os dumps que gerei hoje saíram em SQL puro. Comparação real, no maior deles
(ConfortoTermico, série temporal com 340 mil linhas):

| Formato | Tamanho |
|---|---|
| SQL puro (o que fiz hoje) | **94 MB** |
| comprimido | **7 MB** |

**13× de diferença**, e isso importa mais do que o normal porque a pasta de destino
(`Dropbox/BackpsDB`) sincroniza para a nuvem. Com retenção de 10 dumps, o
ConfortoTermico sozinho seria ~940 MB de tráfego e quota — contra ~70 MB.

`pg_dump --format=custom` já sai comprimido (zlib), então a especificação está certa ao
dizer que "dumps de banco não serão compactados em ZIP" — só falta dizer *qual* formato.
O `custom` também é o único que permite `pg_restore` seletivo e verificação barata
(`pg_restore -l arquivo` lê o índice e falha se estiver corrompido — resolve o item
"necessidade de verificar um dump" sem infraestrutura nenhuma).

**Isso já está implementado no seu código.** `ConfortoTermico/app/database_operacao.py:25`
tem `criar_backup_banco()` com `--format=custom --no-owner --no-privileges`, em 30 linhas.
É a referência a copiar — não vale reescrever do zero.

---

## 4. ZIP de código: `git bundle` faz o mesmo, menor e com histórico

A especificação descreve o fluxo mais complexo do sistema para o código: consultar o Git,
listar arquivos rastreados e não ignorados, gerar o ZIP, gerar um manifesto, validar.

Tamanhos reais:

| Projeto | `.git` (histórico completo) | árvore de trabalho |
|---|---|---|
| ConfortoTermico | 4,4 MB | 39 MB |
| ControleBancario | 4,7 MB | 401 MB |
| ControleRendaVariavel | 2,2 MB | 268 MB |
| MegaSena | 866 KB | 3,0 MB |

`git bundle create projeto.bundle --all` é **um comando**, gera **um arquivo**, e cabe
tudo em ~12 MB para os quatro projetos somados — com *todo* o histórico, branches e tags.
O ZIP guarda um único instantâneo e é maior. E ele restaura de verdade:
`git clone projeto.bundle`, o que resolve o item "restauração automática de código" que
a especificação teve de deixar fora do primeiro ciclo.

O manifesto também deixa de ser necessário: `git bundle verify` já valida o arquivo.

Contrapartida honesta: o ZIP dá para abrir e olhar no Explorer; o bundle não. Se essa
navegação importa, mantenha o ZIP. Se o objetivo é backup, o bundle é melhor em tudo.

---

## 5. A interface é a firula

Somando o que a especificação pede: Flask + SQLAlchemy + SQLite interno + Docker + SPA com
visão geral, catálogo, detalhe com abas, histórico, configurações, diálogo de confirmação,
métricas de espaço livre, tags de integridade.

Para 4 projetos, um usuário, sem agendamento (explicitamente fora de escopo) — o que
significa que **você precisa abrir o navegador e clicar para que qualquer backup aconteça**.

O que produz valor aqui cabe em ~60 linhas: para cada projeto, subir o contêiner se
preciso, dumpar, empacotar o git, apagar os mais antigos, devolver o contêiner ao estado
anterior, anexar uma linha num log. Fiz exatamente isso hoje em ~30 linhas de shell.

**Recomendação:** troque o aplicativo web por um script + Agendador de Tarefas do Windows.
Você ganha a única coisa que a especificação deixou de fora e que de fato importa num
backup — ele rodar sozinho — e perde apenas telas.

O histórico vira um `backups.log` de uma linha por operação. O "painel" vira a própria
pasta de destino, que o Explorer já sabe listar com data e tamanho.

Se ainda quiser interface depois, o caminho barato é uma página estática só de leitura
listando os arquivos. Mas note que a operação onde uma confirmação visual teria valor
real — a restauração — é rara e destrutiva; um comando documentado no README é mais
seguro do que um botão, justamente por exigir atenção.

---

## 6. Pontos em aberto — respostas do ambiente real

A especificação lista seis "pontos ainda em definição". Cinco já têm resposta:

- **Pasta central:** você decidiu hoje — `C:\Users\MSPA\Dropbox\BackpsDB`.
- **Cadastro manual ou descoberta automática?** Manual. A descoberta é traiçoeira aqui:
  o ControleBancario tem **dois** volumes Postgres (`controle-bancario_postgres_data`,
  antigo, e `sistema-financeiro_postgres_data`, o atual) e o nome do projeto Compose
  (`sistema-financeiro`) não bate com o nome da pasta (`ControleBancario`). Nenhuma
  heurística acerta isso sozinha. Uma lista explícita de 4 itens é mais curta que o código
  que tentaria adivinhá-la.
- **Verificação do dump:** `pg_restore -l arquivo`. Um comando, sem infraestrutura.
- **Restauração:** com `--format=custom`,
  `pg_restore --clean --if-exists --no-owner -d <banco>`. Como os dumps já saem com
  `--no-owner --no-privileges`, não há dependência de roles do servidor de origem.
- **Projetos que não são repositórios Git:** o próprio BackupRestore não é (`git status`
  falha na pasta). Trate como: sem `.git`, pula o backup de código e faz só o banco.

O único que continua em aberto é a **retenção padrão**, e ela depende de quota do Dropbox,
não de técnica. Com `--format=custom`, 10 dumps dos 4 projetos ficam na casa de ~100 MB —
provavelmente irrelevante. Sugiro 10 e seguir em frente.

---

## 7. Correções pontuais

- **`instance/*.db` são mesmo legados** — confirmado: `dados_entrada.db` e `historico.db`
  do ConfortoTermico estão parados em 25/jul, enquanto a aplicação roda em Postgres desde
  então. A especificação acertou ao descartá-los. (Copiei os dois no backup de hoje por
  precaução; podem ser ignorados daqui em diante.)
- **A mesma pasta `instance/` acumula lixo**: 9 arquivos
  `conforto_termico_backup_2026...dump` de 39 KB gerados em sequência em 08/ago — quase
  certamente resíduo da suíte de testes, que exercita `criar_backup_banco()` sem limpar.
  Vale um `.gitignore`/cleanup lá, e é um argumento a mais para a retenção ser aplicada
  pelo próprio código que escreve o arquivo.
- **Backup dentro do Dropbox**: dá offsite e versionamento de 30 dias de graça, o que é
  bom. Mas os projetos *também* estão no Dropbox — um acidente na conta atinge origem e
  cópia juntos. Não é motivo para mudar agora; é motivo para, em algum momento, ter uma
  cópia fora dele.
- **Protótipo**: o painel exibe "ESPAÇO LIVRE 981,6 GB", "38 dumps / 12,8 GB",
  "5,6 GB em pacotes". Com `--format=custom` e `git bundle`, os números reais ficam duas
  ordens de grandeza abaixo — mais um sinal de que o painel resolve um problema de escala
  que você não tem.

---

## 8. Reconstrução do zero — o teste que importa

Máquina nova, Docker vazio, só a pasta de backup na mão. É o cenário que define se o
backup presta, e nenhum dos três artefatos sozinho basta: o dump tem os dados, o bundle
tem o código, e a config tem o que faz os dois se encontrarem.

**O que é de fato insubstituível é pouco.** Verifiquei:

| Segredo | Regenerável? | Custo de perder |
|---|---|---|
| `POSTGRES_PASSWORD` | **Sim** — você inventa outra ao recriar o contêiner. Os dumps saem com `--no-owner --no-privileges`, então restauram sob qualquer role. | nenhum |
| `DJANGO_SECRET_KEY`, `SECRET_KEY` | **Sim** — não há nada cifrado nem assinado em repouso (procurei por `Fernet`, `itsdangerous`, `signing.dumps`: nenhuma ocorrência) | sessões e cookies expiram; você loga de novo |
| `.certs/local-root-ca.crt` | **Sim** — `scripts/export_local_ca.ps1` (MegaSena) e `scripts/exportar_ca_local.ps1` (ConfortoTermico), ambos rastreados no Git | nenhum |
| `internal_token.txt` | **Sim** — token compartilhado entre `coletor` e `ict`, recriados juntos | nenhum |

Ou seja: em teoria dá para reconstruir tudo sem guardar segredo algum. Mas "regenerável em
tese" e "recuperável às 2h da manhã com tudo quebrado" são coisas diferentes — você teria
de redescobrir quais variáveis cada projeto exige, em que arquivo, com que nome de banco e
de usuário. Copiar 200 KB de texto elimina esse passo inteiro. **Guarde os arquivos.**

O valor da tabela acima é outro: ela diz que **nenhum segredo é irrecuperável**, então se a
cópia de config falhar ou ficar velha, você ainda reconstrói — só com mais trabalho.
O backup não tem ponto único de falha.

**Ordem da restauração:**

1. Docker + Git instalados.
2. `git clone projeto.bundle ProjetoX` — devolve código e histórico completos.
3. Copiar `.env*`, `.secrets/`, `.certs/`, `.docker-local/` do backup para dentro da pasta.
4. `docker compose up -d postgres` — sobe só o banco, com volume vazio.
5. `docker exec -i <container> pg_restore --clean --if-exists --no-owner -U <user> -d <db> < dump`
6. `docker compose up` — aplicação sobe sobre o banco já populado.

O passo 4 antes do 6 importa: subir tudo de uma vez faz as migrações criarem um esquema
vazio que o `pg_restore` vai derrubar em seguida. Funciona, mas restaurar sobre o banco
limpo é mais previsível.

**Uma consequência a decidir conscientemente:** guardar os `.env` no backup coloca senhas
em texto claro dentro de `Dropbox/BackpsDB` — que sincroniza para a nuvem. Hoje elas só
existem no disco local, protegidas pelo `.gitignore`. Como são senhas de desenvolvimento,
presas a `127.0.0.1`, e todas regeneráveis pela tabela acima, aceitar isso é uma escolha
defensável — mas que seja escolha, não descuido. Se incomodar, o remédio barato é um único
`.7z` com senha só para a pasta de config, sem tocar em dumps nem bundles.

---

## Proposta

Substituir o aplicativo web por:

1. Um script (`backup.py`) com uma lista de 4 projetos — contêiner, usuário, banco, pasta.
2. Por projeto, **três artefatos**: sobe contêiner se parado →
   `pg_dump --format=custom` (dados) → `git bundle --all` (código e histórico) →
   cópia de `.env*`, `.secrets/`, `.certs/`, `.docker-local/` (config) → apaga além dos
   10 mais recentes → devolve o contêiner ao estado anterior → 1 linha no log.
3. Uma tarefa no Agendador do Windows.
4. Um `RESTAURAR.md` com os 6 passos da seção 8.

Mantém tudo que a especificação lista em "Escopo inicial", exceto as telas — e ganha
execução automática, que era o que faltava.

Vale rodar a restauração completa uma vez, numa pasta descartável, antes de confiar nela.
É o único jeito de saber se a cópia de config está completa — e o motivo de ela existir.

Se preferir manter o Flask, as seções 1 a 4 valem do mesmo jeito: são sobre *como* falar
com os bancos, não sobre ter ou não interface.
