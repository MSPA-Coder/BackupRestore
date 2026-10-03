# Roteiro do ensaio completo de recuperação do VPS1 (custo zero)

Roteiro versionado, escrito como instrução para quem conduz o ensaio (uma pessoa ou uma IA).
Resolve o achado 3 do [ensaio parcial de 03/10/2026](ENSAIO_RTO_2026-10-03.md).
As referências a "o mantenedor" e "sessão nova" valem para qualquer executor.

---

Você vai conduzir um **ensaio de recuperação de desastre do VPS1 sem nenhum custo**: reconstruir o servidor numa máquina descartável **dentro deste PC**, só com o que sobreviveria à perda do VPS (o catálogo de backups do BackupRestore, o kit externo de segredos e o GitHub), e **cronometrar cada fase**. Responda em português do Brasil, direto e sem enfeite.

## O que já se sabe (não refaça)

O ensaio parcial de 03/10/2026 está em `docs/ENSAIO_RTO_2026-10-03.md` (BackupRestore#33). Ele restaurou os quatro dumps no `backuprestore-sandbox` em 15 s no total, conferiu a produção em leitura e achou: RPO real de cerca de 11 h no catálogo local (cópias mais novas ficaram só no VPS), Wealthfolio com cópia de volume válida mas sem chave mestra para abrir, `media_volume` do CB sem backup verificado, e o plano `PLANO_BACKUPRESTORE_VPS.md` fora do caminho citado. **Faltou o essencial**: nunca se reconstruiu um servidor. Este ensaio cobre o que ficou de fora: provisionamento, segredos, clone, build, deploy, nginx e dados vivos.

## Regra de custo: ZERO

Proibido criar ou tocar em qualquer coisa que custe dinheiro ou crie recurso externo: instância Oracle ou de qualquer nuvem, registro de DNS, certificado Let's Encrypt (`certbot` real), Tailscale, domínio, e-mail ou Telegram reais. Se um passo exigir isso, **não o execute**: simule (abaixo) ou registre como "tempo externo estimado". Gasto de CPU, disco e banda local é livre.

## A máquina do ensaio: um "VPS1" descartável em WSL2

Este PC (Windows 11 Home, 15,6 GB de RAM, 739 GB livres, Docker Desktop com WSL2; Hyper-V não existe nesta edição) permite uma distro Ubuntu isolada. O VPS1 real é Ubuntu 24.04, então use a mesma versão.

- Crie a distro **com nome exato `rto-vps1`**, a partir de um rootfs do Ubuntu 24.04 (por exemplo `docker export` de um contêiner `ubuntu:24.04` seguido de `wsl --import rto-vps1 <pasta> <tar>`), com **systemd habilitado** (`/etc/wsl.conf`) e o seu **próprio `dockerd`** dentro dela. Isso importa por duas razões: isola o ensaio dos contêineres de desenvolvimento do mantenedor (os nomes dos contêineres do VPS são iguais aos locais de propósito) e deixa o `instalar.sh` e as units systemd rodarem de verdade.
- **Nunca** toque nas distros `docker-desktop` nem em qualquer contêiner, volume ou rede que não tenha sido criado dentro de `rto-vps1`. Antes de qualquer `wsl --terminate` ou `wsl --unregister`, confira que o nome é `rto-vps1`.
- Não altere `.wslconfig` nem a memória do Docker Desktop sem perguntar. Se faltar memória, suba os aplicativos um de cada vez e registre como achado.
- Rede: o ensaio responde em `localhost` com `curl --resolve dominio:443:127.0.0.1`. Nenhuma porta é aberta para fora.

## O que o ensaio mede e o que NÃO mede (diga isso no relatório)

| Mede (tempo de máquina real) | Não mede (estimar com faixa e fonte) |
|---|---|
| Ubuntu base, Docker, nginx, systemd, instalar.sh | Criar a instância na nuvem e entrar nela |
| Clone dos repositórios, segredos do kit, build das imagens | Tailscale, firewall da nuvem, fail2ban |
| Restauração dos bancos, migrações, `deploy`, health | Emissão do certificado real e propagação de DNS |
| Volume do Wealthfolio com a chave mestra | Tempo humano de atenção, decisões e interrupções |
| Conferência de dados e do contrato v4 | |

Para a coluna da direita, procure no que já existe os tempos de quando o VPS2 foi criado (13/09/2026) e o VPS1 (17-18/08/2026) e cite a fonte; se não houver registro, pergunte ao mantenedor uma faixa (mínimo e máximo) e marque como **estimativa dele**, nunca como medida.

## Regras de segurança

1. **Produção só em leitura**: `listar`, `estado`, `~/deploy.sh --status`, consultas SQL de leitura pelo MCP `financas`. Nenhum restore, reinício, deploy ou escrita no VPS1 nem no VPS2.
2. Restaurações vão **só para dentro de `rto-vps1`** (e, para o que já existe, o `backuprestore-sandbox`). A trava do BackupRestore (`CONTAINERS_PROTEGIDOS`) não se contorna.
3. **Segredos não entram na conversa nem no relatório**: mostre nome e tamanho, nunca valor. Copie o kit para dentro da distro por arquivo, sem imprimir. Não mova a pasta de chaves (`C:\Dev\OracleKeysMariano`, o BackupRestore aponta para o caminho absoluto).
4. **A reconstrução do Wealthfolio** (imagem com patches, 35 a 60 minutos de build) é CPU local e não custa dinheiro, mas é longa: **peça OK antes de iniciá-la**, uma vez, e diga a estimativa.
5. **Não escreva código para fazer o ensaio passar.** Falha é resultado: classifique de quem é o defeito (backup, documentação, script ou ambiente) antes de mexer. Mudança só em branch e PR; merge só com OK. Sem commit de segredo.

## Roteiro com cronômetro

Crie um arquivo `marcas.log` com um utilitário de uma linha (`marca FASE texto` grava `data-hora-UTC`, fase e texto). **Todo marcador sai desse arquivo**, não da memória. O relógio começa no T0 e **não conta** o preparo da distro (equivale a "a máquina já existe e eu estou nela").

0. **Preparo (fora do relógio):** distro `rto-vps1` criada, systemd e `dockerd` no ar. Anote o tempo à parte.
1. **T0, SO base:** `apt` com o mesmo conjunto do servidor real (Docker, nginx, git, curl, certbot **só instalado**, openssh-server). Existe um roteiro versionado para isso? Procure em `_manutencao/vps/` e nos READMEs. **Se não existir, é achado número um**: o servidor real foi montado à mão.
2. **Artefatos do servidor:** clone de `_manutencao` e `bash vps/instalar.sh` (as units systemd, `deploy.sh`, vigia, autocura, backup-agent). Marque o que falha por depender do ambiente real (Tailscale, Telegram, domínios).
3. **Clone dos aplicativos:** na VPS real os repositórios privados usam chaves de deploy próprias (`github-<repo>` no `~/.ssh/config`). Para o ensaio, use a autenticação `gh` deste PC e **registre como achado se as chaves de deploy não estiverem no kit**: sem elas, uma máquina nova não clona.
4. **Segredos e ambiente:** monte `.secrets/` e `.env.vps` **somente a partir do que `KIT_RECUPERACAO.md` e a pasta de chaves prometem**, sem espiar o VPS. Cada arquivo que o Compose exigir e o kit não tiver é achado. (O kit lista `.env.docker` dos ambientes locais; confira se o `.env.vps` de produção está coberto.)
5. **Bancos:** suba só o Postgres de cada sistema, restaure o dump **mais recente do catálogo local** (`restaurar.py`/`cli.py`, relido com SHA-256) e rode os serviços `db-provision` e `migrate` na ordem do Compose. Marque o tempo por sistema.
6. **Aplicativos:** `docker compose up` de Conforto, Bancário, MegaSena e Renda, na ordem do `deploy.sh` (leia-o; ele confere CI e `/health` público, que não existem no ensaio, então reproduza só o essencial e diga o que pulou). Critério: contêineres `healthy` e `/health` em 200 em `localhost`.
7. **nginx e TLS simulados:** instale os vhosts (`vps/nginx/`, `instalar-nginx.sh`) com **certificado autoassinado** gerado na hora para os domínios do ensaio e `curl --resolve`. Meça o tempo e registre o que o vhost real assume (certificados em `/etc/letsencrypt`) que o kit não entrega.
8. **Wealthfolio:** restaure o volume do catálogo (`volume/*.tar.gz`, SHA-256 conferido) com a **chave mestra** apontada por `WF_SECRET_KEY_HOST_PATH`. Se a imagem exigir build, peça o OK (regra 4) e meça. Verifique que o SQLite e o cofre de tokens abrem **sem imprimir nenhum token**.
9. **Contrato entre sistemas:** com Bancário e Renda restaurados, rode o verificador do contrato (`manutencao/docs/contratos/verificar.py`) e a simulação do add-on (`WealthfolioTeste/addons/controle-patrimonial-sync/sim`) contra os endpoints `patrimonio/v4` **da cópia restaurada**. Isso prova que os sistemas voltam a conversar, que é o que `/health` não prova.
10. **Dados:** por sistema, compare o restaurado com a produção **em leitura** (contagem de linhas das tabelas principais, somas de valores, data do último registro; use as ferramentas `cb_*`, `crv_*` e `sql` do MCP `financas`). Diferença é esperada e deve ser **explicada pelo RPO** (idade do dump); diferença que o RPO não explique é achado grave.
11. **T-final:** todos os serviços `healthy`, contrato v4 ok, dados conferidos.

## Duas medições de RPO (as duas, lado a lado)

(a) **O que o catálogo local tinha** quando o desastre "aconteceu". (b) **O que existiria se a Camada 2 tivesse sincronizado logo depois do `backup-db.sh`**: rode `sincronizar-vps --todos` (é a tarefa agendada normal; só lê o VPS e grava no catálogo local) e repita só a restauração dos bancos para medir a diferença. A distância entre (a) e (b) é o valor de corrigir o achado de sincronização.

## O que entregar

Um relatório Markdown em `docs/` do BackupRestore, em PR (merge só com OK), com:

- **RTO de máquina medido**, total e por fase (a partir de `marcas.log`), e o gargalo número um.
- **RTO estimado completo** = medido + faixas externas (instância, DNS/TLS, Tailscale), com mínimo, provável e máximo e a fonte de cada faixa. Deixe claro que é uma **estimativa composta**, não uma medida.
- **RPO** em (a) e (b), por sistema.
- **O que não voltou ou voltou degradado**: `media_volume` do CB, qualquer segredo ausente do kit, chaves de deploy, certificados.
- **Achados** com a classificação de quem é o defeito, cada um com a correção proposta, separando (i) o que se corrige já em documentação ou inventário, no mesmo PR, e (ii) mudança de desenho (por exemplo, roteiro de bootstrap versionado, sincronização da Camada 2 após cada backup, backup do `media_volume`), que vira proposta à parte e não se implementa.
- **Recomendação**: o RTO provável é aceitável para uso pessoal e familiar? O que reduziria mais o tempo?

Ao final, descarte **só** `rto-vps1` (`wsl --unregister rto-vps1`, depois de confirmar o nome e com o OK do mantenedor), e confirme que a produção não mudou comparando `~/deploy.sh --status` antes e depois. Se o ensaio não puder ser concluído, entregue o que mediu e diga exatamente onde e por que parou.

## Critério de pronto

Servidor reconstruído em `rto-vps1` com os quatro aplicativos `healthy`, o contrato v4 passando contra os dados restaurados, o Wealthfolio abrindo com a chave mestra, tempos por fase vindos de `marcas.log`, e o relatório em PR.
