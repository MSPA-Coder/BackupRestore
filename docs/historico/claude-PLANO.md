> **Histórico — parcialmente superado.** Registro de decisão válido para o
> *porquê* (host-only, sem senha guardada, retenção, `--format=custom`), mas
> diverge do que foi implementado: descreve o artefato de código como
> `git bundle` (virou `.zip`) e um terceiro artefato `config` como "não
> opcional" (foi removido antes do Release V1.0 — ver nota no topo de
> `motor.py`). `README.md`/`RESTAURAR.md` são a fonte de verdade sobre o
> comportamento atual. Movido para `docs/historico/` em 2026-08-15.

# BackupRestore — plano alinhado

Substitui as recomendações de `claude-ANALISE.md` e `claude-JUNCAO.md` (os dois seguem
válidos como registro das verificações feitas no ambiente). Alinhado a: Python + Flask +
SQLite, sem Docker, sem HTMX, sem suíte de testes, foco em **backup e restauração que
funcionam**, projeto que não deve crescer em features.

---

## Alinhamento

| Fica | Sai |
|---|---|
| Python 3.14 + Flask no host | Docker para o BackupRestore |
| SQLite (stdlib `sqlite3`) | SQLAlchemy, Alembic, migrações |
| Jinja + CSS/JS próprios | React, TypeScript, Vite, Tailwind, npm |
| Modelo de dados do MVP (jobs, artefatos, eventos) | Supabase e RLS |
| Telas do MVP (painel, projetos, detalhe, histórico) | HTMX, suíte pytest, CI, hooks, AGENTS.md |
| `docker exec` para falar com os bancos | `host:porta`, cofre de senhas |

**Por que sem Docker aqui:** o BackupRestore é a única ferramenta que *gerencia* os
contêineres dos outros. Colocá-lo dentro de um exigiria montar o socket do Docker — que o
próprio AGENTS.md dos outros projetos classifica como acesso privilegiado ao host — mais
bind mounts da pasta de projetos e do destino. Roda no host e o problema desaparece.

Confirmei que o host tem o necessário: Python 3.14.6, `git` e `docker` no PATH.
`pg_restore` **não** existe no host, e não precisa: `pg_dump` e `pg_restore` rodam dentro
do contêiner de cada projeto via `docker exec`, então a versão nunca desencontra da do
servidor e nada precisa ser instalado no host.

**Dependências:** só Flask. Todo o resto é stdlib (`sqlite3`, `subprocess`, `hashlib`,
`zipfile`, `pathlib`, `shutil`). Sem ORM: o schema tem 4 tabelas e não vai evoluir.

---

## Isto aqui é um seguro, não um aplicativo

Como o foco é segurança do backup e da restauração, é onde vale gastar o cuidado — e não
em arquitetura. As sete regras abaixo são o projeto; o resto é encanamento.

**1. Escrita atômica.** Todo artefato é escrito como `.tmp`, verificado, e só então
renomeado para o nome final. Um `pg_dump` interrompido no meio (máquina desligou, Docker
caiu) nunca pode deixar para trás um arquivo truncado com nome de dump bom. Rename é
atômico no mesmo volume; escrever direto no destino não é.

**2. Verificar antes de confiar.** Código de saída zero não prova nada. Cada artefato é
lido de volta antes de ser aceito:

- dump → `docker exec <ct> pg_restore -l <arquivo>` (lê o índice; falha se corrompido)
- bundle → `git bundle verify`
- config → `zipfile.ZipFile.testzip()`

Só depois de passar é que o artefato entra no catálogo como `valido`.

**3. Nunca apagar antes de ter o substituto.** A retenção roda **depois** da verificação do
artefato novo, nunca antes. E nunca remove o último artefato válido de um tipo, mesmo que
a retenção mande. Backup que apaga a cópia boa antes de garantir a nova é a forma mais
comum de perder tudo.

**4. Devolver o contêiner ao estado em que estava.** Registrar se estava rodando, e
restaurar isso em `finally` — inclusive quando o dump falha. Um backup noturno não pode
deixar quatro Postgres ligados até de manhã, nem derrubar um que você estava usando.

**5. Toda restauração começa por um dump.** Antes de qualquer `pg_restore`, dump de
segurança do estado atual, verificado pela regra 2, gravado como `pre_restore`. É a única
proteção contra restaurar o arquivo errado. Sem exceção, sem opção de pular.

**6. Restauração exige confirmação digitada.** O nome do banco de destino, digitado à mão.
Sem "tem certeza? [Sim]" — restauração é a única operação destrutiva do sistema e a
fricção é o recurso de segurança.

**7. SHA-256 gravado e reconferível.** Calculado na escrita, guardado no catálogo, e um
comando `verificar` que relê os arquivos e compara. Detecta corrupção silenciosa no
destino — que é real, ainda mais numa pasta que sincroniza com a nuvem.

E a regra que valida todas as outras: **um artefato só serve se você já restaurou a partir
dele pelo menos uma vez.** Está no fim da Fase 1 por isso.

---

## Estrutura

```
BackupRestore/
  projetos.py      # os 4 projetos: contêiner, usuário, banco, caminho  (dados)
  banco.py         # sqlite3: criar tabelas, gravar/consultar
  motor.py         # dump, bundle, config, verificação, retenção        (o núcleo)
  restaurar.py     # snapshot de segurança + pg_restore + confirmação
  cli.py           # python cli.py backup --todos   ← Agendador chama isto
  web.py           # Flask: telas e disparo                            (Fase 2)
  templates/  static/
  RESTAURAR.md     # procedimento manual, para quando nada funcionar
```

Seis arquivos Python. `motor.py` recebe o executor de subprocesso como parâmetro — mesmo
padrão que `ConfortoTermico/app/database_operacao.py:25` já usa (`executar: Callable = subprocess.run`),
o que deixa as partes delicadas conferíveis sem Docker ligado.

**Três artefatos por projeto**, na pasta central:

```
BackpsDB/conforto_termico/
  banco/   conforto_termico_banco_20260811_1430.dump     ← pg_dump --format=custom
  codigo/  conforto_termico_codigo_20260811_1430.bundle  ← git bundle --all
  config/  conforto_termico_config_20260811_1430.zip     ← .env*, .secrets/, .certs/
```

O terceiro não é opcional: sem `.certs/local-root-ca.crt` o `compose.yaml` dos projetos nem
constrói a imagem, e os `.env` são todos gitignored — o bundle não os carrega.

---

## Fase 1 — o motor (entrega backup de verdade)

Hoje não existe backup automatizado nenhum: o MVP no GitHub simula tudo
(`sleep(700ms)`, `sha256` aleatório). Esta fase resolve isso e vale sozinha, mesmo que a
Fase 2 nunca aconteça.

1. **`projetos.py`** — os 4 projetos como dados. Já levantei os valores:

   | projeto | contêiner | usuário | banco |
   |---|---|---|---|
   | ConfortoTermico | `conforto-termico-postgres-1` | `conforto` | `conforto_termico` |
   | MegaSena | `mega-sena-postgres-1` | `mega_sena` | `mega_sena` |
   | ControleBancario | `sistema-financeiro-postgres-1` | `controle_bancario` | `controle_bancario` |
   | ControleRendaVariavel | `controle-renda-variavel-db-1` | `investimentos` | `investimentos` |

2. **`banco.py`** — SQLite com 4 tabelas, herdadas do MVP e podadas: `projetos`,
   `artefatos` (tipo, situação, caminho, bytes, sha256, `fixado`, `finalidade`),
   `execucoes`, `eventos`. Sem migrações: uma função `criar_tabelas()` com `IF NOT EXISTS`.

3. **`motor.py`** — as regras 1 a 4 e 7. Por projeto: inspeciona o contêiner → inicia se
   parado → `pg_dump --format=custom --no-owner --no-privileges` → `git bundle --all` →
   zip da config → verifica os três → grava no catálogo → aplica retenção → devolve o
   contêiner ao estado anterior.

4. **`cli.py`** — `backup --todos`, `backup --projeto X`, `verificar`, `listar`.

5. **Agendador de Tarefas do Windows** apontando para `cli.py backup --todos`.

6. **O ensaio de restauração.** Numa pasta descartável: clonar o bundle, extrair a config,
   subir só o Postgres, restaurar o dump, subir a aplicação, abrir no navegador. É o único
   teste que interessa neste projeto — e o que prova que a cópia de config está completa.
   O que der errado aqui vira correção no motor e vira `RESTAURAR.md`.

**Fim da Fase 1:** backup rodando sozinho todo dia, com integridade verificada, e uma
restauração comprovada.

---

## Fase 2 — a interface

O motor já existe e é confiável; a tela só olha e dispara. Nada de execução no navegador
— foi exatamente o erro do MVP.

1. **`web.py`** — Flask com fábrica de aplicação, quatro rotas de página (painel, projetos,
   detalhe, histórico) e duas de ação (disparar backup, restaurar). Lê o mesmo SQLite que a
   CLI escreve.

2. **Telas**, portadas do MVP para Jinja — o desenho fica, a stack sai:
   - **Painel**: os 4 projetos, último backup de cada tipo, o que precisa de atenção.
   - **Detalhe**: abas banco / código / config, lista de artefatos com data, tamanho,
     situação e sha256, botão de fixar.
   - **Histórico**: execuções e eventos.
   - **Restauração**: a única tela com trabalho de verdade — escolher artefato, mostrar o
     destino em vermelho, exigir o nome do banco digitado (regra 6), avisar que um dump de
     segurança será feito antes (regra 5).

3. **Progresso**: o backup roda numa thread e grava fase e percentual na tabela
   `execucoes`; a página consulta a cada segundo com um `fetch` de ~15 linhas de JS. As
   fases são as reais (dump, bundle, config, verificação), não temporizadores.

4. **CSS e JS próprios**, sem build, no padrão de `ConfortoTermico/app/static/`
   (`css/style.css`, `js/features/*.js`). O MVP usa Tailwind, que não sobrevive à mudança —
   mas o layout e a hierarquia visual, sim.

**Fim da Fase 2:** o mesmo motor, agora com a tela que você gostou.

---

## O que não fazer

Registrado para a tentação não voltar depois:

- **Descoberta automática de projetos.** São quatro, escritos à mão em 20 linhas. E a
  heurística erraria: o ControleBancario tem dois volumes Postgres e o projeto Compose
  chama `sistema-financeiro`, não bate com a pasta.
- **Guardar senhas.** Via `docker exec` não são necessárias, e as que existem estão todas
  nos `.env` — que já vão no artefato de config.
- **Cifrar os artefatos.** Fora do escopo original e trabalha contra o objetivo: dump que
  você não consegue abrir num dia ruim não é backup.
- **Agendamento configurável pela interface.** O Agendador do Windows já faz isso.
- **Restauração de código automatizada.** `git clone arquivo.bundle` é um comando; embrulhar
  em botão só adiciona lugar para errar.

---

## Nota sobre o repositório no GitHub

O `MSPA-Coder/BackupRestore` atual não tem caminho de migração para isto — stack, execução
e banco são outros. O que se aproveita é desenho, não código: o modelo de dados
(especialmente `finalidade='pre_restore'` e o ciclo de vida de situação, que eu não tinha
proposto e são bons) e o layout das telas. Sugiro começar limpo e manter aquele repositório
como referência visual até a Fase 2 terminar.

---

Se estiver alinhado, começo pela Fase 1 — `projetos.py`, `banco.py`, `motor.py` e `cli.py`,
com as sete regras implementadas, e rodo o primeiro backup real dos quatro projetos.
