# Ensaio de recuperação do VPS1 — 03/10/2026

## Escopo e veredito

Este foi um **ensaio parcial, sem custo**, no sandbox Docker local `backuprestore-sandbox`. Ele mediu a restauração dos dumps que já estavam no catálogo local e conferiu-os contra a produção somente por consultas de leitura. Não criou VM, DNS, certificado, Tailscale, nem implantou as aplicações em uma máquina nova.

**RTO ponta a ponta: não medido.** O número que este ensaio pode sustentar é o tempo da fase de restauração dos quatro bancos: **15 segundos** (3,54 s + 3,80 s + 3,55 s + 3,74 s). Não é correto apresentar esse número como o RTO de desastre: ele não inclui provisionamento, recuperação de segredos, clone, build, deploy, DNS nem certificado.

O sandbox oficial já existia e estava saudável. A tentativa de criar outro identificou corretamente o conflito do nome protegido e não substituiu nem removeu o contêiner existente. Nenhum contêiner de desenvolvimento ou de produção foi usado como destino.

## Linha do tempo medida

| Fase | Resultado | Tempo |
|---|---|---:|
| Conferir o sandbox e resolver o conflito de nome | sandbox oficial, saudável | não cronometrado isoladamente |
| Restaurar Conforto Térmico | 21 tabelas com dados, 81 linhas | 3,54 s |
| Restaurar Controle Bancário | 38 tabelas com dados, 6.881 linhas | 3,80 s |
| Restaurar Controle Renda Variável | 25 tabelas com dados, 46.603 linhas | 3,55 s |
| Restaurar Mega-Sena | 8 tabelas com dados, 3.106 linhas | 3,74 s |
| Conferências de origem e do volume Wealthfolio | consultas somente leitura; SHA-256 válido | não incluído no RTO de restauração |

Não houve espera humana durante a execução Docker. Uma simulação integral em VM deverá iniciar o cronômetro em T0 e registrar separadamente toda espera humana para criar e liberar a máquina, DNS e Tailscale.

## RPO observado

Hora de referência do ensaio: 03/10/2026 13:49 BRT. Os artefatos restaurados eram as cópias locais da Camada 2, portanto são os dados que existiriam após a perda do VPS1.

| Sistema | Artefato local usado | RPO aproximado no ensaio | Integridade |
|---|---|---:|---|
| Conforto Térmico | 03:00:29 BRT | 10 h 49 min | válido e restaurado |
| Controle Bancário | 02:40:50 BRT | 11 h 09 min | válido e restaurado |
| Controle Renda Variável | 03:00:30 BRT | 10 h 49 min | válido e restaurado |
| Mega-Sena | 02:40:50 BRT | 11 h 09 min | válido e restaurado |
| Wealthfolio | volume de 02:40:51 BRT | 11 h 08 min | dois artefatos relidos; SHA-256 válido |

O agente do VPS informou cópias mais recentes, às 09:36Z (06:36 BRT), para os quatro bancos e para o volume Wealthfolio. Elas ainda não estavam no catálogo local. Assim, a diferença entre a captura no VPS e a última cópia recuperável fora dele era cerca de quatro horas e não pode ser ignorada na avaliação do RPO.

## Verificação dos dados

As consultas abaixo foram leitura na produção e leitura no banco restaurado. Os horários do Controle Renda Variável representam o mesmo instante (origem em UTC e sandbox em BRT).

| Sistema e indicador | Produção | Restaurado | Resultado |
|---|---|---|---|
| Conforto Térmico: `historico.leituras` (linhas, soma `valor`, último registro) | 0, 0, sem registro | 0, 0, sem registro | igual; o indicador não tem dados para validar mais profundamente |
| Controle Bancário: `cash_flow_entry` | 1.544; 1.745.863,47; 03/10 09:12 BRT | 1.542; 1.745.863,47; 02/10 17:39 BRT | **diverge em duas linhas**; soma igual |
| Controle Renda Variável: `position_movements` | 35; 0; 02/10 19:37 UTC | 35; 0; 02/10 16:37 BRT | igual |
| Mega-Sena: `draws` | 3.065; 1.849.911.458.610; 01/10 | igual | igual |

A divergência no Controle Bancário é compatível com o RPO do artefato: houve atividade depois da cópia restaurada. O ensaio não tentou corrigi-la.

## Wealthfolio e dados que não voltam automaticamente

Ao contrário da lacuna histórica, o catálogo atual contém cópias de volume do Wealthfolio e sua releitura passou. Ainda assim, a recuperação completa só é possível com a chave mestra externa indicada por `WF_SECRET_KEY_HOST_PATH` e com o `.env`. Sem ela, o SQLite cifrado e o cofre de tokens não abrem. Este ensaio não extraiu o volume nem iniciou o Wealthfolio porque não havia ambiente isolado completo com esses segredos.

Os comprovantes do Controle Bancário (`media_volume`) continuam fora dos artefatos verificados; o dump recupera apenas suas referências. Também não foi provado neste ensaio que o inventário externo contém todos os segredos e permissões necessários para cada aplicação.

## Achados e correções propostas

1. O plano `PLANO_BACKUPRESTORE_VPS.md`, citado nas anotações do mantenedor, não existe mais em lugar nenhum (nem em `_manutencao`, nem no antigo caminho do Dropbox). Nenhum documento versionado deste repositório depende dele; o roteiro de recuperação passa a ser [ROTEIRO_ENSAIO_RTO.md](ROTEIRO_ENSAIO_RTO.md), versionado aqui.
2. A sincronização da Camada 2 deixou cópias recentes disponíveis somente no VPS. Documentar o RPO como o artefato local mais recente, não como a última execução de `backup-db.sh`; como mudança de desenho, sincronizar após cada backup remoto ou alertar quando a defasagem passar do limite aceito.
3. Criar um roteiro específico de ensaio em VM com marcadores T0/T-final, responsável por cada passo manual e uma forma segura de disponibilizar os segredos. Esse roteiro é requisito para medir o RTO integral.
4. Manter e testar a cópia independente de `media_volume` do Controle Bancário.
5. Fazer um ensaio separado do Wealthfolio: restaurar o volume em Docker descartável, usar a chave mestra pelo mecanismo aprovado e validar a reimportação do add-on `patrimonio/v4` sem registrar segredos.

## Produção e descarte

Produção não sofreu alteração: foram executados apenas `listar`, `estado`, consultas SQL de leitura, `docker ps` e `~/deploy.sh --status`. Ao final, o status mostrou os quatro serviços do VPS1 e o Wealthfolio saudáveis (HTTP 200 onde aplicável). O sandbox Docker e os recursos criados para ele foram preservados; nenhum volume foi removido.
