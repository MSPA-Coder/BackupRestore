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

  // Todo texto que veio do servidor entra por textContent: nada de HTML montado
  // a partir de dados (o campo `erro` pode carregar saída de agente remoto).
  function no(tag, classe, texto, estilo) {
    const e = document.createElement(tag);
    if (classe) e.className = classe;
    if (estilo) e.setAttribute('style', estilo);
    if (texto !== undefined) e.textContent = texto;
    return e;
  }

  function desenhar(dados) {
    const progresso = Number(dados.progresso) || 0;
    if (dados.situacao === 'sucesso') {
      const rotulos = no('div');
      rotulos.append(no('span', 'context-label', 'BACKUP CONCLUÍDO'),
                     no('strong', null, 'Artefatos gravados e verificados'));
      const lista = no('a', 'secondary', 'Ver lista');
      lista.setAttribute('href', location.pathname);
      const contexto = no('div', 'context-bar', undefined,
                          'grid-template-columns:1fr auto');
      contexto.append(rotulos, lista);
      alvo.replaceChildren(contexto);
      return;
    }
    if (dados.situacao === 'falha') {
      const aviso = no('div', 'aviso-erro', undefined, 'margin-bottom:20px');
      aviso.append(no('strong', null, 'O backup falhou.'),
                   document.createTextNode(' ' + (dados.erro || 'sem detalhe')));
      alvo.replaceChildren(aviso);
      return;
    }
    const barra = no('div', 'barra-progresso');
    barra.append(no('i', null, undefined, 'width:' + progresso + '%'));
    const rotulos = no('div');
    rotulos.append(no('span', 'context-label', 'EM ANDAMENTO'),
                   no('strong', null, dados.fase || 'Preparando'),
                   barra);
    const contexto = no('div', 'context-bar rodando', undefined,
                        'grid-template-columns:1fr auto');
    contexto.append(rotulos, no('span', 'cell-value', progresso + '%'));
    alvo.replaceChildren(contexto);
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
