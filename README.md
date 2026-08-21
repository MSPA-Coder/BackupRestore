# BackupRestore

Backup dos projetos locais e ensaio de restauração em sandbox: dump PostgreSQL
e ZIP de código por projeto, com verificação de integridade. Também busca,
verifica e cataloga os dumps que o VPS de produção já produz sozinho
(Camada 2 do backup de produção) — nunca dispara `pg_dump` remoto nem toca em
contêiner de produção.

Roda no host (Python >=3.13; Python 3.14 atualmente testado, com Flask). Não é containerizado de propósito: é a
ferramenta que gerencia os contêineres dos outros projetos, e colocá-la dentro de
um exigiria montar o socket do Docker.

A versão testada não é um congelamento: a compatibilidade mínima e a atualização
do runtime serão declaradas de forma reproduzível, e cada evolução precisa passar
por verificação dos artefatos e ensaio de restauração.

**Dependência única: Flask.** Todo o resto é biblioteca padrão. A faixa
compatível está em `requirements.txt`; prepare o runtime explicitamente com:

```powershell
python -m pip install -r requirements.txt
```

---

## Uso

Para a interface, o caminho mais curto é **dar dois cliques em `iniciar.bat`** —
ele sobe o servidor e abre o navegador em http://127.0.0.1:5401. Feche a janela
para parar.

Pela linha de comando:

```bash
python cli.py backup --todos          # o que o Agendador chama (só os projetos locais)
python cli.py sincronizar-vps --todos # Camada 2: busca, verifica e cataloga os dumps do VPS
python cli.py listar                  # catálogo
python cli.py verificar               # relê os arquivos e confere SHA-256
python cli.py ensaio --projeto mega_sena   # restaura no sandbox e compara com a origem
python web.py                         # interface em http://127.0.0.1:5401
```

Os quatro projetos locais produzem o próprio backup (contêiner Docker). Os
quatro projetos `_vps` não — a produção acontece sozinha no servidor
(`_manutencao/vps/backup-db.sh`, systemd timer) e o `sincronizar-vps` só
busca, verifica e cataloga o que já existe lá, pelo agente restrito
(`_manutencao/vps/backup-agent.sh`). Configure o alvo uma vez com:

```powershell
python cli.py configurar-vps <host> --usuario ubuntu --chave 'C:\caminho\da\chave-dedicada'
```

O agente do servidor só sabe quatro verbos (`listar`, `enviar`, `apagar`,
`estado`) — este cliente nunca dispara `pg_dump` remoto nem toca em contêiner
de produção.

> **Atenção ao interpretador.** `iniciar.bat` e a tarefa agendada usam o runtime
> configurado no host porque o `PATH` pode apontar para o atalho da Microsoft
> Store ou para uma instalação sem Flask. Confirme o executável com
> `python --version` e `python -c "import flask"`; não trate um caminho interno
> de uma versão específica como contrato permanente.

Para restaurar de verdade, veja [RESTAURAR.md](RESTAURAR.md). A lista dos
arquivos confidenciais que precisam de protecao independente esta em
[KIT_RECUPERACAO.md](KIT_RECUPERACAO.md).

---

## Destino dos backups

O destino é definido antes do primeiro backup por quem opera o host, nunca pela
interface HTTP. Execute localmente:

```powershell
python cli.py configurar-raiz 'C:\caminho\dos\backups'
```

O comando grava a raiz e, por padrão, torna essa própria pasta o único limite
permitido. Se o operador precisar manter vários destinos sob uma pasta pai,
define o limite explicitamente:

```powershell
python cli.py configurar-raiz 'C:\backups\BackupRestore' --permitida 'C:\backups'
```

`BACKUPRESTORE_RAIZ_PERMITIDA`, definido no ambiente do processo que executa a
aplicação ou tarefa agendada, prevalece sobre o arquivo local e pode restringir
ainda mais esse limite. A tela **Configurações** só exibe o destino e o limite
efetivos. A raiz e o limite ficam em `configuracao.local.json`, que não entra no
Git. Instalações anteriores continuam válidas: a raiz já gravada passa a ser seu
limite até o operador executar o comando acima.

Para evitar que o catálogo passe a apontar para arquivos inexistentes, a troca
de destino é bloqueada depois que há artefatos catalogados; nesse caso, migre os
arquivos e o catálogo de forma consciente. Referências do catálogo que tentem
sair da raiz são recusadas e marcadas como corrompidas, nunca abertas ou
removidas.

### Rollback da configuração

O formato do catálogo e dos artefatos não mudou. Para reverter somente o código,
pare a interface, mantenha `configuracao.local.json` e use a versão anterior;
ela ignora o campo adicional de limite permitido. Não mova nem apague artefatos
como parte do rollback. Antes de retomar a rotina, rode `python cli.py verificar`
e um `python cli.py ensaio --projeto <slug>` no sandbox.

## O que é gravado

Dois artefatos por projeto, em `<raiz-configurada>\projects\<projeto>\` (a
raiz é a escolhida em Configurações — ver seção anterior):

| Tipo | Ferramenta | Por quê |
|---|---|---|
| `banco/*.dump` | `pg_dump --format=custom` | comprimido (6,7 MB contra 94 MB em SQL puro no ConfortoTermico) e aceita `pg_restore` seletivo |
| `codigo/*.zip` | Git + ZIP do aplicativo | arquivos rastreados e não ignorados, incluindo trabalho local permitido |

Cada um com `.manifest.json` ao lado (SHA-256, tamanho, origem, e para código o
`HEAD` e se havia trabalho não commitado).

Total atual: **32 MB** para os quatro projetos locais, incluindo dumps de
segurança.

**Os quatro projetos `_vps` só têm `banco/*.dump`** — sem `codigo/`, porque o
código de produção não é um artefato deste sistema (ver seção "Agendamento" e
[RESTAURAR.md](RESTAURAR.md)). O `.manifest.json` desses dumps registra o
servidor de origem em vez de um contêiner local, e o carimbo de tempo é
sempre o momento em que o servidor capturou o dado — não o do download.

---

## As sete regras

O que este projeto é, na prática. Estão em `motor.py` e `restaurar.py`.

1. **Escrita atômica.** Tudo nasce em `temp/` e só vira nome final por
   `os.replace`. Um dump interrompido nunca vira arquivo truncado com nome bom.
2. **Verificar antes de confiar.** Código de saída zero não prova nada: todo
   artefato é relido (`pg_restore --list`, `testzip`) antes
   de entrar no catálogo. Medido: dump truncado sai com exit 1, dump com bytes
   trocados derruba o `pg_restore` — ambos reprovados.
3. **Nunca apagar antes de ter o substituto.** A retenção roda depois da
   verificação do artefato novo, e nunca remove o último válido de um tipo.
4. **Devolver o contêiner ao estado em que estava**, em `finally`, inclusive
   quando o backup falha.
5. **Toda restauração começa por um dump de segurança** do destino, verificado.
   Se ele falhar, a restauração é abortada.
6. **Restauração exige o nome do banco digitado**, e os contêineres reais são
   recusados por par `(ambiente, contêiner)` — não há flag que libere. O par
   existe porque os contêineres do VPS usam os mesmos nomes dos locais; uma
   lista só de nomes os protegeria por coincidência, não por desenho.
7. **SHA-256 gravado e reconferível** por `python cli.py verificar`.

E a que valida as outras: **um artefato só serve se você já restaurou a partir
dele.** É o `cli.py ensaio`.

---

## Sandbox

`compose.teste.yaml` sobe um PostgreSQL descartável na porta 5439. É o único
destino de restauração que a ferramenta aceita.

O processo do sandbox roda como `postgres`, sem capabilities e com filesystem
raiz somente leitura; apenas o volume descartável de dados e os diretórios
transitórios em `tmpfs` permanecem graváveis. Isso não altera o procedimento de
descarte nem os artefatos de backup.

O sandbox também fica limitado a 2 vCPU — folga suficiente para o maior ensaio
de restauração local, sem permitir que um dump defeituoso ocupe todos os 20
vCPU disponíveis no host.

```bash
docker compose -f compose.teste.yaml up -d
docker compose -f compose.teste.yaml down -v    # descarta
```

## Verificação

As travas puras de restauração, catálogo e ZIP usam `unittest`, sem dependência
de desenvolvimento adicional. O ensaio real continua obrigatório quando a
mudança toca backup ou restauração:

```powershell
python -m unittest discover -s tests -v
python cli.py verificar
python cli.py ensaio --projeto <slug>
```

---

## CI

O workflow em GitHub Actions roda em push e pull request para `main`, por
disparo manual e semanalmente. Ele testa a versão mínima Python 3.13 e a versão
Python mais recente disponível, instala `requirements.txt`, executa os testes
unitários e compila as fontes. Não roda `verificar` ou `ensaio`, pois esses
comandos dependem dos artefatos e do sandbox locais. O Dependabot acompanha
pip e GitHub Actions semanalmente, agrupando atualizações minor/patch.

---

## Agendamento

O backup só vale se rodar sozinho. Registre as duas tarefas uma vez
(PowerShell): a local, e a que busca do VPS meia hora depois — dando folga
para o timer do servidor (03:00 America/Sao_Paulo) terminar de produzir o dia.

```powershell
schtasks /create /tn "BackupRestore" /tr '"C:\Users\MSPA\AppData\Local\Python\bin\python.exe" "C:\Users\MSPA\Dropbox\Programacao\VSCodeProjects\BackupRestore\cli.py" backup --todos' /sc daily /st 03:00
schtasks /create /tn "BackupRestoreVPS" /tr '"C:\Users\MSPA\AppData\Local\Python\bin\python.exe" "C:\Users\MSPA\Dropbox\Programacao\VSCodeProjects\BackupRestore\cli.py" sincronizar-vps --todos' /sc daily /st 03:30
```

Caminho completo do Python de propósito — o Agendador não roda no Git Bash, então
`python` puro pegaria o atalho da Loja e a tarefa falharia em silêncio. Depois de
criar, confira o resultado da primeira execução com `python cli.py listar`.

**A tarefa do VPS precisa do sandbox de pé** (`docker compose -f
compose.teste.yaml up -d`, uma vez — o script deixa o contêiner parado entre
usos, e a sincronização o liga sozinha quando precisa). Sem ele,
`sincronizar-vps` recusa com uma mensagem clara em vez de travar.

**Se `sincronizar-vps` falhar com `Permission denied (publickey)` mesmo com a
chave certa em `configuracao.local.json`**, o motivo quase sempre é a ACL do
arquivo da chave: o cliente OpenSSH do Windows recusa uma chave privada legível
por mais de um principal. Restrinja com:

```powershell
icacls "C:\caminho\da\chave-dedicada" /inheritance:r /grant:r "$env:USERDOMAIN\$env:USERNAME:R"
```

Uma chave criada por `ssh-keygen` num terminal Git Bash herda ACLs abertas da
pasta — funciona rodando à mão nesse mesmo terminal, mas falha em silêncio
quando o Agendador de Tarefas chama o `ssh.exe` real do Windows.

---

## Arquivos

```
projetos.py     os 8 projetos (4 locais + 4 de origem VPS) e os contêineres protegidos
banco.py        catálogo SQLite (3 tabelas, sem ORM, sem migrações)
motor.py        produção e verificação dos artefatos locais — o núcleo
restaurar.py    travas, dump de segurança e pg_restore
vps.py          Camada 2: busca, verifica e cataloga os dumps que o VPS produziu sozinho
cli.py          linha de comando
web.py          interface Flask
catalogo.sqlite3  fica fora da pasta de backup de propósito
```

`docs/historico/` guarda os documentos de decisão (`claude-ANALISE.md`,
`claude-JUNCAO.md`, `claude-PLANO.md`) e a especificação anterior
(`ESPECIFICACAO.md`) — nenhum deles é lido pelo código, e cada um tem uma
nota no topo sobre o que diverge do estado atual. `docs/prototipo/`
(`index.html`, `app.js`, `styles.css`) é o protótipo estático original,
inalcançável por qualquer rota do Flask; a interface atual reusa o CSS dele
em `static/css/style.css`.
