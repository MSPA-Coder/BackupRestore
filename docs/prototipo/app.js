const basePath = 'C:\\Users\\MSPA\\Dropbox\\Programacao\\VSCodeProjects';

const projects = [
  { name:'Controle Bancário', folder:'ControleBancario', slug:'controle_bancario', database:'controle_bancario', db:'Hoje, 09:42', code:'Hoje, 09:45', status:'Íntegro', dbCount:10, codeCount:10 },
  { name:'Controle Renda Variável', folder:'ControleRendaVariavel', slug:'controle_renda_variavel', database:'renda_variavel', db:'Ontem, 22:01', code:'Ontem, 22:03', status:'Íntegro', dbCount:10, codeCount:9 },
  { name:'Conforto Térmico', folder:'ConfortoTermico', slug:'conforto_termico', database:'conforto_termico', db:'07 ago, 18:35', code:'07 ago, 18:38', status:'Atenção', dbCount:8, codeCount:8 },
  { name:'Mega-Sena', folder:'MegaSena', slug:'mega_sena', database:'mega_sena', db:'06 ago, 08:15', code:'06 ago, 08:16', status:'Íntegro', dbCount:10, codeCount:10 }
].map(project => ({ ...project, path:`${basePath}\\${project.folder}` }));

let currentProject = projects[0];
let currentType = 'database';
let toastTimer;

function statusTag(status) {
  const style = status === 'Atenção' ? 'warn' : status === 'Falhou' ? 'fail' : '';
  return `<span class="tag ${style}">${status}</span>`;
}

function projectRows(withHeader = false) {
  const header = withHeader ? '<div class="project-row"><span>Projeto</span><span>Último banco</span><span>Último código</span><span>Estado</span></div>' : '';
  return header + projects.map((project,index) => `
    <div class="project-row">
      <div><div class="project-name">${project.name}</div><div class="project-path" title="${project.path}">${project.path}</div></div>
      <div><span class="cell-label">Banco</span><span class="cell-value">${project.db}</span></div>
      <div><span class="cell-label">Código</span><span class="cell-value">${project.code}</span></div>
      <div class="state-cell">${statusTag(project.status)}<button class="open-link" data-project="${index}">Abrir →</button></div>
    </div>`).join('');
}

function backupRows(type) {
  const isDatabase = type === 'database';
  const suffix = isDatabase ? 'dump' : 'zip';
  const sizes = isDatabase ? ['124 MB','121 MB','119 MB'] : ['42 MB','41 MB','41 MB'];
  const dates = [['2026-08-09_0942','Hoje, 09:42'],['2026-08-08_2200','Ontem, 22:00'],['2026-08-07_2200','07 ago, 22:00']];
  return dates.map((date,index) => ({
    file:`${currentProject.slug}_${date[0]}.${suffix}`,
    date:date[1], size:sizes[index], format:isDatabase ? 'PostgreSQL custom' : 'ZIP + manifesto', status:'Íntegro'
  }));
}

function renderBackups() {
  const isDatabase = currentType === 'database';
  document.getElementById('backup-heading').textContent = isDatabase ? 'Backups de banco' : 'Backups de código';
  document.getElementById('backup-subtitle').textContent = isDatabase ? 'Dumps PostgreSQL disponíveis para restauração controlada.' : 'Pacotes ZIP gerados pelo aplicativo conforme as regras do Git.';
  document.getElementById('db-count').textContent = currentProject.dbCount;
  document.getElementById('code-count').textContent = currentProject.codeCount;
  document.getElementById('backup-list').innerHTML = backupRows(currentType).map((backup,index) => `
    <div class="backup-row">
      <div><div class="backup-file">${backup.file}</div><div class="backup-date">${backup.date}</div></div>
      <div><span class="cell-label">Tamanho</span><span class="cell-value">${backup.size}</span></div>
      <div><span class="cell-label">Formato</span><span class="cell-value">${backup.format}</span></div>
      <div>${statusTag(backup.status)}</div>
      <button class="secondary" data-backup="${index}">${isDatabase ? 'Restaurar' : 'Ver detalhes'}</button>
    </div>`).join('');
}

function renderProject() {
  document.getElementById('detail-title').textContent = currentProject.name;
  document.getElementById('detail-path').textContent = currentProject.path;
  renderBackups();
}

function showView(view, updateHash = true) {
  const target = document.getElementById(view) ? view : 'dashboard';
  document.querySelectorAll('.view').forEach(element => element.classList.toggle('active', element.id === target));
  document.querySelectorAll('[data-view-link]').forEach(link => link.classList.toggle('active', link.dataset.viewLink === target));
  const names = { dashboard:'Visão geral', projects:'Projetos', backups:'Backups', restore:'Restaurar', integrity:'Integridade', retention:'Retenção', history:'Histórico', settings:'Configurações', 'project-detail':currentProject.name };
  document.getElementById('breadcrumb').textContent = names[target];
  if (updateHash && target !== 'project-detail') history.pushState(null,'',`#${target}`);
  window.scrollTo({ top:0, behavior:'smooth' });
}

function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = `${message}. Protótipo: nenhuma alteração foi realizada.`;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'),2800);
}

function openBackupDialog(index) {
  const backup = backupRows(currentType)[index];
  const isDatabase = currentType === 'database';
  const dialog = document.getElementById('backup-dialog');
  document.getElementById('dialog-eyebrow').textContent = isDatabase ? 'RESTAURAÇÃO DE BANCO' : 'DETALHES DO PACOTE';
  document.getElementById('dialog-title').textContent = backup.file;
  document.getElementById('dialog-description').textContent = isDatabase ? `Destino: ${currentProject.database} em localhost:5432` : 'O pacote foi produzido pelo aplicativo usando os arquivos aceitos pelas regras do Git.';
  document.getElementById('dialog-data').innerHTML = `<div><span>Projeto</span><strong>${currentProject.name}</strong></div><div><span>Gerado em</span><strong>${backup.date}</strong></div><div><span>Tamanho</span><strong>${backup.size}</strong></div><div><span>Integridade</span><strong>Validada</strong></div>`;
  document.getElementById('restore-warning').hidden = !isDatabase;
  dialog.querySelector('.dialog-actions .primary').textContent = isDatabase ? 'Continuar restauração' : 'Abrir pasta';
  dialog.showModal();
}

document.getElementById('project-summary').innerHTML = projectRows();
document.getElementById('projects-table').innerHTML = projectRows(true);
document.getElementById('history-list').innerHTML = [
  ['Hoje, 09:45','Backup de código','Controle Bancário','Concluído'],
  ['Hoje, 09:42','Backup de banco','Controle Bancário','Concluído'],
  ['Ontem, 22:03','Backup de código','Controle Renda Variável','Concluído'],
  ['07 ago, 18:35','Backup de banco','Conforto Térmico','Falhou']
].map(item => `<div class="history-row"><span class="backup-date">${item[0]}</span><span class="operation">${item[1]}</span><span class="cell-value">${item[2]}</span>${statusTag(item[3] === 'Falhou' ? 'Falhou' : 'Íntegro')}</div>`).join('');

const allArtifacts = [
  { file:'controle_bancario__database__20260814T124200Z.dump', project:'Controle Bancário', type:'Banco', size:'124 MB', date:'Hoje, 09:42', hash:'bd01b8e3f8c2', status:'Íntegro' },
  { file:'controle_bancario__code__20260814T124500Z.zip', project:'Controle Bancário', type:'Código', size:'42 MB', date:'Hoje, 09:45', hash:'2f65e49d99ac', status:'Íntegro' },
  { file:'conforto_termico__database__20260807T213500Z.dump', project:'Conforto Térmico', type:'Banco', size:'81 MB', date:'07 ago, 18:35', hash:'eaa913ce20fd', status:'Atenção' }
];
function artifactRows(mode = 'catalog') {
  return allArtifacts.map((artifact, index) => {
    const action = mode === 'restore' ? `<button class="primary" data-backup="${index % 3}">Preparar restauração</button>` : '<button class="open-link" data-go="project-detail">Ver →</button>';
    const column = mode === 'integrity' ? `<span class="sha">${artifact.hash}…</span><span>${artifact.date}</span>` : `<span>${artifact.size}</span><span>${statusTag(artifact.status)}</span><span>${artifact.date}</span>`;
    return `<div class="artifact-row"><div class="backup-file">${artifact.file}</div><span>${artifact.project}</span><span>${artifact.type}</span>${column}${mode === 'integrity' ? statusTag(artifact.status) : ''}${action}</div>`;
  }).join('');
}
document.getElementById('artifact-list').innerHTML = artifactRows();
document.getElementById('restore-list').innerHTML = artifactRows('restore');
document.getElementById('integrity-list').innerHTML = artifactRows('integrity');
document.getElementById('retention-list').innerHTML = projects.map(project => `<article class="retention-card"><div class="card-topline"><span class="project-initials">${project.name.slice(0,2).toUpperCase()}</span>${statusTag(project.status)}</div><h2>${project.name}</h2><p class="muted">Retenção configurada: 10 por tipo.</p><div class="retention-counts"><div><span>Banco</span><strong>${project.dbCount} <small>/ 10</small></strong></div><div><span>Código</span><strong>${project.codeCount} <small>/ 10</small></strong></div></div><button class="open-link" data-project="${projects.indexOf(project)}">Ver artefatos →</button></article>`).join('');

document.addEventListener('click', event => {
  const navigation = event.target.closest('[data-go]');
  if (navigation) showView(navigation.dataset.go);
  const projectButton = event.target.closest('[data-project]');
  if (projectButton) { currentProject = projects[Number(projectButton.dataset.project)]; renderProject(); showView('project-detail',false); }
  const tab = event.target.closest('[data-tab]');
  if (tab) { currentType = tab.dataset.tab; document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active',item === tab)); renderBackups(); }
  const backupButton = event.target.closest('[data-backup]');
  if (backupButton) openBackupDialog(Number(backupButton.dataset.backup));
  const demo = event.target.closest('[data-demo]');
  if (demo) { document.getElementById('backup-dialog').close(); showToast(demo.dataset.demo); }
});

document.querySelectorAll('.dialog-close,.dialog-close-action').forEach(button => button.addEventListener('click',() => document.getElementById('backup-dialog').close()));
window.addEventListener('hashchange',() => showView(location.hash.slice(1) || 'dashboard',false));
renderProject();
showView(location.hash.slice(1) || 'dashboard',false);
