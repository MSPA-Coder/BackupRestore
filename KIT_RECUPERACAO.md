# Kit externo de recuperacao

Os dumps recuperam os dados e os ZIPs recuperam o codigo, mas nenhum deles
deve carregar senhas, tokens, certificados ou chaves de sessao. Uma recuperacao
completa exige um pequeno kit externo, guardado separadamente da raiz de backup.

## Inventario local minimo

Mantenha uma copia protegida dos arquivos locais abaixo. Os exemplos de
ambiente versionados ajudam a recriar a estrutura, mas não substituem os
arquivos operacionais nem os segredos provisionados.

| Projeto | Arquivos externos esperados |
|---|---|
| `ControleBancario` | `.env.docker`; `.secrets/postgres_password`, `.secrets/postgres_app_password`, `.secrets/django_secret_key`, `.secrets/patrimonio_token`, `.secrets/patrimonio_integration_token`; os quatro de qualidade `.secrets/quality_postgres_password`, `.secrets/quality_django_secret_key`, `.secrets/quality_patrimonio_token`, `.secrets/quality_patrimonio_integration_token`; `.certs/local-root-ca.crt` |
| `ControleRendaVariavel` | `.env`; `.secrets/postgres_password`, `.secrets/postgres_app_password`, `.secrets/secret_key`, `.secrets/database_url`, `.secrets/patrimonio_token`, `.secrets/patrimonio_integration_token`; o par do coletor `.secrets/collector_agent_read_token` e `.secrets/collector_agent_write_token`; os três de qualidade `.secrets/postgres_password_quality`, `.secrets/postgres_app_password_quality` e `.secrets/patrimonio_integration_token_quality`; `.certs/local-root-ca.crt`; se o agente RTD estiver instalado, `.docker-local/remote-collector.env` e `.docker-local/rtd-control-token` |
| `MegaSena` | `.env.docker`; `.secrets/postgres_password.txt`, `.secrets/secret_key.txt`; `.certs/local-root-ca.crt` |
| `ConfortoTermico` | `.env.docker`; `.secrets/postgres_password.txt`, `.secrets/secret_key.txt`; os três tokens internos `.secrets/internal_token.txt`, `.secrets/internal_control_token.txt`, `.secrets/internal_read_token.txt`; `.certs/local-root-ca.crt` |
| `MpPortal` | `.env`; `.secrets/postgres_password`, `.secrets/django_secret_key`; `.certs/local-root-ca.crt` |
| `BackupRestore` | `configuracao.local.json` (endereço do VPS, usuário e caminho da chave SSH) e `agendamento.local.json` |
| `WealthfolioTeste` (se houver instância local) | `.env`; o arquivo da **chave mestra**, apontado por `WF_SECRET_KEY_HOST_PATH` no `.env` e guardado fora do checkout |

Confira sempre o README e o `compose.yaml` da versao restaurada: esse inventario
descreve o estado atual, nao substitui a configuracao versionada.

### O catálogo NÃO entra no kit, de propósito

`catalogo.sqlite3` (300 KB) fica de fora. Ele muda a cada execução, então uma
cópia no kit envelhece entre uma regeneração manual e a seguinte, e um índice
velho sobre um acervo novo confunde mais do que ajuda.

Ele também não é insubstituível: cada artefato carrega ao lado um
`<arquivo>.manifest.json` com projeto, tipo, `criado_em`, `bytes` e o
`sha256` — que é exatamente o que `banco.registrar_artefato` grava. O catálogo
é reconstruível varrendo a raiz de backup e relendo esses manifestos.

**Não existe hoje um comando que faça essa reconstrução.** Enquanto não
existir, perder o catálogo custa escrever o script na hora do aperto. Se isso
parecer caro demais, a saída é criar o comando — não pôr o banco no kit.

O arquivo de ambiente do MpPortal chama-se `.env`; todos os outros usam
`.env.docker`. Procurar o nome dos demais ali nao acha nada.

As linhas do ControleBancario e do ControleRendaVariavel ganharam em 02/10/2026
os segredos que o `compose.yaml` de cada um declara e a tabela não tinha (o
`patrimonio_integration_token` das rotas v4, com o de qualidade, e o
`postgres_app_password` do ControleBancario): o Compose recusa subir sem eles.
Conferido contra os arquivos Compose, não contra o disco.

A tabela acima foi conferida contra o disco em 22/09/2026, e cinco das seis
linhas estavam desatualizadas: faltavam os segredos de qualidade do
ControleBancario e do Renda Variavel, os dois tokens internos extras do
ConfortoTermico, o `database_url`, a `senha-inicial.txt` do NetWorth e o
`rtd-control-token` do agente; o `collector_agent_token` virou um par
`read`/`write`; e o MpPortal usa `.env`, nao `.env.docker`.

O desvio nao era visivel: os segredos nasceram e mudaram de nome nos projetos,
e nada liga este documento a eles. **Confira esta tabela contra o disco sempre
que um projeto ganhar ou renomear um segredo** -- um inventario que lista
nomes que nao existem so e descoberto durante uma recuperacao, que e o pior
momento possivel.

**Os segredos que ligam sistemas precisam ser restaurados aos pares.**

- O `patrimonio_token` precisa existir: o Compose recusa subir com um segredo
  declarado e ausente.
- Os `patrimonio_integration_token` do ControleBancario e do ControleRendaVariavel
  (rotas v4) são os que o add-on do Wealthfolio guarda no cofre dele. Rotacionar
  um deles exige colar o valor novo na tela do add-on. O cofre fica dentro do
  volume do Wealthfolio, cifrado com a chave mestra: restaurar a cópia do
  volume com a mesma chave traz os tokens de volta, e eles só valem se os
  arquivos do CB e do CRV restaurados forem os da mesma época.
- **A chave mestra do Wealthfolio é o que torna a cópia do volume utilizável.**
  O banco SQLite e o cofre de segredos são cifrados com ela. A cópia diária
  (`volume/*.tar.gz`) sem a chave não abre, e a chave não pode ser recriada:
  guarde-a no cofre antes de precisar da cópia, não depois.
- O NetWorth (prova de conceito) foi aposentado em 29/09/2026. Os dumps antigos
  do banco dele ficam no acervo, sem retenção nem verificação novas.

## VPS (producao)

Os arquivos abaixo vivem só nos servidores, fora do Git — a Camada 2 do backup
(`vps.py`) **nunca os toca**. Copiar para o cofre continua sendo tarefa manual,
a mesma dos locais. Os cinco primeiros ficam no VPS compartilhado; o portal,
num VPS dedicado.

| Projeto (no VPS) | Fora do Git, indispensável |
|---|---|
| `controle-bancario` | `.env.vps`; `.secrets/postgres_password`, `.secrets/postgres_app_password`, `.secrets/django_secret_key`, `.secrets/patrimonio_token`, `.secrets/patrimonio_integration_token` (ou na pasta de `COMPOSE_SECRETS_DIRECTORY`, se o `.env.vps` a definir); `.certs/local-root-ca.crt` |
| `controle-renda-variavel` | `.env.vps` (inclui `PATRIMONIO_TITULAR`); `.secrets/postgres_password`, `.secrets/postgres_app_password`, `.secrets/secret_key`, o par do coletor `.secrets/collector_agent_read_token` e `.secrets/collector_agent_write_token`, `.secrets/patrimonio_token`, `.secrets/patrimonio_integration_token`; `.certs/local-root-ca.crt` |
| `wealthfolio-teste` | `.env` (este usa `.env`, não `.env.vps`: `WF_SECRET_KEY_HOST_PATH`, `WF_AUTH_PASSWORD_HASH`, `WF_ADDON_NETWORK_PRIVATE_TARGETS`); o arquivo da **chave mestra**, apontado por `WF_SECRET_KEY_HOST_PATH` e guardado fora do checkout |
| `mega-sena` | `.env.vps`; `.secrets/postgres_password.txt`, `.secrets/secret_key.txt`; `.certs/local-root-ca.crt` |
| `conforto-termico` | `.env.vps`; `.secrets/postgres_password.txt`, `.secrets/internal_token.txt`, `.secrets/secret_key.txt`; `.certs/local-root-ca.crt` |
| `mp-portal` (VPS dedicado) | `.env.vps`; `.secrets/postgres_password`, `.secrets/django_secret_key`; `.certs/local-root-ca.crt` |

Não é o mesmo inventário dos locais acima. Confirme os nomes contra a
configuração da versão restaurada antes de cada ensaio.

**Esta tabela do VPS nao foi conferida contra o disco de um servidor** -- nem
na revisao de 22/09/2026 nem depois. Em 02/10/2026 as linhas do
`controle-bancario` e do `controle-renda-variavel` foram acertadas pelos
segredos que o `compose.yaml` declara para os servicos que sobem sem o perfil
de qualidade (o mesmo arquivo roda no VPS), e a do `wealthfolio-teste`, pelo
`compose.yaml` e o `.env.example` dele. As demais seguem suspeitas ate
confirmadas num servidor.

O token do coletor de Renda Variável precisa corresponder no servidor e no
agente Windows. Preserve-o por canal seguro; não copie seu valor para este
documento, manifestos ou registros de ensaio.

**O dono e o modo dos arquivos também fazem parte da restauração.** O Compose
sem Swarm monta cada segredo com as permissões do host, e cada contêiner lê
com o seu próprio usuário:

- **`controle-bancario`:** o `patrimonio_token` leva o mesmo dono e modo do
  `django_secret_key` (`chown/chmod --reference`, com `sudo`). Com
  `ubuntu:600`, o deploy passa e a rota responde 503;
- **`controle-renda-variavel`:** os arquivos ficam com modo
  `644`, dentro de `.secrets/` com modo `700`.

## Estado de implantação

Preserve também `/home/ubuntu/.local/state/mspa-deploy/<projeto>.commit` para
cada projeto, ou um registro externo equivalente. Esse arquivo não é segredo:
ele registra o commit saudável mais recente selecionado pelo deploy e ajuda a
reconstruir a combinação de código e banco. O dump do BackupRestore não
incorpora esse SHA.

## Dados fora dos artefatos

O BackupRestore não cobre o volume Docker `media_volume` do
`controle-bancario`, onde ficam os comprovantes enviados pelos usuários. O
dump recupera as referências do banco, mas não os arquivos correspondentes.
Mantenha uma cópia independente e testada desses comprovantes, com retenção e
proteção compatíveis com a sensibilidade deles.

## Onde guardar

Use um cofre de credenciais ou uma midia cifrada sob controle do mantenedor,
fora da pasta dos projetos e fora da raiz configurada do BackupRestore. O kit
nao deve entrar no Git, no catalogo SQLite, nos manifestos, nos logs nem nos
ZIPs de codigo.

O mecanismo de cifra e a chave de recuperacao precisam ser independentes do
computador protegido. Copiar `.secrets/` para outra pasta no mesmo disco nao e
uma estrategia de recuperacao.

## Ensaio sem expor valores

Periodicamente, em uma pasta descartavel:

1. restaure o ZIP de codigo e o dump de banco;
2. recoloque os arquivos do kit com os nomes e permissoes documentados;
3. valide `docker compose config --quiet` e suba os servicos;
4. confirme healthchecks e uma operacao de leitura da aplicacao;
5. descarte somente o ambiente criado para o ensaio.

Registre apenas data, resultado e identidade dos arquivos conferidos. Nunca
registre o conteudo dos segredos ou uma URL de banco com senha.
