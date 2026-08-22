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
| `ControleBancario` | `.env.docker`; `.secrets/postgres_password`, `.secrets/django_secret_key`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |
| `ControleRendaVariavel` | `.env`; `.secrets/postgres_password`, `.secrets/secret_key`, `.secrets/collector_agent_token`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt`; se o agente RTD estiver instalado, `.docker-local/remote-collector.env` |
| `MegaSena` | `.env.docker`; `.secrets/postgres_password.txt`, `.secrets/secret_key.txt`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |
| `ConfortoTermico` | `.env.docker`; `.secrets/postgres_password.txt`, `.secrets/internal_token.txt`, `.secrets/secret_key.txt`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |

Confira sempre o README e o `compose.yaml` da versao restaurada: esse inventario
descreve o estado atual, nao substitui a configuracao versionada.

## VPS (producao)

Os arquivos abaixo vivem só no servidor, fora do Git — a Camada 2 do backup
(`vps.py`) **nunca os toca**. Copiar para o cofre continua sendo tarefa manual,
a mesma dos locais.

| Projeto (no VPS) | Fora do Git, indispensável |
|---|---|
| `controle-bancario` | `.env.vps`; `.secrets/postgres_password`, `.secrets/django_secret_key`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |
| `controle-renda-variavel` | `.env.vps`; `.secrets/postgres_password`, `.secrets/secret_key`, `.secrets/collector_agent_token`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |
| `mega-sena` | `.env.vps`; `.secrets/postgres_password.txt`, `.secrets/secret_key.txt`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |
| `conforto-termico` | `.env.vps`; `.secrets/postgres_password.txt`, `.secrets/internal_token.txt`, `.secrets/secret_key.txt`, `.secrets/github_token.txt`; `.certs/local-root-ca.crt` |

Não é o mesmo inventário dos locais acima. Confirme os nomes contra a
configuração da versão restaurada antes de cada ensaio.

O token do coletor de Renda Variável precisa corresponder no servidor e no
agente Windows. Preserve-o por canal seguro; não copie seu valor para este
documento, manifestos ou registros de ensaio.

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
