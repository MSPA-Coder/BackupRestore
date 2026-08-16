# BackupRestore

Backup dos projetos locais e ensaio de restauração em sandbox: dump PostgreSQL
e ZIP de código por projeto, com verificação de integridade.

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
python cli.py backup --todos          # o que o Agendador chama
python cli.py listar                  # catálogo
python cli.py verificar               # relê os arquivos e confere SHA-256
python cli.py ensaio --projeto mega_sena   # restaura no sandbox e compara com a origem
python web.py                         # interface em http://127.0.0.1:5401
```

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

O destino é escolhido na tela **Configurações** antes do primeiro backup. A
escolha fica em `configuracao.local.json`, que não entra no Git. Para evitar que
o catálogo passe a apontar para arquivos inexistentes, o aplicativo bloqueia a
troca depois que já há artefatos catalogados; nesse caso, a migração deve mover
os arquivos e o catálogo de forma consciente.

## O que é gravado

Dois artefatos por projeto, em `<raiz-configurada>\projects\<projeto>\` (a
raiz é a escolhida em Configurações — ver seção anterior):

| Tipo | Ferramenta | Por quê |
|---|---|---|
| `banco/*.dump` | `pg_dump --format=custom` | comprimido (6,7 MB contra 94 MB em SQL puro no ConfortoTermico) e aceita `pg_restore` seletivo |
| `codigo/*.zip` | Git + ZIP do aplicativo | arquivos rastreados e não ignorados, incluindo trabalho local permitido |

Cada um com `.manifest.json` ao lado (SHA-256, tamanho, origem, e para código o
`HEAD` e se havia trabalho não commitado).

Total atual: **32 MB** para os quatro projetos, incluindo dumps de segurança.

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
   recusados por lista — não há flag que libere.
7. **SHA-256 gravado e reconferível** por `python cli.py verificar`.

E a que valida as outras: **um artefato só serve se você já restaurou a partir
dele.** É o `cli.py ensaio`.

---

## Sandbox

`compose.teste.yaml` sobe um PostgreSQL descartável na porta 5439. É o único
destino de restauração que a ferramenta aceita.

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

## Agendamento

O backup só vale se rodar sozinho. Registre a tarefa uma vez (PowerShell como
administrador):

```powershell
schtasks /create /tn "BackupRestore" /tr "\"%LOCALAPPDATA%\Python\bin\python.exe\" \"C:\Users\MSPA\Dropbox\Programacao\VSCodeProjects\BackupRestore\cli.py\" backup --todos" /sc daily /st 03:00
```

Caminho completo do Python de propósito — o Agendador não roda no Git Bash, então
`python` puro pegaria o atalho da Loja e a tarefa falharia em silêncio. Depois de
criar, confira o resultado da primeira execução com `python cli.py listar`.

---

## Arquivos

```
projetos.py     os 4 projetos e os contêineres protegidos
banco.py        catálogo SQLite (3 tabelas, sem ORM, sem migrações)
motor.py        produção e verificação dos artefatos — o núcleo
restaurar.py    travas, dump de segurança e pg_restore
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
