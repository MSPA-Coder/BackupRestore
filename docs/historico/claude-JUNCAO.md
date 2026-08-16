> **Histórico.** Registro intermediário entre `claude-ANALISE.md` e
> `claude-PLANO.md`. Não é lido pelo código. Movido para `docs/historico/`
> em 2026-08-15.

# BackupRestore — junção do implantado com a análise

Sobre o repositório `MSPA-Coder/BackupRestore` (commit `d5fc097`, "BackupRestore MVP")
confrontado com `claude-ANALISE.md`.

---

## O que o repositório realmente é

React + TypeScript + Vite + Tailwind + Supabase, ~1.100 linhas, gerado no Bolt. E aqui
está o ponto que define a junção inteira:

**A camada de gerência está boa. A camada de execução não existe.**

`src/lib/jobEngine.ts` não roda `pg_dump`. Ele faz isto:

```ts
await sleep(PHASE_DURATION_MS);          // 700 ms por "fase"
const sha = generateSha256();            // 64 chars aleatórios de [0-9a-f]
const size = randomSize(5, 80);          // número aleatório entre 5 e 80 MB
```

E a reconciliação, que deveria comparar o catálogo com o disco:

```ts
if (Math.random() < 0.05) {              // 5% de chance de marcar "missing"
  await supabase.from('artifacts').update({ status: 'missing' })
}
```

Não é um defeito de implementação — é uma barreira de plataforma. Um aplicativo React
roda dentro do navegador, em sandbox: não tem acesso ao sistema de arquivos, não executa
processos, não fala com o Docker. **Nenhuma quantidade de código nesse projeto vai
conseguir fazer um backup.** O que ele produz hoje são linhas no Supabase descrevendo
backups que nunca aconteceram.

Você chegou nessa conclusão sozinho ("apenas script resolve"). Concordo — e o inverso
também é verdade: o script sozinho não te dá o que você gostou.

---

## O que fica (e é melhor do que eu tinha proposto)

Fui injusto na análise anterior ao reduzir o histórico a "um `backups.log` de uma linha
por operação". O modelo de dados do MVP é melhor que isso, em pontos concretos:

- **`artifacts.purpose = 'pre_restore'` + `jobs.safety_artifact_id`** — dump automático de
  segurança antes de toda restauração, rastreado como artefato de primeira classe. É o
  que ferramentas de backup sérias fazem e eu não tinha proposto. Restauração é a operação
  destrutiva do sistema; ter a rede embaixo dela muda o risco do conjunto.
- **`artifacts.status` como ciclo de vida** (`creating → valid → corrupted | missing |
  deleted`) — distingue "apaguei por retenção" de "sumiu do disco". Um log não distingue.
- **`artifacts.pinned`** — proteger um artefato da retenção. Óbvio depois de ver, e some
  numa solução só de script.
- **`sha256` + `manifest_path`** — validação real de integridade.
- **`events`** como trilha de auditoria separada dos `jobs`.
- **`jobs.worker_id` + `jobs.heartbeat_at`** — ver abaixo, é a chave da junção.

A interface também fica. Ela resolve as duas coisas que pasta + log não resolvem: ver o
estado dos quatro projetos de uma vez, e conduzir uma restauração com confirmação
explícita.

---

## A dobradiça: a fila já está no schema

O detalhe que faz essa junção ser encaixe e não reescrita:

```sql
worker_id     text,
heartbeat_at  timestamptz,
status  ... CHECK (status IN ('queued','running',...,'interrupted'))
```

`worker_id`, `heartbeat_at`, `queued`, `interrupted`. **O schema já foi desenhado para um
processo externo pegar trabalho de uma fila.** Ele só nunca ganhou esse processo — o
`jobEngine` no navegador ocupou o lugar dele e fingiu o serviço.

Então a junção é: **tirar o `jobEngine` do lugar e plugar um agente local no soquete que
já existe.**

```
┌──────────────┐   insere job          ┌──────────┐   pega job queued   ┌─────────────┐
│  UI React    │ ───status=queued────► │  jobs    │ ◄────────────────── │  agente.py  │
│  (localhost) │                       │          │                     │  (Windows)  │
│              │ ◄──lê progresso────── │ artifacts│ ◄──grava real────── │ docker exec │
└──────────────┘                       │ events   │    sha256, bytes    │ pg_dump     │
                                       └──────────┘                     │ git bundle  │
                                                                        └─────────────┘
        Agendador de Tarefas ─────────────────────────────────────────────────┘
                    (mesmo agente, sem UI, no horário marcado)
```

A UI **nunca executa nada** — só enfileira e lê. O agente é o único que toca em disco,
Docker e Git. E o mesmo agente atende os dois gatilhos: o botão na tela e o Agendador.
Isso resolve o buraco que apontei na análise (sem agendamento, nada roda sozinho) sem
perder a tela.

---

## O que sai

| Sai | Por quê |
|---|---|
| `src/lib/jobEngine.ts` (287 linhas) | simulação; vira `POST /api/jobs` de ~15 linhas |
| `generateSha256()`, `randomSize()` | o agente calcula os valores reais |
| `reconcileArtifacts()` com `Math.random()` | o agente lista a pasta de verdade |
| Supabase (nuvem) | ver abaixo |
| `database_connections.host / port` | trocar por `container_name` — seção 1 da análise |
| `database_connections.encrypted_password` | **não existe criptografia nenhuma no repositório** — o nome da coluna é aspiracional, não há uma linha de cripto em `src/`. E via `docker exec` a senha não é necessária (seção 2a). Remover. |

### Sobre o Supabase

Duas razões para trazer o banco para casa:

**1. Segurança.** Todas as políticas RLS são `TO anon, authenticated USING (true)` — CRUD
total para o papel anônimo, em todas as tabelas. A chave `VITE_SUPABASE_ANON_KEY` é
pública por construção: ela vai embutida no JS compilado. Ou seja, quem tiver a URL do
projeto lê e escreve seu catálogo inteiro, incluindo `projects.repository_path` (a
estrutura de pastas da sua máquina) e a tabela de conexões. O comentário do schema diz
"dados intencionalmente compartilhados/local" — mas isto é um Postgres na nuvem, não local.

**2. Coerência com o propósito.** Depois da conversa sobre reconstrução do zero: o catálogo
que diz *o que você tem e se está íntegro* não deveria depender de um serviço externo e de
conexão. Se os artefatos estão em `Dropbox/BackpsDB`, o índice deles deve estar do lado.

Trocar por **SQLite local** custa pouco: `src/lib/supabase.ts` tem 6 linhas, e o
acoplamento é raso — 5 consultas em `App.tsx` e o resto dentro do `jobEngine`, que sai
inteiro de qualquer forma. Vira um `src/lib/api.ts` com `fetch('/api/...')` contra o
agente. O schema converte quase literal: `uuid`→`text`, `timestamptz`→`text` ISO-8601,
`jsonb`→`text`; `CHECK`, índices e triggers funcionam igual no SQLite. E some tudo que é
RLS, política e chave — menos código, não mais.

Se preferir manter o Supabase para acessar de fora de casa, o desenho do agente não muda
(ele passa a fazer polling na nuvem). Mas aí feche as políticas e aceite que o catálogo
mora fora.

---

## Ajustes no schema

Fora as remoções acima, três coisas a acrescentar, vindas da análise:

```sql
-- 1. Falar com o contêiner, não com a porta (seção 1)
ALTER TABLE database_connections ADD COLUMN container_name text NOT NULL;
--    e registrar se o contêiner estava parado, para devolvê-lo ao estado original
ALTER TABLE jobs ADD COLUMN container_was_running boolean;

-- 2. O terceiro artefato (seção 2b) — sem ele não se reconstrói do zero
--    artifacts.type hoje aceita ('database','code'); passa a aceitar 'config'
--    → cópia de .env*, .secrets/, .certs/, .docker-local/ (~200 KB no total)

-- 3. Código vira bundle, não zip (seção 4)
--    artifacts.relative_path passa a terminar em .bundle
--    git bundle verify substitui o manifesto próprio
```

O item 2 é o mais importante: hoje o modelo tem `database` e `code`, e um backup só com
esses dois **não reconstrói nada** — o `compose.yaml` nem constrói a imagem sem
`.certs/local-root-ca.crt`.

---

## Ordem sugerida

1. **Agente primeiro, sem UI.** `agente.py` com a lista dos 4 projetos, gravando em SQLite.
   Rodar pelo Agendador. A partir daqui você **tem backup de verdade** — hoje não tem.
2. **Restauração testada** numa pasta descartável, seguindo os 6 passos da seção 8 da
   análise. É o que prova que a cópia de config está completa.
3. **UI reapontada** para o agente: `supabase.ts` → `api.ts`, `jobEngine.ts` → `POST /api/jobs`.
   As telas, os tipos (`src/types/index.ts`) e o Tailwind ficam como estão.
4. **Depois, se sobrar vontade:** `pinned`, reconciliação real, validação sob demanda.

Note que o passo 1 entrega valor sozinho e o 3 é opcional no sentido estrito — mas é o que
você gostou, e depois do passo 1 ele fica barato, porque a parte difícil (execução real)
já estará resolvida e testada.

---

## Resumo

O MVP acertou o **catálogo** e errou o **motor**; minha análise acertou o motor e
subestimou o catálogo. A junção é literalmente isso: o schema e as telas do repositório,
com um agente Python no lugar do `jobEngine`, banco local em vez de nuvem, `docker exec`
em vez de `host:porta`, e um terceiro tipo de artefato para a config.

Nada aqui exige sofisticação nova. O trabalho é quase todo **remoção**: sai a simulação,
sai a nuvem, sai o RLS, sai o cofre de senha. O que entra é um script — que você já
concluiu que resolve.
