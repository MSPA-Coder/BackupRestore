# Kit externo de recuperacao

Os dumps recuperam os dados e os ZIPs recuperam o codigo, mas nenhum deles
deve carregar senhas, tokens, certificados ou chaves de sessao. Uma recuperacao
completa exige um pequeno kit externo, guardado separadamente da raiz de backup.

## Inventario minimo

Mantenha uma copia protegida dos diretorios locais abaixo, quando existirem:

| Projeto | Arquivos externos esperados |
|---|---|
| `ControleBancario` | `.secrets/postgres_password`, `.secrets/django_secret_key` |
| `ControleRendaVariavel` | `.secrets/postgres_password`, `.secrets/secret_key`, `.secrets/rtd_control_token` e a configuração operacional local do RTD descrita no README do projeto |
| `MegaSena` | `.secrets/postgres_password.txt`, `.secrets/secret_key.txt` |
| `ConfortoTermico` | `.secrets/postgres_password.txt`, `.secrets/internal_token.txt`; a chave de sessao gerada fica no volume persistente e pode ser recriada com perda apenas das sessoes ativas |

Confira sempre o README e o `compose.yaml` da versao restaurada: esse inventario
descreve o estado atual, nao substitui a configuracao versionada.

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
