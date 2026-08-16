// Acompanhamento de um backup em andamento.
//
// O progresso é lido do catálogo, onde o motor grava cada fase concluída. Não
// há temporizador nem estimativa: quando aparece "Bundle do código", é porque o
// dump do banco terminou e foi verificado.

(function () {
  const alvo = document.getElementById('acompanhamento');
  if (!alvo) return;

  const execucao = new URLSearchParams(location.search).get('execucao');
  if (!execucao) return;

  const FASES_FINAIS = ['sucesso', 'falha'];

  function desenhar(dados) {
    if (dados.situacao === 'sucesso') {
      alvo.innerHTML =
        '<div class="context-bar" style="grid-template-columns:1fr auto">' +
        '<div><span class="context-label">BACKUP CONCLUÍDO</span>' +
        '<strong>Artefatos gravados e verificados</strong></div>' +
        '<a class="secondary" href="' + location.pathname + '">Ver lista</a></div>';
      return;
    }
    if (dados.situacao === 'falha') {
      alvo.innerHTML =
        '<div class="aviso-erro" style="margin-bottom:20px"><strong>O backup falhou.</strong> ' +
        (dados.erro || 'sem detalhe') + '</div>';
      return;
    }
    alvo.innerHTML =
      '<div class="context-bar rodando" style="grid-template-columns:1fr auto">' +
      '<div><span class="context-label">EM ANDAMENTO</span>' +
      '<strong>' + (dados.fase || 'Preparando') + '</strong>' +
      '<div class="barra-progresso"><i style="width:' + (dados.progresso || 0) + '%"></i></div>' +
      '</div><span class="cell-value">' + (dados.progresso || 0) + '%</span></div>';
  }

  function consultar() {
    fetch('/api/execucao/' + execucao)
      .then(function (resposta) { return resposta.json(); })
      .then(function (dados) {
        desenhar(dados);
        if (FASES_FINAIS.indexOf(dados.situacao) === -1) {
          setTimeout(consultar, 1000);
        } else if (dados.situacao === 'sucesso') {
          // Recarrega uma vez para a lista de artefatos aparecer atualizada.
          setTimeout(function () { location.href = location.pathname; }, 1200);
        }
      })
      .catch(function () { setTimeout(consultar, 3000); });
  }

  consultar();
})();
