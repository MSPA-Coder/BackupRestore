# Orientacoes do projeto

## Papel e fontes de verdade

BackupRestore roda no host Windows e coordena backup e ensaio de restauracao dos
quatro projetos web locais, alem de buscar e catalogar os dumps que o VPS
produz sozinho (Camada 2 do backup de producao). Ele nao e containerizado
porque isso exigiria conceder a um container acesso privilegiado ao daemon
Docker.

- `README.md`: instalacao, operacao e formato dos artefatos.
- `RESTAURAR.md`: recuperacao manual e limites do backup.
- `projetos.py`: os oito projetos (quatro locais, quatro de origem VPS, campo
  `ambiente`) e os containers protegidos.
- `motor.py` e `restaurar.py`: contratos de integridade e restauracao dos
  projetos locais.
- `vps.py`: Camada 2 — busca, verifica e cataloga dumps do VPS por SSH via um
  agente restrito no servidor (`listar`/`enviar`/`apagar`/`estado`); nunca
  dispara `pg_dump` remoto nem toca em container de producao.

Confirme o comportamento no codigo antes de alterar documentacao. Pedido atual
do mantenedor e requisitos de seguranca e preservacao de dados prevalecem sobre
decisoes historicas.

## Ambiente e versoes

A aplicacao usa Python no host como excecao deliberada ao padrao container-first.
O interpretador deve satisfazer a versao minima declarada pelo projeto e importar
as dependencias instaladas; nao dependa de uma instalacao global escolhida por
acidente pelo `PATH`. `iniciar.bat` seleciona, nesta ordem,
`BACKUPRESTORE_PYTHON`, `%LOCALAPPDATA%\Python\bin\python.exe` e `python` do
`PATH`; nao introduza fallback para diretorio interno de versao especifica.

Versoes documentadas como "testadas" sao um estado operacional atual, nao um
congelamento. Declare separadamente a versao minima suportada, a versao testada e
a proxima candidata. Atualize de forma deliberada, executando a verificacao e um
ensaio de restauracao. O PostgreSQL do sandbox fixa a versao principal porque
mudancas de formato de dados exigem migracao explicita.

Nunca instale dependencias silenciosamente no host. Se o runtime configurado
estiver ausente ou invalido, diagnostique e proponha uma instalacao reproduzivel.

## Invariantes de seguranca e recuperacao

Qualquer mudanca em `motor.py`, `restaurar.py`, `banco.py`, `projetos.py` ou
`vps.py` preserva estes contratos:

1. Artefatos nascem em diretorio temporario e so recebem o nome final por troca
   atomica depois de verificados.
2. Dumps e ZIPs sao relidos; codigo de saida zero, sozinho, nao basta.
3. Retencao ocorre apenas depois de existir substituto valido e nunca remove o
   ultimo artefato valido de um tipo.
4. O estado original do container e restaurado em `finally`, inclusive em falha.
5. Restauracao comeca por dump de seguranca verificado do destino.
6. O nome do banco precisa ser confirmado e somente `backuprestore-sandbox`
   pode ser destino da restauração automática, sem flag de bypass.
7. SHA-256, tamanho e origem permanecem verificaveis pelo catalogo.

A raiz de backup e seu limite permitido sao definidos somente pelo operador no
host (`cli.py configurar-raiz` ou a variavel de processo
`BACKUPRESTORE_RAIZ_PERMITIDA`). A interface em `127.0.0.1` e somente leitura
para essa configuracao. Caminhos relativos do catalogo devem ser resolvidos e
confirmados sob a raiz antes de leitura, remocao ou restauracao.

O sandbox `backuprestore-sandbox` e o unico destino autorizado para restauracao
automatica. Nunca aponte ensaios aos volumes operacionais. `down -v` somente e
permitido para o Compose descartavel `compose.teste.yaml`, depois de confirmar o
nome do projeto Compose.

Segredos, certificados e arquivos ignorados nao fazem parte do ZIP de codigo.
Nao os adicione sem uma estrategia de cifra, acesso e recuperacao aprovada.

## Validacao proporcional

Comandos de referencia, usando o runtime Python configurado:

```powershell
python -m unittest discover -s tests -v
python cli.py verificar
python cli.py ensaio --projeto <slug>
```

- Documentacao ou interface sem efeito operacional: confira links, comandos e o
  fluxo afetado.
- Catalogo e regras puras: execute os testes automatizados relacionados.
- Backup, retencao, restauracao ou selecao de alvo: execute testes, `verificar`
  e ao menos um `ensaio` real no sandbox.
- Mudanca no sandbox: valide `docker compose -f compose.teste.yaml config
  --quiet`, suba o servico, aguarde o health check e descarte somente o volume
  do sandbox ao concluir.

Nao substitua o ensaio de restauracao apenas por mocks. Testes pequenos devem
proteger selecao de alvo, bloqueios, retencao e falhas antes do ensaio real.

## Pratica de mudanca

- Preserve alteracoes locais nao relacionadas e nunca registre segredos.
- Mantenha mudancas pequenas, reversiveis e com um unico objetivo.
- Mudancas de formato do catalogo ou artefato precisam de compatibilidade,
  migracao ou procedimento de rollback explicito.
- Atualize README/RESTAURAR quando operacao ou recuperacao mudarem.
- Não adicione diários de implementação ou planos concluídos às instruções vivas;
  o histórico de mudanças já permanece no Git.
- Ao concluir, informe comandos executados, resultados e validacoes omitidas.
