# Como restaurar

Procedimento verificado em 2026-08-11 nos quatro projetos. Os comandos abaixo
foram executados de verdade, não são exemplo.

Nada aqui depende do BackupRestore estar funcionando. Se sobrou a pasta de
backup e você tem Git e Docker, isto basta.

---

## Onde estão as coisas

```
<raiz-configurada>\projects\<projeto>\
    banco\            <projeto>_banco_<data>.dump      (pg_dump --format=custom)
    codigo\           <projeto>_codigo_<data>.zip      (arquivos não ignorados pelo Git)
    pre_restauracao\  <projeto>_seguranca_<data>.dump  (dumps automáticos pré-restauração)
```

A raiz é definida pelo operador com `python cli.py configurar-raiz` e fica em
`configuracao.local.json` (fora do Git) — confira ali qual é a atual antes de
procurar os arquivos. A interface web apenas a exibe; não pode alterá-la.

Cada artefato tem um `.manifest.json` ao lado com SHA-256, tamanho e origem.
Os `<projeto>` são `conforto_termico`, `mega_sena`, `controle_bancario` e
`controle_renda_variavel`.

---

## Reconstrução completa, do zero

O ZIP permite recuperar o código rastreado e o estado Git registrado no
manifesto. A reconstrução completa também depende dos arquivos externos
enumerados em "O que o backup não cobre". Antes de uma emergência, mantenha o
[kit externo de recuperação](KIT_RECUPERACAO.md) em cofre ou mídia cifrada
independente da raiz de backup.

**1. Código**

```bash
python -c "import zipfile; zipfile.ZipFile(r'<backup>\projects\<projeto>\codigo\<arquivo>.zip').extractall(r'<PastaDoProjeto>')"
```

Isso restaura só o que o Git rastreava e não ignorava — `.env`, certificados e
outros segredos ficam fora (ver "O que o backup não cobre" abaixo) e precisam
ser configurados conscientemente no destino antes do próximo passo.

**2. Subir só o banco**, com o volume vazio:

```bash
docker compose up -d postgres
```

Antes do resto: se a aplicação subir junto, as migrações criam um esquema vazio
que o `pg_restore` vai derrubar em seguida. Funciona, mas restaurar sobre banco
limpo é mais previsível.

**3. Restaurar os dados**

```bash
docker exec -i <container-postgres> pg_restore --no-owner --no-acl --exit-on-error -U <usuario> -d <banco> < <arquivo>.dump
```

Contêineres e usuários de cada projeto estão em `projetos.py`.

**4. Subir a aplicação**

```bash
docker compose up -d
```

---

## Conferir um artefato antes de confiar nele

O host não tem `pg_restore` — e não precisa ter. Qualquer contêiner
`postgres:17-alpine` serve de ferramenta:

```bash
docker exec -i backuprestore-sandbox pg_restore --list < arquivo.dump
python -c "import zipfile; print(zipfile.ZipFile(r'arquivo.zip').testzip())"
```

Ambos falham em arquivo truncado ou com bytes trocados; foi medido.

Para conferir tudo de uma vez contra o SHA-256 do catálogo:

```bash
python cli.py verificar
```

---

## Ensaiar sem risco

O sandbox (`compose.teste.yaml`) é um PostgreSQL descartável na porta 5439. O
comando abaixo restaura o dump mais recente lá dentro e compara as contagens de
linha com o projeto original:

```bash
python cli.py ensaio --projeto conforto_termico
```

**Restaurar sobre um projeto real não é possível pela ferramenta.** Os quatro
contêineres reais estão em `CONTAINERS_PROTEGIDOS` (`projetos.py`) e
`restaurar.py` os recusa antes de ler qualquer coisa — não há flag que libere.
Fazer isso é trabalho manual, com o passo 4 acima, conscientemente.

---

## O que o backup não cobre

- **Arquivos ignorados.** O ZIP segue o `.gitignore`; arquivos como `.env` ficam
  fora quando forem ignorados pelo projeto.
- **Os volumes Docker em si.** Restaura-se o *conteúdo* dos bancos via dump, não
  o volume. É o que se quer: dump é portável entre versões e máquinas.
- **Arquivos ignorados pelo Git.** O backup de código respeita as regras do
  projeto. Se uma reconstrução exigir `.env`, certificados ou outros segredos,
  eles devem ser configurados conscientemente no destino; não entram no ZIP por
  padrão.

Veja [KIT_RECUPERACAO.md](KIT_RECUPERACAO.md) para o inventário operacional.
