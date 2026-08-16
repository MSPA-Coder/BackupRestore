> **Histórico — superado.** Descreve uma arquitetura que não foi implementada
> (Docker, SQLAlchemy, fila de jobs com worker separado, criptografia,
> cobertura de 85%). O sistema real é host-only, sem worker, sem
> criptografia, sem suíte de testes. Não use como referência de
> comportamento esperado — `README.md`/`RESTAURAR.md` são a fonte de
> verdade atual. Movido para `docs/historico/` em 2026-08-15.

# BackupRestore — especificação funcional e técnica

**Versão:** 1.0  
**Estado:** pronta para desenvolvimento do MVP  
**Data de referência:** 10 de agosto de 2026  
**Idioma da interface:** português do Brasil  

## 1. Visão do produto

BackupRestore é uma aplicação web local, de usuário único, para criar, catalogar, validar, reter e restaurar backups de bancos PostgreSQL e criar pacotes independentes do código-fonte de projetos Git. A aplicação centraliza os artefatos fora das pastas dos projetos e mantém um histórico auditável das operações.

O produto reduz quatro riscos: backups manuais inconsistentes, perda de arquivos por seleção incorreta, restaurações destrutivas sem salvaguardas e ausência de evidência objetiva de integridade.

### 1.1 Objetivos do MVP

- Centralizar a configuração e a situação de todos os projetos cadastrados.
- Gerar dumps PostgreSQL restauráveis, com integridade verificada.
- Gerar ZIPs de código reproduzíveis a partir do estado de trabalho do Git.
- Tornar uma restauração deliberada, validada, registrada e protegida por backup de segurança.
- Aplicar retenção somente depois da confirmação de um novo artefato íntegro.
- Expor métricas operacionais que permitam responder: “há backup válido, quando foi feito, quanto ocupa e já foi testado?”.

### 1.2 Não objetivos do MVP

- Agendamento automático ou serviço de execução em segundo plano fora da aplicação.
- Armazenamento em nuvem, replicação remota ou política 3-2-1 completa.
- Login, múltiplos usuários, acesso pela internet ou controle por papéis.
- Backup físico, incremental, contínuo ou recuperação point-in-time do PostgreSQL.
- Criptografia dos próprios arquivos `.dump` e `.zip`.
- Restauração automática de código ou alteração de um repositório a partir de ZIP.
- Integração com GitHub, GitLab ou outro provedor Git.
- Descoberta ou backup de projetos que não sejam repositórios Git.

## 2. Premissas, restrições e decisões

### 2.1 Premissas confirmadas

- O host é Windows com Docker Desktop, Git e acesso local aos projetos.
- Aplicação, testes, Git e clientes PostgreSQL executam em contêineres Docker; não serão instaladas dependências de projeto no host.
- O estado interno usa SQLite por meio do módulo `sqlite3` da biblioteca padrão do Python. O projeto não usa SQLAlchemy, outro ORM, driver SQLite externo ou servidor de banco para esse estado.
- Flask, o servidor HTTP e as ferramentas PostgreSQL são empacotados de forma reproduzível na imagem Docker; o mantenedor não precisa instalar pacotes Python no Windows.
- Os bancos protegidos são PostgreSQL. Bancos SQLite em pastas `instance` são legados e ficam fora do escopo.
- Backups antigos dentro de pastas `backups` dos projetos não serão usados nem migrados automaticamente.
- Dumps de banco e pacotes de código são tipos independentes, com configuração, execução e retenção próprias.
- O ZIP é produzido pela aplicação; scripts de empacotamento dos projetos não são executados.
- O socket do Docker não será montado no contêiner e o contêiner não usará modo privilegiado.

### 2.2 Decisões que encerram os pontos antes abertos

| Tema | Decisão do MVP |
|---|---|
| Pasta central | Configurada no deployment e montada em `/data/backups`; valor de host inicialmente sugerido `D:\Backups\BackupRestore`. A aplicação não usa o destino sem teste de escrita, leitura, renomeação e exclusão de arquivo temporário. Mudá-lo exige recriar os contêineres, pois a aplicação não controla mounts do Docker. |
| Raiz de descoberta | Configurada no deployment e montada em `/workspace/projects:ro`; valor de host inicialmente sugerido `C:\Users\MSPA\Dropbox\Programacao\VSCodeProjects`. Mudá-la também exige recriar os contêineres. |
| Retenção | Padrão de 10 artefatos íntegros por projeto e por tipo; intervalo permitido de 1 a 999; cada projeto pode sobrescrever o padrão. Artefatos falhos não contam para a cota. |
| Cadastro | Manual e por descoberta assistida. A descoberta nunca cadastra ou altera projetos sem confirmação. |
| Repositórios | Backup de código exige repositório Git válido. Um projeto pode ser cadastrado apenas para backup de banco, sem habilitar código. |
| Seleção de código | Inclui arquivos rastreados e arquivos não rastreados que não estejam ignorados. Exclui `.git`, ignorados, links simbólicos cujo destino escape do repositório e a pasta central de backup. |
| Formato do banco | `pg_dump --format=custom --no-owner --no-acl`; extensão `.dump`. O dump não é envolvido em ZIP. |
| Validação do dump | Sucesso do processo, arquivo não vazio, `pg_restore --list`, hash SHA-256 e persistência do manifesto. |
| Restauração | Restauração completa por recriação controlada do banco de destino. Antes de qualquer alteração, a aplicação cria e valida um dump de segurança obrigatório. |
| Compatibilidade PostgreSQL | A versão principal das ferramentas deve ser igual ou superior à do servidor e dentro da matriz oficialmente suportada. Incompatibilidade bloqueia a operação. |
| Segredos | Senhas são criptografadas no SQLite por uma chave externa ao banco, nunca exibidas integralmente e sempre suprimidas dos logs. |
| Execução | Operações longas viram jobs persistidos e são executadas por um único worker local, sem bloquear a requisição HTTP. |

## 3. Personas e termos

### 3.1 Persona principal

**Mantenedor local:** desenvolvedor responsável pelos projetos e bancos, capaz de reconhecer caminhos, hosts, portas e nomes de banco. Precisa de uma operação segura, mas não de recursos multiusuário.

### 3.2 Glossário

- **Artefato:** arquivo final `.dump` ou `.zip` aceito pelo catálogo.
- **Manifesto:** metadados em JSON associados ao artefato, incluindo hash, tamanho, origem, ferramenta e conteúdo verificável.
- **Job:** execução persistida de backup, restauração, validação ou retenção.
- **Íntegro:** artefato cujo tamanho, hash e validações específicas do formato foram aprovados.
- **Dump de segurança:** backup criado imediatamente antes de uma restauração destrutiva.
- **RPO observado:** idade do último backup íntegro; não é garantia enquanto não houver agendamento.
- **RTO observado:** duração medida de uma restauração concluída; não é garantia contratual.

## 4. Escopo funcional

### 4.1 Configurações gerais

**RF-001 — Configurar armazenamento central.** O mantenedor informa o caminho absoluto do host no arquivo local de deployment antes de subir o Compose. A aplicação exibe o caminho de host e o mount `/data/backups`, valida acesso ao iniciar e mostra capacidade livre, ocupação total catalogada e estado do destino. A UI não tenta criar mounts dinamicamente.

**RF-002 — Configurar raiz de descoberta.** O mantenedor informa o caminho absoluto no deployment; ele é montado em `/workspace/projects` como somente leitura. A aplicação cadastra caminhos relativos a essa raiz e exibe a representação equivalente do host.

**RF-003 — Configurar retenções padrão.** Devem existir valores independentes para banco e código.

**RF-004 — Proteger limites de caminho.** O destino central não pode estar dentro da raiz de um projeto cadastrado. Um projeto não pode estar dentro do destino central. Caminhos devem ser normalizados e comparados sem depender de maiúsculas/minúsculas no Windows.

### 4.2 Projetos

**RF-010 — Descobrir projetos.** A aplicação deve examinar os descendentes diretos da raiz configurada e, opcionalmente, até uma profundidade máxima de 3, localizar diretórios Git e classificar cada resultado como novo, já cadastrado ou inválido.

**RF-011 — Cadastrar projeto.** Campos obrigatórios: nome único e caminho absoluto único. O mantenedor escolhe habilitar banco, código ou ambos.

**RF-012 — Editar projeto.** Alterações de caminho, conexão e retenção devem ser validadas antes de persistir. Alterar o caminho não move artefatos históricos.

**RF-013 — Ativar/desativar projeto.** Projeto inativo permanece consultável, mas não aceita novos backups ou restaurações.

**RF-014 — Exibir diagnóstico.** A tela do projeto deve mostrar separadamente: caminho acessível, Git válido, `.gitignore` encontrado, estado de trabalho Git, conexão PostgreSQL, versão do servidor, credencial disponível e destino gravável.

**RF-015 — Testar conexão.** O teste deve usar timeout, consultar versão e banco atual e não persistir uma senha digitada até o salvamento explícito.

### 4.3 Configuração PostgreSQL

**RF-020 — Configurar conexão.** Campos: host, porta (padrão 5432), database, usuário, senha, modo SSL e timeout. Host deve ser informado do ponto de vista do contêiner, por exemplo `host.docker.internal` para serviço exposto no host.

**RF-021 — Não aceitar banco administrativo como alvo.** `postgres`, `template0` e `template1` não podem ser escolhidos como banco protegido ou destruído sem uma mudança futura explícita de escopo.

**RF-022 — Separar segredo da URI.** A senha não deve fazer parte de URL exibida, mensagem de erro, argumento visível do processo ou manifesto. O cliente PostgreSQL deve recebê-la por arquivo temporário `.pgpass` com permissão restrita, removido ao final.

### 4.4 Backup de banco

**RF-030 — Solicitar backup.** Um projeto ativo e habilitado deve aceitar um pedido de backup manual. Pedidos concorrentes equivalentes devem ser recusados com indicação do job em andamento.

**RF-031 — Executar dump de forma atômica.** O dump deve ser escrito com extensão temporária no mesmo volume do destino, validado e então renomeado atomicamente para o nome final.

**RF-032 — Nomear artefato.** Padrão: `{slug}__database__{UTC-YYYYMMDDTHHMMSSZ}__{job-id-curto}.dump`. Nomes devem ser únicos e independentes do fuso horário de exibição.

**RF-033 — Validar dump.** Aceitar somente quando: retorno do `pg_dump` é zero; arquivo é maior que zero; `pg_restore --list` retorna zero e lista ao menos uma entrada restaurável; SHA-256 e tamanho foram calculados após o fechamento do arquivo.

**RF-034 — Criar manifesto.** Salvar `{nome-do-artefato}.manifest.json` com esquema versionado, projeto, banco de origem sem senha, timestamps UTC, duração, versões cliente/servidor, tamanho, SHA-256, opções do dump e resultado das validações.

**RF-035 — Registrar falha.** Arquivo temporário deve ser removido após falha, salvo quando a retenção diagnóstica estiver explicitamente habilitada. O job e o histórico mantêm código, fase e mensagem sanitizada.

### 4.5 Backup de código

**RF-040 — Obter inventário pelo Git.** Usar comandos Git com saída delimitada por NUL para suportar espaços e caracteres Unicode. A seleção corresponde à união de arquivos rastreados com arquivos não rastreados e não ignorados.

**RF-041 — Registrar estado Git.** O manifesto deve conter branch quando houver, commit `HEAD` quando houver, indicador `dirty`, contagem de arquivos e lista de caminhos com hash SHA-256 de cada conteúdo.

**RF-042 — Tratar repositórios especiais.** Repositório sem commit inicial pode ser empacotado se houver arquivos elegíveis. Submódulos não são expandidos no MVP; entra apenas o gitlink e o manifesto registra essa limitação. Git LFS inclui o conteúdo presente no working tree, sem executar download.

**RF-043 — Proteger fronteiras.** Links simbólicos são registrados como links apenas quando suportados pelo ZIP e nunca são seguidos para fora da raiz. Mudança ou desaparecimento de arquivo durante leitura deve fazer o job falhar, sem publicar ZIP parcial.

**RF-044 — Gerar ZIP de forma atômica.** Padrão: `{slug}__code__{UTC-YYYYMMDDTHHMMSSZ}__{job-id-curto}.zip`. O manifesto deve existir dentro do ZIP como `backuprestore-manifest.json` e ao lado do artefato.

**RF-045 — Validar ZIP.** Reabrir o arquivo, testar CRC de todas as entradas, rejeitar caminhos absolutos ou com `..`, confirmar a presença do manifesto e comparar contagem e nomes com o inventário.

### 4.6 Catálogo, integridade e retenção

**RF-050 — Listar artefatos.** Filtros: projeto, tipo, estado, período e origem (manual ou pré-restauração). Ordenação inicial: mais recente primeiro.

**RF-051 — Revalidar integridade.** O usuário pode recalcular hash e executar a validação específica do formato. Divergência muda o artefato para `corrupted` e gera evento.

**RF-052 — Detectar arquivos órfãos e ausentes.** Uma reconciliação manual compara catálogo e disco, sem importar ou excluir automaticamente. O usuário pode importar artefato compatível após validação ou remover apenas o registro de um arquivo ausente.

**RF-053 — Aplicar retenção segura.** Após publicar um novo artefato íntegro, excluir os íntegros mais antigos que excedam a cota daquele tipo. Nunca excluir: artefato fixado, último íntegro do tipo, artefato usado por job em andamento ou dump de segurança ainda vinculado a restauração incompleta.

**RF-054 — Exclusão consistente.** A exclusão segue: marcar `deleting`, remover artefato e manifesto, confirmar ausência e marcar `deleted`. Falha deixa estado recuperável e evento de atenção.

### 4.7 Restauração PostgreSQL

**RF-060 — Pré-validações obrigatórias.** Projeto ativo; artefato íntegro; hash atual igual ao catálogo; `pg_restore --list` aprovado; conexão administrativa válida; espaço livre suficiente; versão compatível; nenhum job conflitante.

**RF-061 — Exibir impacto.** A confirmação mostra origem do dump, destino, host, data, tamanho, hash abreviado e informa que o banco atual será substituído.

**RF-062 — Confirmação forte.** Para habilitar o botão, o mantenedor deve digitar exatamente o nome do banco de destino. A confirmação expira em 5 minutos e é vinculada ao artefato e à configuração validada.

**RF-063 — Criar salvaguarda.** Antes de desconectar usuários ou remover o banco, criar e validar um dump de segurança do destino atual. Se falhar, abortar sem alterar o banco. Não existe opção de ignorar essa etapa no MVP.

**RF-064 — Recriar banco.** Conectar a um banco administrativo, impedir novas conexões quando suportado, encerrar sessões do alvo, remover e recriar o banco com proprietário configurado. Cada etapa deve ter timeout e evento próprio.

**RF-065 — Restaurar conteúdo.** Executar `pg_restore --exit-on-error --no-owner --no-acl` no banco recém-criado. A execução é serial no MVP para tornar erros determinísticos.

**RF-066 — Validar resultado.** Confirmar conexão, consultar catálogo PostgreSQL e exigir ao menos um objeto quando o manifesto do dump listar objetos. Registrar duração e contagens disponíveis.

**RF-067 — Tratar falha destrutiva.** Se a falha ocorrer depois da remoção do banco, o estado do job deve ser `failed_needs_attention`, o painel deve destacar criticidade e oferecer uma nova ação explícita para restaurar o dump de segurança. A aplicação nunca executa rollback destrutivo silencioso.

### 4.8 Jobs, histórico e painel

**RF-070 — Persistir job antes da execução.** Estados: `queued`, `running`, `succeeded`, `failed`, `failed_needs_attention`, `cancel_requested`, `cancelled`, `interrupted`.

**RF-071 — Exibir progresso por fases.** O percentual pode ser indeterminado; a interface sempre mostra fase atual, início, duração e última mensagem sanitizada.

**RF-072 — Cancelar com segurança.** Backup pode ser cancelado antes da publicação. Restauração só pode ser cancelada antes da fase destrutiva; depois disso a interface bloqueia cancelamento e explica o motivo.

**RF-073 — Recuperar após reinício.** Ao iniciar, jobs `running` devem virar `interrupted`. Arquivos temporários são reconciliados e nunca promovidos automaticamente.

**RF-074 — Manter eventos.** Cada transição relevante produz evento imutável com timestamp UTC, tipo, severidade, projeto, job e detalhes sanitizados.

**RF-075 — Exibir indicadores.** Painel: projetos ativos, artefatos e bytes por tipo, operações falhas, destino/bytes livres, idade do último íntegro por projeto/tipo, última restauração bem-sucedida e quantidade de artefatos corrompidos/ausentes.

## 5. Regras de negócio

- **RN-001:** banco e código são independentes; falha de um não invalida o outro.
- **RN-002:** somente artefato `valid` pode ser restaurado ou contar para retenção.
- **RN-003:** retenção roda após publicação e nunca antes dela.
- **RN-004:** timestamps persistidos usam UTC; a UI apresenta `America/Sao_Paulo` por padrão.
- **RN-005:** exclusão e alteração de projeto não apagam histórico.
- **RN-006:** slug é imutável após criação e único, para preservar o caminho de armazenamento.
- **RN-007:** não pode haver dois jobs ativos do mesmo projeto e tipo. Durante restauração, nenhum job de banco do projeto pode iniciar.
- **RN-008:** senha, chave, conteúdo de `.pgpass` e URI com credencial são dados secretos e não entram em logs, eventos, manifestos ou respostas da API.
- **RN-009:** mudança na configuração de conexão invalida confirmações de restauração pendentes.
- **RN-010:** um arquivo encontrado no disco não se torna confiável apenas por ter nome ou extensão esperados.

## 6. Arquitetura proposta

### 6.1 Componentes

```text
Navegador local
      |
      v
Flask / API JSON ---- repositórios sqlite3 ---- SQLite (estado e auditoria)
      |
      +---- Serviço de projetos/Git ---- raiz dos projetos (somente leitura)
      |
      +---- Fila persistida + worker único
                 |---- pg_dump / pg_restore / psql
                 |---- gerador e validador ZIP
                 +---- armazenamento central (leitura e escrita)
```

- **Camada web:** páginas server-rendered ou frontend leve consumindo API; nenhuma regra destrutiva fica apenas no navegador.
- **Serviços de aplicação:** projetos, credenciais, backup de banco, backup de código, restauração, catálogo, retenção e reconciliação.
- **Adaptadores:** subprocessos com lista de argumentos, filesystem, Git e ferramentas PostgreSQL.
- **Worker:** processo separado no mesmo Compose, consumindo jobs transacionalmente. Concorrência inicial igual a 1.
- **Estado:** SQLite em volume persistente, acessado diretamente pelo `sqlite3` padrão, com WAL, foreign keys, transações explícitas e migrações SQL versionadas pelo próprio projeto.

### 6.2 Contêineres

- `web`: Flask/Gunicorn, sem modo debug, exposto somente em `127.0.0.1`.
- `worker`: mesma imagem, comando próprio para jobs.
- `test`: profile do Compose com pytest, lint, tipos e testes de integração.
- `postgres-test`: somente no profile de testes, com dados descartáveis.

A imagem fixa versões de Python, Git e clientes PostgreSQL. A raiz de projetos é montada como somente leitura; destino, estado e temporários usam montagens separadas e mínimas. Health checks verificam web, worker e escrita no estado, sem executar backup real.

### 6.3 Estrutura do armazenamento

```text
<backup-root>/
  projects/
    <project-slug>/
      database/
        <arquivo>.dump
        <arquivo>.dump.manifest.json
      code/
        <arquivo>.zip
        <arquivo>.zip.manifest.json
  quarantine/
  temp/
```

O SQLite e a chave mestra não ficam dentro de `<backup-root>` para evitar que a cópia dos artefatos leve junto catálogo e segredo. Temporários ficam no mesmo volume do artefato final para permitir renomeação atômica.

## 7. Modelo de dados

### 7.1 Entidades principais

**settings**

- `id`, `backup_root_host_display`, `discovery_root_host_display`, `default_database_retention`, `default_code_retention`, `timezone`, `updated_at`. Os caminhos efetivos do contêiner são constantes de deployment e não são alterados pelo banco.

**projects**

- `id` UUID, `name`, `slug`, `repository_path`, `active`, `database_enabled`, `code_enabled`, `database_retention`, `code_retention`, `created_at`, `updated_at`.
- Unicidade: `name`, `slug` e caminho normalizado.

**database_connections**

- `project_id`, `host`, `port`, `database`, `username`, `encrypted_password`, `sslmode`, `connect_timeout`, `server_major_version`, `last_tested_at`, `last_test_status`.

**artifacts**

- `id` UUID, `project_id`, `type` (`database|code`), `purpose` (`regular|pre_restore`), `status` (`creating|valid|corrupted|missing|deleting|deleted|failed`), `relative_path`, `manifest_path`, `size_bytes`, `sha256`, `created_at`, `validated_at`, `duration_ms`, `pinned`, `source_job_id`, `metadata_json`.
- Unicidade: caminho relativo e SHA-256 por projeto/tipo quando aplicável.

**jobs**

- `id` UUID, `project_id`, `operation`, `status`, `phase`, `progress`, `requested_at`, `started_at`, `finished_at`, `artifact_id`, `safety_artifact_id`, `error_code`, `error_message`, `parameters_json`, `worker_id`, `heartbeat_at`.

**events**

- `id`, `job_id`, `project_id`, `occurred_at`, `event_type`, `severity`, `message`, `details_json`.

### 7.2 Migração e consistência

- Toda mudança de esquema usa arquivos SQL numerados e versionados. Um migrador pequeno do próprio projeto consulta `PRAGMA user_version`, aplica cada migração pendente dentro de transação e só então atualiza a versão; não há Alembic nem geração automática de esquema.
- Exclusões usam estados e chaves estrangeiras; eventos não têm cascade delete.
- JSON armazena metadados variáveis, não campos essenciais para filtro ou integridade.
- Transição de estado usa compare-and-set transacional para impedir que dois workers adquiram o mesmo job.

## 8. Contrato HTTP/JSON

Prefixo `/api/v1`; datas em ISO 8601 UTC; UUIDs como texto; erros seguem `{ "error": { "code", "message", "details", "request_id" } }`. Operação criada retorna `202 Accepted` e o job. Criação usa `Idempotency-Key` opcional para evitar duplo clique.

| Método e rota | Finalidade |
|---|---|
| `GET /health` | Saúde superficial da aplicação. |
| `GET/PUT /api/v1/settings` | Consultar configurações e alterar apenas retenções/fuso; mounts são somente leitura na API. |
| `POST /api/v1/settings/validate-paths` | Revalidar os mounts configurados no deployment. |
| `GET/POST /api/v1/projects` | Listar e cadastrar projetos. |
| `GET/PATCH /api/v1/projects/{id}` | Consultar e editar projeto. |
| `POST /api/v1/projects/discover` | Descoberta assistida. |
| `POST /api/v1/projects/{id}/diagnose` | Diagnóstico Git, destino e banco. |
| `POST /api/v1/projects/{id}/database/test` | Testar conexão. |
| `POST /api/v1/projects/{id}/backups/database` | Criar job de dump. |
| `POST /api/v1/projects/{id}/backups/code` | Criar job de ZIP. |
| `GET /api/v1/artifacts` | Catálogo filtrável e paginado. |
| `GET /api/v1/artifacts/{id}` | Detalhes e manifesto sanitizado. |
| `POST /api/v1/artifacts/{id}/validate` | Revalidar artefato. |
| `POST /api/v1/artifacts/{id}/restore-plan` | Validar e gerar confirmação temporária. |
| `POST /api/v1/artifacts/{id}/restore` | Criar job usando token e nome digitado. |
| `POST /api/v1/reconcile` | Comparar catálogo e armazenamento. |
| `GET /api/v1/jobs/{id}` | Consultar execução. |
| `POST /api/v1/jobs/{id}/cancel` | Solicitar cancelamento permitido. |
| `GET /api/v1/events` | Histórico paginado e filtrável. |
| `GET /api/v1/dashboard` | Indicadores agregados. |

Endpoints mutáveis exigem `Content-Type: application/json`, validação de origem local e token CSRF. A API não aceita caminho arbitrário para leitura; opera apenas sobre IDs e raízes cadastradas.

## 9. Segurança e proteção de dados

### 9.1 Modelo de ameaça do MVP

Protege contra erro operacional, requisições web forjadas, path traversal, vazamento acidental em logs/manifestos, publicação de artefato parcial e acesso de rede não intencional. Não promete proteção contra administrador do host comprometido, malware com acesso ao perfil do usuário ou exfiltração física do disco.

### 9.2 Controles obrigatórios

- Bind do serviço em `127.0.0.1`; cabeçalhos `Host` aceitos por allowlist; CORS desabilitado por padrão.
- CSRF em toda mutação e cookies `HttpOnly`, `SameSite=Strict` quando usados.
- Content Security Policy sem scripts inline, `X-Content-Type-Options: nosniff` e `frame-ancestors 'none'`.
- Chave mestra com ao menos 256 bits, gerada na primeira inicialização, montada como secret somente leitura e fora do Git, SQLite e backup root.
- Criptografia autenticada de senhas; rotação de chave documentada antes da primeira versão estável.
- Subprocessos com `shell=False`, argumentos em lista, timeout, diretório de trabalho controlado e ambiente mínimo.
- Normalização por `resolve`, checagem de ancestralidade e rejeição de caminhos que escapem das raízes autorizadas.
- Logs estruturados com redaction por nome de campo e padrões de URI; mensagens do `stderr` também passam por sanitização.
- Arquivos e diretórios criados com a permissão mais restritiva disponível no volume Windows/Docker.

## 10. Requisitos não funcionais e métricas

As metas abaixo são critérios de aceite, inspirados nas características do ISO/IEC 25010 e adaptados ao uso local.

| ID | Característica | Meta mensurável | Como verificar |
|---|---|---|---|
| RNF-001 | Correção | 100% dos artefatos publicados têm manifesto, tamanho e SHA-256 coincidentes. | Testes e reconciliação em amostra de 100 artefatos gerados. |
| RNF-002 | Confiabilidade | Nenhuma falha ou cancelamento publica arquivo final parcial. | Testes de injeção de falha em todas as fases de escrita. |
| RNF-003 | Recuperabilidade | Reinício durante job termina em `interrupted`, sem job eternamente `running`, em até 30 s após startup. | Teste de interrupção do worker. |
| RNF-004 | Segurança | Zero segredo em respostas, logs, eventos e manifestos do conjunto de testes. | Scanner de padrões e testes com credencial sentinela. |
| RNF-005 | Desempenho UI | p95 das consultas de catálogo/painel abaixo de 500 ms com 100 projetos, 10 mil artefatos e 100 mil eventos, no ambiente de referência. | Benchmark em contêiner com dados sintéticos. |
| RNF-006 | Throughput | Overhead de leitura/gravação da aplicação menor que 15% sobre a cópia sequencial do mesmo volume, excluindo PostgreSQL e compressão. | Benchmark documentado. |
| RNF-007 | Escalabilidade local | Paginação mantém no máximo 100 registros por resposta e nenhuma tela carrega eventos sem limite. | Testes de API e inspeção de consultas. |
| RNF-008 | Usabilidade | Backup manual iniciado em até 3 interações a partir do projeto; restauração exige confirmação forte e mostra todas as salvaguardas. | Testes de fluxo de interface. |
| RNF-009 | Acessibilidade | Fluxos principais atendem WCAG 2.2 AA: teclado, foco, nomes acessíveis, contraste e anúncios de estado. | axe-core sem violações críticas/sérias + teste manual de teclado. |
| RNF-010 | Compatibilidade | Layout funcional em Chrome e Edge atuais, largura de 360 a 1920 px. | Matriz Playwright. |
| RNF-011 | Manutenibilidade | Cobertura de linhas ≥ 85%, branches ≥ 75% e 100% dos fluxos destrutivos com testes de integração. | Relatório pytest-cov. |
| RNF-012 | Qualidade estática | Zero erro em lint, formatação e checagem de tipos do código de aplicação. | Comandos oficiais no serviço `test`. |
| RNF-013 | Observabilidade | 100% dos jobs têm request ID, timestamps, fase terminal e duração ou motivo de interrupção. | Consulta de invariantes no banco de teste. |
| RNF-014 | Portabilidade | Ambiente novo sobe e executa testes apenas com Docker Compose e arquivos versionados, exceto secrets locais. | Ensaio em workspace limpo. |

### 10.1 Indicadores operacionais

- **Taxa de sucesso por operação:** `jobs succeeded / jobs terminados`, janela de 30 dias.
- **Idade do último íntegro:** agora menos `created_at` do último artefato válido, por projeto e tipo.
- **Cobertura de proteção:** configurações habilitadas com ao menos um artefato válido / configurações habilitadas.
- **Integridade recente:** artefatos revalidados sem divergência / artefatos revalidados.
- **RTO observado:** p50 e p95 da duração de restaurações concluídas, sem transformar o valor em garantia.
- **Crescimento do armazenamento:** bytes criados menos bytes removidos por retenção, por 7 e 30 dias.

Como não há agendamento no MVP, o sistema mede RPO observado, mas não promete RPO máximo. A interface deve dizer isso explicitamente.

## 11. UX e estados de interface

O protótipo existente define identidade visual e navegação inicial. O desenvolvimento deve manter as quatro áreas — visão geral, projetos, histórico e configurações — e acrescentar estados reais.

Toda coleção terá `loading`, vazia, sucesso, parcial e erro com ação recuperável. Botões de backup ficam desabilitados com motivo quando o diagnóstico falhar. Operações não usam apenas toast: jobs permanecem visíveis após navegação ou recarga. Cor nunca é o único indicador de estado.

No detalhe do projeto, abas de banco e código possuem contagem, último íntegro, idade, retenção e ação principal independentes. A restauração usa duas etapas: plano/validação e confirmação forte. `failed_needs_attention` ocupa destaque persistente até reconhecimento do mantenedor.

## 12. Tratamento de erros

Erros têm código estável e mensagem orientada à ação. Códigos mínimos:

- `PATH_NOT_ACCESSIBLE`, `PATH_OUTSIDE_ALLOWED_ROOT`, `BACKUP_ROOT_CONFLICT`.
- `GIT_NOT_REPOSITORY`, `GIT_FILE_CHANGED`, `GIT_COMMAND_FAILED`.
- `DB_CONNECTION_FAILED`, `DB_VERSION_INCOMPATIBLE`, `DB_DUMP_FAILED`.
- `ARTIFACT_INVALID`, `ARTIFACT_HASH_MISMATCH`, `ARTIFACT_MISSING`.
- `RESTORE_CONFIRMATION_INVALID`, `RESTORE_SAFETY_DUMP_FAILED`, `RESTORE_FAILED_NEEDS_ATTENTION`.
- `JOB_CONFLICT`, `JOB_NOT_CANCELLABLE`, `NO_SPACE_LEFT`, `PROCESS_TIMEOUT`.

Stack trace fica apenas no log técnico sanitizado. A UI mostra request ID e orientação, nunca comando com credencial.

## 13. Estratégia de testes

### 13.1 Testes automatizados

- **Unitários:** normalização de caminhos Windows, slug, retenção, transições de job, sanitização, seleção Git, nomes e manifestos.
- **Integração filesystem:** publicação atômica, volume cheio simulado, arquivos alterados durante leitura, links, Unicode, reconciliação e retenção protegida.
- **Integração Git:** tracked, untracked, ignored, submodule, LFS sem fetch, repo sem commit e nomes especiais.
- **Integração PostgreSQL:** dump e restore em versões suportadas, senha com caracteres especiais, conexões ativas, falha antes/depois do drop, backup de segurança e objetos variados.
- **API:** validação, paginação, idempotência, CSRF, conflitos, redaction e códigos de estado.
- **E2E:** cadastro, diagnóstico, dois tipos de backup, filtro, revalidação, restauração confirmada, falha crítica e recuperação.
- **Segurança:** traversal, ZIP slip, host header, injeção de argumento, XSS armazenado e busca da credencial sentinela.

### 13.2 Testes manuais de aceite

1. Configurar raízes e verificar teste de leitura/escrita.
2. Descobrir e cadastrar um repositório sem duplicá-lo.
3. Gerar ZIP com arquivo tracked, untracked permitido e ignored; confirmar conteúdo esperado.
4. Gerar dump, conferir catálogo, manifesto e `pg_restore --list`.
5. Corromper cópia controlada e confirmar detecção por hash.
6. Restaurar banco descartável, comparando objetos/dados de referência.
7. Simular falha do dump de segurança e confirmar que o destino não foi alterado.
8. Interromper worker durante backup e confirmar recuperação sem arquivo final parcial.
9. Exceder retenção e confirmar preservação do último íntegro e dos fixados.
10. Navegar somente por teclado e validar foco do diálogo de restauração.

## 14. Critérios de aceite do MVP

O MVP está aceito quando:

- Todos os requisitos `RF` enumerados nesta especificação estão implementados ou formalmente retirados em decisão registrada.
- Os fluxos manuais de aceite passam em Docker Compose sobre workspace limpo.
- Todas as metas `RNF` passam; exceção exige evidência, impacto e decisão explícita.
- Um banco PostgreSQL de teste é salvo, destruído e restaurado com dados equivalentes aos fixtures.
- Um repositório de teste produz ZIP exatamente com o inventário Git previsto.
- Nenhum segredo sentinela aparece nos artefatos indevidos, banco de eventos, respostas ou logs.
- Reinício e falhas injetadas não deixam artefato parcial publicado nem restauração classificada falsamente como sucesso.
- Documentação cobre primeira inicialização, geração da chave, configuração de mounts, operação, restauração de emergência e atualização.

## 15. Plano de implementação

### Fase 0 — Fundação

- Estrutura Python, Compose, configurações, migrações, logs estruturados, IDs de requisição e pipeline de testes.
- Entrega: health check e banco interno migrado de forma reproduzível.

### Fase 1 — Configuração e projetos

- Settings, cadastro, descoberta, diagnósticos, segredo criptografado e testes de caminho/conexão.
- Entrega: projeto pronto para operar, ainda sem gerar artefato.

### Fase 2 — Jobs e backup de banco

- Fila persistida, worker, dump atômico, manifesto, validação, catálogo e eventos.
- Entrega: dump íntegro e auditável.

### Fase 3 — Backup de código

- Inventário Git, ZIP seguro, manifesto por arquivo e validação.
- Entrega: pacote correspondente ao working tree permitido.

### Fase 4 — Retenção e reconciliação

- Políticas por tipo, exclusão consistente, revalidação e órfãos/ausentes.
- Entrega: armazenamento governado sem exclusão insegura.

### Fase 5 — Restauração segura

- Plano, confirmação forte, dump de segurança, recriação, restore, falha crítica e recuperação explícita.
- Entrega: ciclo completo comprovado em banco descartável.

### Fase 6 — Interface, métricas e endurecimento

- Integração do protótipo com dados reais, estados acessíveis, painel, E2E, benchmarks, segurança e documentação operacional.
- Entrega: MVP candidato a uso.

## 16. Backlog posterior ao MVP

- Agendador com política de RPO e notificações locais.
- Teste periódico de restauração em banco efêmero.
- Destino secundário e verificação 3-2-1.
- Criptografia de artefatos e rotação de chave.
- Backup incremental/PITR quando houver necessidade comprovada.
- Exportação assinada de relatórios de auditoria.
- Restauração assistida de código em pasta nova, nunca sobre working tree existente.

## 17. Rastreabilidade do protótipo

| Área existente | Requisitos correspondentes | Evolução necessária |
|---|---|---|
| Visão geral | RF-075, RNF-005 | Substituir métricas fictícias, incluir idade do último íntegro, corrupção e RTO observado. |
| Projetos | RF-010 a RF-015 | Formulários reais, descoberta confirmada, estados vazios/erro e diagnóstico acionável. |
| Detalhe banco | RF-020 a RF-035, RF-060 a RF-067 | Jobs persistentes, validação, plano de restauração e confirmação forte. |
| Detalhe código | RF-040 a RF-045 | Manifesto, estado Git, inventário e detalhes verificáveis. |
| Histórico | RF-070 a RF-074 | Paginação, filtros, fases, severidade, request ID e erros sanitizados. |
| Configurações | RF-001 a RF-004 | Manter mounts como somente leitura, permitir editar retenções e mostrar saúde/capacidade. |

## 18. Definição de pronto por item

Um item só está pronto quando código e migração estão revisados; testes automatizados relevantes passam no contêiner; estados de carregamento, vazio e erro existem; segurança e redaction foram verificadas; documentação foi atualizada; não há regressão de acessibilidade crítica/séria; e o requisito e seu teste de aceite permanecem rastreáveis pelo identificador desta especificação.
