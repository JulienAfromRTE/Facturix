var currentMapping=null;
var currentIndex=null;
var tooltip=document.getElementById('tooltip');
var mappingsIndex = { mappings: [] };
var mappingToDelete = null;
var pendingAuditCallback = null; // callback à exécuter après saisie de l'auteur

function positionTooltip(e){
var margin=12;
var vpW=window.innerWidth;
var vpH=window.innerHeight;
var scrollY=window.scrollY||document.documentElement.scrollTop;
var scrollX=window.scrollX||document.documentElement.scrollLeft;
// Mesure la taille réelle du tooltip (il est visible à ce stade)
var tw=tooltip.offsetWidth;
var th=tooltip.offsetHeight;
var x=e.pageX+margin;
var y=e.pageY+margin;
// Dépasse à droite → coller à gauche du curseur
if(x+tw>scrollX+vpW-margin){x=e.pageX-tw-margin;}
// Dépasse en bas → afficher au-dessus du curseur
if(y+th>scrollY+vpH-margin){y=e.pageY-th-margin;}
// Garde dans les limites hautes/gauches
if(y<scrollY+margin){y=scrollY+margin;}
if(x<scrollX+margin){x=scrollX+margin;}
tooltip.style.left=x+'px';
tooltip.style.top=y+'px';
}

/* ---- ONGLETS ---- */
document.getElementById('tabControle').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentControle').classList.add('active');
});
document.getElementById('tabParam').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentParam').classList.add('active');
loadMappings();
loadGlobalSettings();
});

/* ── Global settings toggle (schematron on/off) ────────────────────────── */
async function loadGlobalSettings(){
  try{
    var resp=await fetch(BASE+'/api/rules');
    if(!resp.ok)return;
    var data=await resp.json();
    var cb=document.getElementById('settingSchematronEnabled');
    if(cb){cb.checked=data.schematron_enabled!==false;}
  }catch(e){console.warn('loadGlobalSettings',e);}
}

document.getElementById('settingSchematronEnabled').addEventListener('change',async function(){
  var enabled=this.checked;
  try{
    var resp=await fetch(BASE+'/api/rules');
    var data=resp.ok?await resp.json():{};
    delete data.categories;  // injecté par GET, pas à renvoyer
    data.schematron_enabled=enabled;
    var save=await fetch(BASE+'/api/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(save.ok){
      var ind=document.getElementById('settingSaveIndicator');
      if(ind){ind.style.opacity='1';setTimeout(function(){ind.style.opacity='0';},1500);}
    }
  }catch(e){alert('Erreur sauvegarde paramètre: '+e.message);}
});
document.getElementById('tabRules').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentRules').classList.add('active');
loadRules();
});
document.getElementById('tabAide').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentAide').classList.add('active');
});
document.getElementById('tabBatch').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentBatch').classList.add('active');
});
document.getElementById('tabStats').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentStats').classList.add('active');
statsLoadAll();
});

/* ============================================================
   STATISTIQUES
   ============================================================ */
var STATS_TYPE_LABELS = {
  'simple':'CART Simple',
  'groupee':'CART Groupée',
  'ventesdiverses':'Ventes Diverses',
  'flux':'Flux Générique',
  'CARTsimple':'CART Simple'
};
var STATS_PALETTE = ['#4f46e5','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#84cc16','#f97316'];
var statsState = { lastSummary:null, lastTrend:null, lastTopKo:null, lastHistory:null };

function statsLabel(t){ return STATS_TYPE_LABELS[t] || (t || 'inconnu'); }
function statsPctClass(p){
  if(p>=75) return 'pct-75';
  if(p>=50) return 'pct-50';
  if(p>=25) return 'pct-25';
  return 'pct-0';
}
function statsBuildQuery(){
  var p = new URLSearchParams();
  var t = document.getElementById('statsFilterType').value;
  var m = document.getElementById('statsFilterMode').value;
  var s = document.getElementById('statsFilterStart').value;
  var e = document.getElementById('statsFilterEnd').value;
  if(t && t!=='all') p.set('type',t);
  if(m) p.set('mode',m);
  if(s) p.set('start',s);
  if(e) p.set('end',e);
  return p.toString() ? ('?'+p.toString()) : '';
}

async function statsLoadAll(){
  await statsLoadTypeOptions();
  await Promise.all([
    statsLoadSummary(),
    statsLoadTrend(),
    statsLoadTopKo(),
    statsLoadHistory()
  ]);
}

async function statsLoadTypeOptions(){
  try{
    var r = await fetch(BASE+'/api/stats/types');
    var d = await r.json();
    // Enrichit la table de libellés avec ceux résolus côté serveur
    if(d.labels){
      Object.keys(d.labels).forEach(function(k){ STATS_TYPE_LABELS[k] = d.labels[k]; });
    }
    var sel = document.getElementById('statsFilterType');
    var current = sel.value || 'all';
    sel.innerHTML = '<option value="all">Tous les types</option>';
    (d.types||[]).forEach(function(t){
      var id = (typeof t === 'string') ? t : t.id;
      var label = (typeof t === 'string') ? statsLabel(t) : (t.label || statsLabel(t.id));
      var o = document.createElement('option');
      o.value = id; o.textContent = label;
      sel.appendChild(o);
    });
    sel.value = current;
  }catch(e){}
}

async function statsLoadSummary(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/summary'+qs);
  var d = await r.json();
  statsState.lastSummary = d;
  document.getElementById('kpiTotal').textContent = d.total_invoices||0;
  document.getElementById('kpiAvgPct').textContent = ((d.avg_conformity_pct||0).toFixed(1))+'%';
  document.getElementById('kpiErrors').textContent = d.nb_errors||0;
  var unit = (d.by_mode||[]).find(function(x){return x.mode==='unitaire';});
  var batch= (d.by_mode||[]).find(function(x){return x.mode==='batch';});
  document.getElementById('kpiUnitaire').textContent = unit?unit.count:0;
  document.getElementById('kpiBatch').textContent = batch?batch.count:0;
  document.getElementById('kpiTypes').textContent = (d.by_type||[]).length;

  // Range hint (bornes globales)
  var hint = document.getElementById('statsRangeHint');
  if(d.date_min && d.date_max){
    hint.textContent = 'Données du '+d.date_min+' au '+d.date_max;
  } else {
    hint.textContent = 'Aucune donnée enregistrée pour le moment';
  }

  // Tableau par type
  var rows = (d.by_type||[]).map(function(x){
    var byMode = (d.by_type_mode||[]).filter(function(y){return y.type===x.type;});
    var u = (byMode.find(function(y){return y.mode==='unitaire';})||{}).count||0;
    var b = (byMode.find(function(y){return y.mode==='batch';})||{}).count||0;
    var pct = (x.avg_pct||0).toFixed(1);
    return '<tr>'
      +'<td style="font-weight:600">'+statsLabel(x.type)+'</td>'
      +'<td>'+x.count+'</td>'
      +'<td>'+u+'</td>'
      +'<td>'+b+'</td>'
      +'<td>'
        +'<div style="display:flex;align-items:center;gap:8px;min-width:160px">'
          +'<div class="progress-track" style="flex:1;height:10px"><div class="progress-fill '+statsPctClass(x.avg_pct||0)+'" style="width:'+(x.avg_pct||0)+'%"></div></div>'
          +'<span style="font-weight:700;color:#1e293b;width:48px;text-align:right">'+pct+'%</span>'
        +'</div>'
      +'</td>'
    +'</tr>';
  }).join('');
  if(!rows){
    document.getElementById('statsByType').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucune donnée pour les filtres sélectionnés.</p>';
  } else {
    document.getElementById('statsByType').innerHTML =
      '<table class="main-table" style="margin-top:6px">'
      +'<thead><tr><th>Type</th><th>Total</th><th>Unitaire</th><th>Batch</th><th>Taux moyen</th></tr></thead>'
      +'<tbody>'+rows+'</tbody></table>';
  }
}

async function statsLoadTrend(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/conformity-trend'+qs);
  var d = await r.json();
  statsState.lastTrend = d;
  statsRenderTrend(d);
}

function statsRenderTrend(d){
  var wrap = document.getElementById('statsChartWrap');
  var legend = document.getElementById('statsChartLegend');
  var dates = d.dates || [];
  var series = d.series || [];
  if(!dates.length){
    wrap.innerHTML = '<p style="color:#94a3b8;font-style:italic;padding:30px;text-align:center">Aucune donnée à afficher pour ces filtres.</p>';
    legend.innerHTML = '';
    return;
  }
  // Dimensions
  var W = wrap.clientWidth || 800;
  var H = 280;
  var padL = 44, padR = 16, padT = 18, padB = 36;
  var innerW = W - padL - padR;
  var innerH = H - padT - padB;
  var n = dates.length;
  var xFor = function(i){ return n<=1 ? padL+innerW/2 : padL + i*(innerW/(n-1)); };
  var yFor = function(p){ return padT + innerH - (Math.max(0,Math.min(100,p))/100)*innerH; };

  // Grille horizontale
  var grid = '';
  [0,25,50,75,100].forEach(function(g){
    var y = yFor(g);
    grid += '<line x1="'+padL+'" y1="'+y+'" x2="'+(W-padR)+'" y2="'+y+'" stroke="#e2e8f0" stroke-width="1"/>';
    grid += '<text x="'+(padL-6)+'" y="'+(y+4)+'" text-anchor="end" font-size="10" fill="#94a3b8">'+g+'%</text>';
  });

  // Axe X (étiquettes : début / milieu / fin pour limiter le bruit)
  var labelIdx = [0];
  if(n>=3){ labelIdx.push(Math.floor(n/2)); labelIdx.push(n-1); }
  else if(n===2){ labelIdx.push(1); }
  var xLabels = labelIdx.map(function(i){
    return '<text x="'+xFor(i)+'" y="'+(H-padB+18)+'" text-anchor="middle" font-size="10" fill="#64748b">'+dates[i]+'</text>';
  }).join('');

  // Tracer chaque série
  var paths = '';
  var dots = '';
  var legendItems = '';
  series.forEach(function(s, idx){
    var color = STATS_PALETTE[idx % STATS_PALETTE.length];
    var pts = [];
    s.points.forEach(function(p, i){
      if(p.pct === null || p.pct === undefined) return;
      pts.push({x: xFor(i), y: yFor(p.pct), pct: p.pct, count: p.count, date: dates[i]});
    });
    if(pts.length){
      var dPath = pts.map(function(pt,i){ return (i===0?'M':'L')+pt.x+' '+pt.y; }).join(' ');
      paths += '<path d="'+dPath+'" fill="none" stroke="'+color+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
      pts.forEach(function(pt){
        var title = statsLabel(s.type)+' — '+pt.date+' : '+pt.pct.toFixed(1)+'% ('+pt.count+' factures)';
        dots += '<circle cx="'+pt.x+'" cy="'+pt.y+'" r="3.5" fill="#fff" stroke="'+color+'" stroke-width="2"><title>'+title+'</title></circle>';
      });
    }
    legendItems += '<span style="display:inline-flex;align-items:center;gap:6px"><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:'+color+'"></span>'+statsLabel(s.type)+'</span>';
  });

  wrap.innerHTML = '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:'+H+'px;display:block">'
    + grid + xLabels + paths + dots + '</svg>';
  legend.innerHTML = legendItems;
}

async function statsLoadTopKo(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/top-ko'+qs+(qs?'&':'?')+'limit=15');
  var d = await r.json();
  statsState.lastTopKo = d;
  var items = d.items || [];
  if(!items.length){
    document.getElementById('statsTopKo').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucun champ KO sur ce périmètre — soit aucune facture, soit toutes conformes 🎉</p>';
    return;
  }
  var maxTotal = items.reduce(function(m,x){return Math.max(m,x.total||0);},1);
  var rows = items.map(function(x){
    var pct = Math.round(100*(x.total||0)/maxTotal);
    var oblig = (x.obligatoire==='Oui') ? '<span style="color:#ef4444;font-weight:700;font-size:0.78em">obligatoire</span>' : '<span style="color:#94a3b8;font-size:0.78em">optionnel</span>';
    return '<tr>'
      +'<td style="font-weight:700;color:#1e293b">'+x.balise+'</td>'
      +'<td style="color:#475569">'+(x.libelle||'')+'</td>'
      +'<td>'+statsLabel(x.type_formulaire)+'</td>'
      +'<td>'+oblig+'</td>'
      +'<td style="text-align:right;font-weight:600">'+(x.nb_erreur||0)+'</td>'
      +'<td style="text-align:right;color:#d97706;font-weight:600">'+(x.nb_ambigu||0)+'</td>'
      +'<td>'
        +'<div style="display:flex;align-items:center;gap:8px;min-width:140px">'
          +'<div class="progress-track" style="flex:1;height:8px"><div class="progress-fill pct-0" style="width:'+pct+'%;background:linear-gradient(90deg,#ef4444,#f87171)"></div></div>'
          +'<span style="font-weight:700;color:#ef4444;width:32px;text-align:right">'+x.total+'</span>'
        +'</div>'
      +'</td>'
    +'</tr>';
  }).join('');
  document.getElementById('statsTopKo').innerHTML =
    '<table class="main-table">'
    +'<thead><tr><th>BT</th><th>Libellé</th><th>Type</th><th></th><th style="text-align:right">Erreurs</th><th style="text-align:right">Ambigus</th><th>Occurrences</th></tr></thead>'
    +'<tbody>'+rows+'</tbody></table>';
}

async function statsLoadHistory(){
  var qs = statsBuildQuery();
  var r = await fetch(BASE+'/api/stats/history'+qs+(qs?'&':'?')+'limit=50');
  var d = await r.json();
  statsState.lastHistory = d;
  var items = d.items || [];
  if(!items.length){
    document.getElementById('statsHistory').innerHTML = '<p style="color:#94a3b8;font-style:italic">Aucun contrôle enregistré pour ces filtres.</p>';
    return;
  }
  var rows = items.map(function(x){
    var pct = (x.conformity_pct||0).toFixed(1);
    var ts = (x.timestamp||'').replace('T',' ').slice(0,16);
    var status = x.error
      ? '<span style="color:#ef4444;font-weight:600">⚠ '+(x.error||'').slice(0,40)+'</span>'
      : (x.erreur>0 ? '<span style="color:#ef4444">'+x.erreur+' KO</span>' : '<span style="color:#10b981">OK</span>');
    return '<tr>'
      +'<td style="white-space:nowrap;font-family:monospace;font-size:0.85em">'+ts+'</td>'
      +'<td>'+statsLabel(x.type_formulaire)+'</td>'
      +'<td>'+(x.mode||'')+'</td>'
      +'<td style="font-family:monospace">'+(x.invoice_number||'—')+'</td>'
      +'<td style="color:#64748b;font-size:0.85em">'+(x.filename||'')+'</td>'
      +'<td style="text-align:right">'+(x.total||0)+'</td>'
      +'<td>'+status+'</td>'
      +'<td><div style="display:flex;align-items:center;gap:6px;min-width:120px">'
        +'<div class="progress-track" style="flex:1;height:8px"><div class="progress-fill '+statsPctClass(x.conformity_pct||0)+'" style="width:'+(x.conformity_pct||0)+'%"></div></div>'
        +'<span style="font-weight:700;width:42px;text-align:right">'+pct+'%</span>'
      +'</div></td>'
    +'</tr>';
  }).join('');
  document.getElementById('statsHistory').innerHTML =
    '<table class="main-table">'
    +'<thead><tr><th>Date</th><th>Type</th><th>Mode</th><th>N° facture</th><th>Fichier</th><th style="text-align:right">Total</th><th>Statut</th><th>Conformité</th></tr></thead>'
    +'<tbody>'+rows+'</tbody></table>';
}

function statsExportCsv(){
  var items = (statsState.lastHistory && statsState.lastHistory.items) || [];
  if(!items.length){ alert('Aucune ligne à exporter.'); return; }
  var hdr = ['date','type','mode','invoice_number','filename','total','ok','erreur','ignore','ambigu','conformity_pct','error'];
  var esc = function(v){
    if(v===null||v===undefined) return '';
    var s = String(v).replace(/"/g,'""');
    return /[",;\n]/.test(s) ? '"'+s+'"' : s;
  };
  var lines = [hdr.join(';')].concat(items.map(function(x){
    return [x.timestamp,x.type_formulaire,x.mode,x.invoice_number,x.filename,x.total,x.ok,x.erreur,x.ignore_count,x.ambigu,x.conformity_pct,x.error].map(esc).join(';');
  }));
  var blob = new Blob(["﻿"+lines.join('\n')], {type:'text/csv;charset=utf-8'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'facturix-historique.csv';
  document.body.appendChild(a); a.click(); a.remove();
}

document.getElementById('btnStatsApply').addEventListener('click', statsLoadAll);
document.getElementById('btnStatsReset').addEventListener('click', function(){
  document.getElementById('statsFilterType').value = 'all';
  document.getElementById('statsFilterMode').value = '';
  document.getElementById('statsFilterStart').value = '';
  document.getElementById('statsFilterEnd').value = '';
  statsLoadAll();
});
document.getElementById('btnStatsExportCsv').addEventListener('click', statsExportCsv);
window.addEventListener('resize', function(){
  if(document.getElementById('contentStats').classList.contains('active') && statsState.lastTrend){
    statsRenderTrend(statsState.lastTrend);
  }
});

/* ============================================================
   BATCH MODE
   ============================================================ */
// batchFilesMap : clé = numéro de facture (ou "tmp_N") → {key, invoiceNumber, pdfFile, rdiFile, pdfName, rdiName, pending}
var batchFilesMap={};
var batchTmpCounter=0;

// ── Dropzone setup ──────────────────────────────────────────
(function(){
  var dz=document.getElementById('batchDropZone');
  if(!dz)return;
  dz.addEventListener('dragover',function(e){e.preventDefault();dz.classList.add('drag-over');});
  dz.addEventListener('dragleave',function(){dz.classList.remove('drag-over');});
  dz.addEventListener('drop',function(e){
    e.preventDefault();dz.classList.remove('drag-over');
    batchHandleFileInput(e.dataTransfer.files);
  });
  // Empêcher le click du bouton interne de remonter deux fois
  dz.querySelector('button').addEventListener('click',function(e){e.stopPropagation();document.getElementById('batchFileInput').click();});
})();

document.getElementById('batchTypeControle').onchange=function(){batchUpdateDzHint();batchRenderFileList();batchUpdateLaunchBtn();};

function batchUpdateDzHint(){
  var mode=document.getElementById('batchTypeControle').value;
  var hints={xml:'PDF + RDI — les numéros de facture sont détectés automatiquement',rdi:'Fichiers RDI (.txt/.rdi) uniquement',xmlonly:'Fichiers PDF uniquement',cii:'Fichiers XML CII uniquement'};
  var el=document.getElementById('batchDzHint');
  if(el)el.textContent=hints[mode]||hints.xml;
  var inp=document.getElementById('batchFileInput');
  if(mode==='rdi')inp.accept='.txt,.rdi';
  else if(mode==='xmlonly')inp.accept='.pdf,.xml';
  else if(mode==='cii')inp.accept='.xml';
  else inp.accept='.pdf,.xml,.txt,.rdi';
}

async function batchHandleFileInput(files){
  if(!files||files.length===0)return;
  var arr=Array.from(files);
  // Afficher section fichiers immédiatement
  document.getElementById('batchFilesSection').style.display='block';
  // Ajouter chaque fichier en mode pending, puis résoudre
  var promises=arr.map(function(f){return batchAddFile(f);});
  await Promise.all(promises);
  batchRenderFileList();
  batchUpdateLaunchBtn();
  // Reset l'input pour permettre de re-sélectionner les mêmes fichiers
  document.getElementById('batchFileInput').value='';
}

async function batchAddFile(file){
  var ext=file.name.toLowerCase().split('.').pop();
  var isRdi=(ext==='txt'||ext==='rdi');
  var isPdf=(ext==='pdf'||ext==='xml');
  // Appel preview pour obtenir le N° de facture
  var invoiceNumber=null;
  try{
    var fd=new FormData();fd.append('file',file);
    fd.append('type_formulaire',document.getElementById('batchTypeFormulaire').value);
    var resp=await fetch(BASE+'/api/batch-preview',{method:'POST',body:fd});
    if(resp.ok){var d=await resp.json();invoiceNumber=d.invoice_number||null;}
  }catch(e){}
  // Clé : numéro de facture si dispo, sinon tmp
  var key=invoiceNumber||('tmp_'+(batchTmpCounter++));
  if(!batchFilesMap[key]){
    batchFilesMap[key]={key:key,invoiceNumber:invoiceNumber,pdfFile:null,rdiFile:null,pdfName:null,rdiName:null};
  } else if(invoiceNumber&&batchFilesMap[key].invoiceNumber!==invoiceNumber){
    // Collision de clé tmp → forcer nouveau
    key='tmp_'+(batchTmpCounter++);
    batchFilesMap[key]={key:key,invoiceNumber:invoiceNumber,pdfFile:null,rdiFile:null,pdfName:null,rdiName:null};
  }
  if(isRdi){batchFilesMap[key].rdiFile=file;batchFilesMap[key].rdiName=file.name;}
  else if(isPdf){batchFilesMap[key].pdfFile=file;batchFilesMap[key].pdfName=file.name;}
}

function batchRenderFileList(){
  var mode=document.getElementById('batchTypeControle').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var keys=Object.keys(batchFilesMap);
  var section=document.getElementById('batchFilesSection');
  if(keys.length===0){section.style.display='none';return;}
  section.style.display='block';
  // En-tête
  var head=document.getElementById('batchFilesHead');
  var headCols='<tr><th>N° Facture</th>';
  if(needPdf)headCols+='<th>PDF</th>';
  if(needRdi)headCols+='<th>RDI</th>';
  headCols+='<th>Statut</th><th style="width:36px"></th></tr>';
  head.innerHTML=headCols;
  // Corps
  var body=document.getElementById('batchFilesBody');
  body.innerHTML='';
  keys.forEach(function(key){
    var e=batchFilesMap[key];
    var hasPdf=!!e.pdfFile;
    var hasRdi=!!e.rdiFile;
    var ready=(!needPdf||hasPdf)&&(!needRdi||hasRdi);
    var hasExtra=(needPdf&&hasPdf)||(needRdi&&hasRdi);
    var tr=document.createElement('tr');
    // Colonne N° facture
    var numCell='<td><div class="batch-file-num">'+(e.invoiceNumber?escHtml(e.invoiceNumber):'<span style="color:#94a3b8;font-weight:400;font-style:italic">Inconnu</span>')+'</div></td>';
    // Colonnes fichiers
    var pdfCell='',rdiCell='';
    if(needPdf)pdfCell='<td>'+(hasPdf?'<span class="batch-file-chip pdf">📄 '+escHtml(e.pdfName)+'</span>':'<span class="batch-file-chip missing">— manquant</span>')+'</td>';
    if(needRdi)rdiCell='<td>'+(hasRdi?'<span class="batch-file-chip rdi">📋 '+escHtml(e.rdiName)+'</span>':'<span class="batch-file-chip missing">— manquant</span>')+'</td>';
    // Statut
    var statusLabel=ready?'✓ Prêt':(hasExtra?'⚠ Incomplet':'⚠ Vide');
    var statusClass=ready?'ok':'warn';
    var statusCell='<td><span class="batch-status-chip '+statusClass+'">'+statusLabel+'</span></td>';
    // Supprimer
    var removeCell='<td><button class="btn-batch-remove" data-key="'+escHtml(key)+'" title="Supprimer">✕</button></td>';
    tr.innerHTML=numCell+pdfCell+rdiCell+statusCell+removeCell;
    tr.querySelector('.btn-batch-remove').addEventListener('click',function(){
      delete batchFilesMap[this.dataset.key];
      batchRenderFileList();
      batchUpdateLaunchBtn();
    });
    body.appendChild(tr);
  });
}

function batchUpdateLaunchBtn(){
  var btn=document.getElementById('btnLaunchBatch');
  if(!btn)return;
  var mode=document.getElementById('batchTypeControle').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var all=Object.values(batchFilesMap);
  var ready=all.filter(function(e){return(!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile);}).length;
  var incomplete=all.filter(function(e){return !((!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile));}).length;
  var total=ready+incomplete;
  if(total>0){
    btn.style.opacity='1';btn.style.pointerEvents='auto';
    var lbl='▶ Lancer le contrôle ('+ready+' facture'+(ready>1?'s':'');
    if(incomplete>0)lbl+=' · '+incomplete+' incomplète'+(incomplete>1?'s':'')+' ignorée'+(incomplete>1?'s':'');
    lbl+=')';
    btn.textContent=lbl;
  } else {
    btn.style.opacity='0.5';btn.style.pointerEvents='none';btn.textContent='▶ Lancer le contrôle (0 facture)';
  }
}

async function batchLaunch(){
  var mode=document.getElementById('batchTypeControle').value;
  var typeForm=document.getElementById('batchTypeFormulaire').value;
  var needPdf=(mode!=='rdi');
  var needRdi=(mode!=='xmlonly'&&mode!=='cii');
  var fd=new FormData();
  fd.append('type_formulaire',typeForm);fd.append('type_controle',mode);
  var count=0;
  var skipped=[];
  Object.values(batchFilesMap).forEach(function(e){
    if((!needPdf||!!e.pdfFile)&&(!needRdi||!!e.rdiFile)){
      if(e.pdfFile)fd.append('pdf_'+count,e.pdfFile);
      if(e.rdiFile)fd.append('rdi_'+count,e.rdiFile);
      fd.append('name_'+count,e.rdiName||e.pdfName||('Facture '+(count+1)));
      fd.append('invoice_number_'+count,e.invoiceNumber||'');
      count++;
    } else {
      // Fichier sans paire — ignoré, on mémorise pour le rapport
      var fname=e.rdiName||e.pdfName||e.key;
      var missing=[];
      if(needPdf&&!e.pdfFile)missing.push('PDF manquant');
      if(needRdi&&!e.rdiFile)missing.push('RDI manquant');
      skipped.push({name:fname,invoiceNumber:e.invoiceNumber,reason:missing.join(', ')});
    }
  });
  fd.append('pair_count',count);
  fd.append('skipped_json',JSON.stringify(skipped));
  document.getElementById('batchLoading').style.display='block';
  document.getElementById('batchResults').style.display='none';
  document.getElementById('batchLoadingMsg').textContent='Contrôle en cours… ('+count+' facture'+(count>1?'s':'')+')';
  try{
    var resp=await fetch(BASE+'/controle-batch',{method:'POST',body:fd});
    var data=await resp.json();
    if(data.error){alert('Erreur: '+data.error);return;}
    data.skipped=skipped;
    batchRenderResults(data);
  }catch(e){alert('Erreur réseau: '+e);}
  finally{document.getElementById('batchLoading').style.display='none';}
}

function batchRenderResults(data){
  var batch=data.batch||[];
  var skipped=data.skipped||[];
  var now=new Date();
  var dateStr=now.toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'})
    +' '+now.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
  document.getElementById('batchResultsSub').textContent=
    batch.length+' facture'+(batch.length>1?'s':'')+' analysée'+(batch.length>1?'s':'')+' · '+dateStr;

  // Bandeau fichiers ignorés
  var skippedEl=document.getElementById('batchSkippedWarning');
  if(skippedEl){
    if(skipped.length>0){
      var skippedRows=skipped.map(function(s){
        var num=s.invoiceNumber?(' <span style="font-weight:700">N°'+escHtml(s.invoiceNumber)+'</span>'):'';
        return '<li>'+escHtml(s.name)+num+' — <em>'+escHtml(s.reason)+'</em></li>';
      }).join('');
      skippedEl.innerHTML='<span style="font-weight:700">⚠ '+skipped.length+' fichier'+(skipped.length>1?'s':'')+' ignoré'+(skipped.length>1?'s':'')+' (paire incomplète) :</span><ul style="margin:6px 0 0 16px;padding:0">'+skippedRows+'</ul>';
      skippedEl.style.display='block';
    } else {
      skippedEl.style.display='none';
    }
  }

  // Stats globales
  var nbTotal=batch.length;
  var nbOk=batch.filter(function(b){return !b.error&&b.stats&&b.stats.erreur===0;}).length;
  var nbErr=batch.filter(function(b){return b.error||(b.stats&&b.stats.erreur>0);}).length;
  var totalErreurs=batch.reduce(function(acc,b){return acc+(b.stats?b.stats.erreur:0);},0);
  var statsEl=document.getElementById('batchStatsGlobal');
  statsEl.innerHTML=
    '<div class="stat-card" style="min-width:110px"><div>Factures</div><div class="stat-value" style="color:#667eea">'+nbTotal+'</div></div>'+
    '<div class="stat-card erreur"><div>Avec erreurs</div><div class="stat-value">'+nbErr+'</div></div>'+
    '<div class="stat-card ok"><div>Sans erreur</div><div class="stat-value">'+nbOk+'</div></div>'+
    '<div class="stat-card" style="border-color:#fca5a5"><div>Erreurs totales</div><div class="stat-value" style="color:#ef4444">'+totalErreurs+'</div></div>';

  // Tableau
  var tbody=document.getElementById('batchTableBody');
  tbody.innerHTML='';

  batch.forEach(function(inv,i){
    var invRow=document.createElement('div');
    invRow.className='batch-inv-row';

    if(inv.error){
      // Ligne en erreur technique
      var mainDiv=document.createElement('div');
      mainDiv.className='batch-inv-main inv-error';
      mainDiv.innerHTML=
        '<div><div class="batch-inv-name">'+escHtml(inv.name)+'<span class="bsub">Erreur technique</span></div></div>'+
        '<div class="batch-sc" colspan="3" style="grid-column:span 3;color:#f59e0b">—</div>'+
        '<div></div>'+
        '<div style="padding:0 6px;color:#f59e0b;font-size:0.82em">⚠ '+escHtml(inv.error)+'</div>'+
        '<div></div>';
      invRow.appendChild(mainDiv);
      tbody.appendChild(invRow);
      return;
    }

    var stats=inv.stats||{};
    var nbOkInv=stats.ok||0;
    var nbErrInv=stats.erreur||0;
    var nbAmbInv=stats.ambigu||0;
    var nbTotInv=stats.total||1;
    var pct=Math.round(nbOkInv/nbTotInv*100);
    var pctClass=pct>=90?'good':(pct>=70?'mid':'bad');

    // Tags d'erreur (les BTs en ERREUR)
    var errTags='';
    if(nbErrInv===0&&nbAmbInv===0){
      errTags='<span style="font-size:0.82em;color:#10b981;font-weight:600;padding:0 6px">✅ Aucune erreur</span>';
    } else {
      var errResults=(inv.results||[]).filter(function(r){return r.status==='ERREUR'||r.status==='AMBIGU';});
      var shown=errResults.slice(0,6);
      shown.forEach(function(r){
        errTags+='<span class="batch-etag'+(r.status==='AMBIGU'?' amb':'')+'">'+escHtml(r.balise)+'</span>';
      });
      if(errResults.length>6){
        errTags+='<span class="batch-etag-more" onclick="batchToggleDetail('+i+')" style="cursor:pointer">+'+(errResults.length-6)+' autres…</span>';
      }
    }

    // N° de facture depuis BT-1 des résultats (RDI en priorité, sinon XML)
    var bt1res=(inv.results||[]).find(function(r){return r.balise==='BT-1';});
    var invoiceNum=inv.invoice_number||(bt1res?(bt1res.rdi||bt1res.xml||''):'');

    var mainDiv=document.createElement('div');
    mainDiv.className='batch-inv-main '+(nbErrInv>0?'has-err':'all-ok');
    mainDiv.innerHTML=
      '<div><div class="batch-inv-num">'+(invoiceNum?escHtml(invoiceNum):'<span style="color:#94a3b8;font-weight:400;font-style:italic;font-size:0.85em">N° inconnu</span>')+'</div><div class="batch-inv-filename" data-fullname="'+escHtml(inv.name)+'">'+escHtml(inv.name)+'</div></div>'+
      '<div class="batch-sc ok">'+nbOkInv+'</div>'+
      '<div class="batch-sc err">'+nbErrInv+'</div>'+
      '<div class="batch-sc amb">'+nbAmbInv+'</div>'+
      '<div class="batch-pct-wrap"><div class="batch-pct-track"><div class="batch-pct-fill '+pctClass+'" style="width:'+pct+'%"></div></div><span class="batch-pct-lbl '+pctClass+'">'+pct+'%</span></div>'+
      '<div style="padding:0 6px;display:flex;flex-wrap:wrap;align-items:center;gap:2px">'+errTags+'</div>'+
      '<div style="padding:0 6px"><button class="btn-batch-detail" id="batchDetailBtn_'+i+'" onclick="batchToggleDetail('+i+')"><span class="b-arrow">▶</span> Détail</button></div>';

    // Zone détail
    var detailZone=document.createElement('div');
    detailZone.className='batch-detail-zone';
    detailZone.id='batchDetailZone_'+i;
    if(inv.categories_results){
      var actionsBar='<div style="display:flex;gap:8px;margin-bottom:14px;align-items:center;flex-wrap:wrap">'+
        '<button class="btn-secondary" onclick="batchExpandAll(\''+i+'\')" style="font-size:0.8em;padding:5px 11px">Tout déplier</button>'+
        '<button class="btn-secondary" onclick="batchCollapseAll(\''+i+'\')" style="font-size:0.8em;padding:5px 11px">Tout replier</button>'+
        '<span style="font-size:0.78em;color:#94a3b8;margin-left:auto">'+(invoiceNum?escHtml(invoiceNum)+' — ':'')+escHtml(inv.name)+' · '+nbTotInv+' champs</span>'+
        '</div>';
      var catHtml=batchBuildCategoriesHTML(inv.categories_results,inv.type_controle,'b'+i+'_');
      detailZone.innerHTML=actionsBar;
      // Panneau Schematron en première position (suffixe d'id pour éviter les collisions)
      if(inv.schematron){appendSchematronPanel(detailZone, inv.schematron, '_b'+i);}
      // Puis les catégories par BG
      var catWrap=document.createElement('div');
      catWrap.innerHTML=catHtml;
      while(catWrap.firstChild){detailZone.appendChild(catWrap.firstChild);}
      // Attacher les événements après injection
      setTimeout(function(di,dz){return function(){batchAttachDetailEvents(dz,di);};}(i,detailZone),0);
    } else {
      detailZone.innerHTML='<div style="color:#94a3b8;font-size:0.85em;font-style:italic">Aucune donnée disponible.</div>';
    }

    invRow.appendChild(mainDiv);
    invRow.appendChild(detailZone);
    tbody.appendChild(invRow);
  });

  document.getElementById('batchResults').style.display='block';

  // Export CSV
  document.getElementById('btnBatchCsvAll').onclick=function(){batchExportCsv(batch);};
}

function batchToggleDetail(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  var btn=document.getElementById('batchDetailBtn_'+i);
  if(!zone||!btn)return;
  var open=zone.classList.contains('open');
  if(open){zone.classList.remove('open');btn.classList.remove('open');}
  else{zone.classList.add('open');btn.classList.add('open');}
}

function batchExpandAll(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  if(!zone)return;
  zone.querySelectorAll('.category-content').forEach(function(c){c.classList.add('open');});
  zone.querySelectorAll('.article-content').forEach(function(c){c.style.display='block';});
}
function batchCollapseAll(i){
  var zone=document.getElementById('batchDetailZone_'+i);
  if(!zone)return;
  zone.querySelectorAll('.category-content').forEach(function(c){c.classList.remove('open');});
  zone.querySelectorAll('.article-content').forEach(function(c){c.style.display='none';});
}

function batchBuildCategoriesHTML(categoriesResults,typeControle,idPfx){
  var categoryOrder={'BG-INFOS-GENERALES':1,'BG-TOTAUX':2,'BG-TVA':3,'BG-LIGNES':4,'BG-VENDEUR':5,'BG-ACHETEUR':6};
  var sorted=Object.keys(categoriesResults).sort(function(a,b){
    return (categoryOrder[a]||999)-(categoryOrder[b]||999);
  });
  var out='';
  sorted.forEach(function(bgId){
    var cat=categoriesResults[bgId];
    if(!cat.champs||cat.champs.length===0)return;
    var errCount=cat.stats.erreur||0;
    var headerBg=errCount>0?'background:#7b1e1e':(cat.stats.ok===cat.stats.total&&cat.stats.total>0?'background:#2e7d32':'background:#366092');
    var catId=idPfx+'cat-'+bgId;
    out+='<div class="category">'+
      '<div class="category-header" data-cat="'+catId+'" style="'+headerBg+'">'+
      '<div>'+escHtml(cat.titre)+'</div>'+
      '<div>'+cat.stats.total+' champs | OK: '+cat.stats.ok+' | Err: '+errCount+'</div></div>'+
      '<div class="category-content" id="'+catId+'">';
    var nonArt=cat.champs.filter(function(r){return r.article_index===undefined;});
    var artChamps=cat.champs.filter(function(r){return r.article_index!==undefined;});
    if(nonArt.length>0){
      out+='<table class="main-table"><thead><tr>'+
        '<th class="col-status"></th><th class="col-bt">BT</th>'+
        '<th class="col-libelle">Libellé</th><th class="col-regles">Règles testées</th>'+
        '<th class="col-valeurs">Valeurs</th><th class="col-erreurs">Détails erreurs</th>'+
        '</tr></thead><tbody>';
      nonArt.forEach(function(r){
        var isXmlOnly=(typeControle==='cii'||typeControle==='xmlonly');
        var valHtml='';
        var tooltipContent='';
        if(!isXmlOnly){var rv=r.rdi||'(vide)';tooltipContent='<strong>RDI:</strong> '+escHtml(r.rdi_field)+' = '+escHtml(rv);valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+escHtml(rv)+'</div>';}
        if(typeControle==='xml'||isXmlOnly){var xv=r.xml||'(vide)';if(tooltipContent)tooltipContent+='<br>';tooltipContent+='<strong>XML:</strong> '+escHtml(r.xml_tag_name)+' = '+escHtml(xv);valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+escHtml(xv)+'</div>';}
        if(r.regles_testees&&r.regles_testees.length>0){tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles :</strong><ul style="margin:2px 0 0;padding-left:16px">';r.regles_testees.forEach(function(rg){tooltipContent+='<li>'+escHtml(rg)+'</li>';});tooltipContent+='</ul>';}
        if(r.details_erreurs&&r.details_erreurs.length>0&&!(r.details_erreurs.length===1&&r.details_erreurs[0]==='RAS')){tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0;padding-left:16px;color:#fcc">';r.details_erreurs.forEach(function(e){tooltipContent+='<li>'+escHtml(e)+'</li>';});tooltipContent+='</ul>';}
        if(r.rule_details){Object.keys(r.rule_details).forEach(function(rn){var rd=r.rule_details[rn];if(!rd||!rd.length)return;tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+escHtml(rn)+' :</strong><ul style="margin:2px 0 0;padding-left:16px;font-family:monospace;font-size:0.85em">';rd.forEach(function(l){tooltipContent+='<li>'+escHtml(l)+'</li>';});tooltipContent+='</ul>';});}
        var sIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
        var btLbl=r.obligatoire==='Oui'?'<span class="bt-oblig">'+escHtml(r.balise)+'</span>':escHtml(r.balise);
        var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
        var errClass=(r.details_erreurs&&r.details_erreurs.length>0)?'col-erreurs':'col-erreurs-hidden';
        out+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
          '<td class="col-status">'+sIcon+'</td>'+
          '<td class="col-bt"><strong>'+btLbl+'</strong></td>'+
          '<td>'+escHtml(r.libelle)+'</td>'+
          '<td><ul>'; r.regles_testees.forEach(function(rg){out+='<li>'+escHtml(rg)+'</li>';}); out+='</ul></td>'+
          '<td class="col-valeurs">'+valHtml+'</td>'+
          '<td class="'+errClass+'"><ul>'; r.details_erreurs.forEach(function(e){out+='<li>'+escHtml(e)+'</li>';}); out+='</ul></td></tr>';
      });
      out+='</tbody></table>';
    }
    if(artChamps.length>0){
      var artGroups={};var artOrder=[];
      artChamps.forEach(function(r){var k=r.article_index;if(!artGroups[k]){artGroups[k]=[];artOrder.push(k);}artGroups[k].push(r);});
      out+='<div style="margin-top:8px;padding:4px 10px;font-size:12px;color:#aaa;border-top:1px solid #333">'+artOrder.length+' article(s) — cliquez pour déplier</div>';
      artOrder.forEach(function(aIdx){
        var ac=artGroups[aIdx];
        var aLid=ac[0].article_line_id||'?';
        var aName=ac[0].article_name||'';
        var aErr=ac.filter(function(r){return r.status==='ERREUR';}).length;
        var aOk=ac.filter(function(r){return r.status==='OK';}).length;
        var aHdrBg=aErr>0?'background:#5a1a1a':'background:#1a3a1a';
        var artContentId=idPfx+'art-'+aIdx;
        out+='<div class="article-block" style="margin:4px 0;border:1px solid #444;border-radius:6px;overflow:hidden">'+
          '<div class="article-header" data-art="'+artContentId+'" style="'+aHdrBg+';padding:8px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:13px">'+
          '<div><strong>📦 Ligne '+escHtml(aLid)+'</strong>'+(aName?' — '+escHtml(aName):'')+'</div>'+
          '<div>'+ac.length+' champs | ✅ '+aOk+' | ❌ '+aErr+'</div></div>'+
          '<div class="article-content" id="'+artContentId+'" style="display:none">'+
          '<table class="main-table"><thead><tr><th class="col-status"></th><th class="col-bt">BT</th><th class="col-libelle">Libellé</th><th class="col-regles">Règles</th><th class="col-valeurs">Valeurs</th><th class="col-erreurs">Erreurs</th></tr></thead><tbody>';
        ac.forEach(function(r){
          var isXmlOnly=(typeControle==='cii'||typeControle==='xmlonly');
          var valHtml='';var tooltipContent='';
          if(!isXmlOnly){var rv=r.rdi||'(vide)';tooltipContent='<strong>RDI:</strong> '+escHtml(r.rdi_field)+' = '+escHtml(rv);valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+escHtml(rv)+'</div>';}
          if(typeControle==='xml'||isXmlOnly){var xv=r.xml||'(vide)';if(tooltipContent)tooltipContent+='<br>';tooltipContent+='<strong>XML:</strong> '+escHtml(r.xml_tag_name)+' = '+escHtml(xv);valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+escHtml(xv)+'</div>';}
          if(r.regles_testees&&r.regles_testees.length>0){tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles :</strong><ul style="margin:2px 0 0;padding-left:16px">';r.regles_testees.forEach(function(rg){tooltipContent+='<li>'+escHtml(rg)+'</li>';});tooltipContent+='</ul>';}
          if(r.details_erreurs&&r.details_erreurs.length>0&&!(r.details_erreurs.length===1&&r.details_erreurs[0]==='RAS')){tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0;padding-left:16px;color:#fcc">';r.details_erreurs.forEach(function(e){tooltipContent+='<li>'+escHtml(e)+'</li>';});tooltipContent+='</ul>';}
          if(r.rule_details){Object.keys(r.rule_details).forEach(function(rn){var rd=r.rule_details[rn];if(!rd||!rd.length)return;tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+escHtml(rn)+' :</strong><ul style="margin:2px 0 0;padding-left:16px;font-family:monospace;font-size:0.85em">';rd.forEach(function(l){tooltipContent+='<li>'+escHtml(l)+'</li>';});tooltipContent+='</ul>';});}
          var sIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
          var btLbl=r.obligatoire==='Oui'?'<span class="bt-oblig">'+escHtml(r.balise)+'</span>':escHtml(r.balise);
          var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
          var errClass=(r.details_erreurs&&r.details_erreurs.length>0)?'col-erreurs':'col-erreurs-hidden';
          out+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
            '<td class="col-status">'+sIcon+'</td><td class="col-bt"><strong>'+btLbl+'</strong></td>'+
            '<td>'+escHtml(r.libelle)+'</td><td><ul>';
          r.regles_testees.forEach(function(rg){out+='<li>'+escHtml(rg)+'</li>';});
          out+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
          r.details_erreurs.forEach(function(e){out+='<li>'+escHtml(e)+'</li>';});
          out+='</ul></td></tr>';
        });
        out+='</tbody></table></div></div>';
      });
    }
    out+='</div></div>';
  });
  return out;
}

function batchAttachDetailEvents(containerEl,i){
  containerEl.querySelectorAll('.category-header').forEach(function(hdr){
    hdr.addEventListener('click',function(){
      document.getElementById(this.getAttribute('data-cat')).classList.toggle('open');
    });
  });
  containerEl.querySelectorAll('.article-header').forEach(function(hdr){
    hdr.addEventListener('click',function(){
      var el=document.getElementById(this.getAttribute('data-art'));
      if(el)el.style.display=el.style.display==='none'?'block':'none';
    });
  });
  containerEl.querySelectorAll('.data-row').forEach(function(row){
    row.addEventListener('mouseenter',function(e){tooltip.innerHTML=this.getAttribute('data-tooltip');tooltip.style.display='block';positionTooltip(e);});
    row.addEventListener('mousemove',function(e){positionTooltip(e);});
    row.addEventListener('mouseleave',function(){tooltip.style.display='none';});
  });
}

function batchExportCsv(batch){
  var lines=['Facture,BT,Libellé,Statut,RDI,XML,Erreurs'];
  batch.forEach(function(inv){
    if(!inv.results)return;
    inv.results.forEach(function(r){
      var cols=[inv.name,r.balise,r.libelle,r.status,r.rdi||'',r.xml||'',(r.details_erreurs||[]).join(' | ')];
      lines.push(cols.map(function(c){return '"'+String(c).replace(/"/g,'""')+'"';}).join(','));
    });
  });
  var blob=new Blob(['\uFEFF'+lines.join('\n')],{type:'text/csv;charset=utf-8'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='facturix-batch-'+new Date().toISOString().slice(0,10)+'.csv';
  a.click();
}

function batchReset(){
  batchFilesMap={};
  batchTmpCounter=0;
  document.getElementById('batchResults').style.display='none';
  document.getElementById('batchFilesSection').style.display='none';
  document.getElementById('batchFilesBody').innerHTML='';
  batchUpdateLaunchBtn();
}

function escHtml(s){
  if(s==null)return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ---- APERÇU FACTURE (au-dessus du taux de conformité) ---- */
function _summFindBT(results, balise){
  if(!results) return '';
  for(var i=0;i<results.length;i++){
    if(results[i].balise===balise){
      var v=(results[i].rdi||'').toString().trim();
      if(!v) v=(results[i].xml||'').toString().trim();
      return v;
    }
  }
  return '';
}
/* Parse robuste : gère le format FR ("3.724.169,45"), le format XML ("3724169.45"),
   les signes "-" en suffixe ("1.000-"), et les zéros de remplissage. */
function _parseAmtFR(v){
  if(v==null) return NaN;
  var s=String(v).trim();
  if(!s) return NaN;
  // Gérer un signe "-" en suffixe (ex : "1.000,00-")
  var neg=false;
  if(s.charAt(s.length-1)==='-'){neg=true;s=s.slice(0,-1).trim();}
  if(s.charAt(0)==='-'){neg=!neg;s=s.slice(1).trim();}
  s=s.replace(/\s/g,'');
  var hasDot=s.indexOf('.')>=0;
  var hasComma=s.indexOf(',')>=0;
  if(hasDot&&hasComma){
    // FR : les "." sont des milliers, "," est la décimale
    s=s.replace(/\./g,'').replace(',','.');
  }else if(hasComma){
    // Décimale FR seule
    s=s.replace(',','.');
  }else if(hasDot){
    // Plusieurs "." → milliers (ex : "1.234.567"). Un seul "." → décimal XML.
    var dots=s.split('.').length-1;
    if(dots>1) s=s.replace(/\./g,'');
  }
  var n=parseFloat(s);
  if(isNaN(n)) return NaN;
  return neg?-n:n;
}
function _summFmtAmount(v, currency){
  if(!v) return '';
  var n=_parseAmtFR(v);
  if(isNaN(n)) return String(v);
  var fmt=n.toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2});
  return fmt+(currency?(' '+currency):' €');
}
function _summFmtDate(v){
  if(!v) return '';
  var s=String(v).trim();
  // YYYYMMDD ou YYYY-MM-DD
  var m=s.match(/^(\d{4})-?(\d{2})-?(\d{2})/);
  if(m) return m[3]+'/'+m[2]+'/'+m[1];
  // DD/MM/YYYY déjà formaté
  if(/^\d{2}\/\d{2}\/\d{4}/.test(s)) return s.slice(0,10);
  return s;
}
function _summDestination(code){
  if(!code) return null;
  var c=String(code).trim().toUpperCase();
  if(c==='B2B') return {label:'🇫🇷 Client français',cls:'b2b'};
  if(c==='B2G') return {label:'🏛️ Chorus (B2G)',cls:'b2g'};
  if(c==='B2BINT') return {label:'🌍 Étranger',cls:'b2bint'};
  return {label:c,cls:''};
}
function _summDocType(code){
  var map={
    '380':{label:'Facture',cls:''},
    '381':{label:'Avoir',cls:'avoir'},
    '384':{label:'Facture rectificative',cls:''},
    '386':{label:"Facture d'acompte",cls:'acompte'},
    '389':{label:'Autofacture',cls:'autofact'},
    '326':{label:'Facture partielle',cls:''},
    '393':{label:'Régularisation',cls:''},
    '751':{label:'Facture pour information',cls:''},
    '875':{label:'Facture pro forma',cls:''}
  };
  if(!code) return null;
  var c=String(code).trim();
  return map[c]||{label:'Type '+c,cls:''};
}
function buildInvoiceSummary(results){
  var box=document.getElementById('invoiceSummary');
  if(!box) return;
  window._lastInvoiceResults=results||[];
  if(!results||!results.length){box.style.display='none';box.innerHTML='';return;}
  var get=function(b){return _summFindBT(results,b);};
  var bt1=get('BT-1');
  var bt3=get('BT-3');
  var bt5=get('BT-5')||'EUR';
  var bt2=get('BT-2'),bt9=get('BT-9'),bt72=get('BT-72'),bt73=get('BT-73'),bt74=get('BT-74');
  var bt109=get('BT-109'),bt110=get('BT-110'),bt112=get('BT-112');
  var bt113=get('BT-113'),bt114=get('BT-114'),bt115=get('BT-115');
  // Client
  var bt44=get('BT-44'),bt48=get('BT-48'),bt46=get('BT-46'),bt49=get('BT-49');
  var bt50=get('BT-50'),bt51=get('BT-51'),bt163=get('BT-163')||get('BT-162');
  var bt52=get('BT-52'),bt53=get('BT-53'),bt54=get('BT-54'),bt55=get('BT-55');

  var hasAny=bt1||bt3||bt2||bt9||bt109||bt112||bt115||bt44;
  if(!hasAny){box.style.display='none';box.innerHTML='';return;}

  var typeInfo=_summDocType(bt3);
  var typeBadge=typeInfo?('<span class="invoice-summary-type '+typeInfo.cls+'">'+escHtml(typeInfo.label)+'</span>'):'';
  var destInfo=_summDestination(get('BT-22-BAR'));
  var destBadge=destInfo?('<span class="invoice-summary-dest '+destInfo.cls+'">'+destInfo.label+'</span>'):'';
  var numHtml=bt1?('<span class="invoice-summary-num">N° <strong>'+escHtml(bt1)+'</strong></span>'):'';

  // Bloc Montants
  var amountRows=[];
  if(bt109) amountRows.push('<div class="invoice-summary-row"><span class="label">Total HT</span><span class="value">'+escHtml(_summFmtAmount(bt109,bt5))+'</span></div>');
  if(bt110) amountRows.push('<div class="invoice-summary-row"><span class="label">TVA</span><span class="value">'+escHtml(_summFmtAmount(bt110,bt5))+'</span></div>');
  if(bt112) amountRows.push('<div class="invoice-summary-row"><span class="label">Total TTC</span><span class="value">'+escHtml(_summFmtAmount(bt112,bt5))+'</span></div>');
  if(bt113) amountRows.push('<div class="invoice-summary-row"><span class="label">Acompte payé</span><span class="value">'+escHtml(_summFmtAmount(bt113,bt5))+'</span></div>');
  if(bt114){var n=_parseAmtFR(bt114);if(!isNaN(n)&&Math.abs(n)>0.0001) amountRows.push('<div class="invoice-summary-row"><span class="label">Arrondi</span><span class="value">'+escHtml(_summFmtAmount(bt114,bt5))+'</span></div>');}
  if(bt115) amountRows.push('<div class="invoice-summary-row amount-total"><span class="label">Net à payer</span><span class="value">'+escHtml(_summFmtAmount(bt115,bt5))+'</span></div>');
  var amountsHtml=amountRows.length?('<div class="invoice-summary-block"><div class="invoice-summary-block-title"><span class="icn">💶</span>Montants</div>'+amountRows.join('')+'</div>'):'';

  // Bloc Client
  var addrParts=[];
  var line1=[bt50,bt51].filter(Boolean).join(' ').trim();
  if(line1) addrParts.push(line1);
  if(bt163) addrParts.push(bt163);
  var line3=[bt53,bt52].filter(Boolean).join(' ').trim();
  if(line3) addrParts.push(line3);
  var line4=[bt54,bt55].filter(Boolean).join(' — ').trim();
  if(line4) addrParts.push(line4);
  var clientLines=[];
  if(bt44) clientLines.push('<div class="client-name">'+escHtml(bt44)+'</div>');
  if(addrParts.length) clientLines.push('<div class="client-addr">'+escHtml(addrParts.join('\n'))+'</div>');
  if(bt49) clientLines.push('<div class="client-id">Code service : <code>'+escHtml(bt49)+'</code></div>');
  if(bt46) clientLines.push('<div class="client-id">Identifiant : <code>'+escHtml(bt46)+'</code></div>');
  if(bt48) clientLines.push('<div class="client-id">N° TVA : <code>'+escHtml(bt48)+'</code></div>');
  var clientHtml=clientLines.length?('<div class="invoice-summary-block"><div class="invoice-summary-block-title"><span class="icn">👤</span>Client</div>'+clientLines.join('')+'</div>'):'';

  // Bloc Dates
  var dateRows=[];
  if(bt2) dateRows.push('<div class="invoice-summary-row"><span class="label">Date facture</span><span class="value">'+escHtml(_summFmtDate(bt2))+'</span></div>');
  if(bt9) dateRows.push('<div class="invoice-summary-row"><span class="label">Échéance</span><span class="value">'+escHtml(_summFmtDate(bt9))+'</span></div>');
  if(bt72) dateRows.push('<div class="invoice-summary-row"><span class="label">Livraison</span><span class="value">'+escHtml(_summFmtDate(bt72))+'</span></div>');
  if(bt73||bt74){
    var p=(_summFmtDate(bt73)||'…')+' → '+(_summFmtDate(bt74)||'…');
    dateRows.push('<div class="invoice-summary-row"><span class="label">Période</span><span class="value">'+escHtml(p)+'</span></div>');
  }
  var datesHtml=dateRows.length?('<div class="invoice-summary-block"><div class="invoice-summary-block-title"><span class="icn">📅</span>Dates</div>'+dateRows.join('')+'</div>'):'';

  var blocks=[amountsHtml,clientHtml,datesHtml].filter(Boolean).join('');
  if(!blocks&&!typeBadge&&!numHtml){box.style.display='none';box.innerHTML='';return;}

  box.innerHTML=
    '<div class="invoice-summary-header">'+
      '<div class="invoice-summary-title">Aperçu de la facture</div>'+
      typeBadge+
      destBadge+
      numHtml+
    '</div>'+
    (blocks?'<div class="invoice-summary-grid">'+blocks+'</div>':'')+
    '<div class="invoice-summary-footer"><button type="button" class="invoice-detail-btn" onclick="showInvoiceDetails()">📄 Afficher plus de détails</button></div>';
  box.style.display='block';
}

/* Construit le panneau de synthèse "Schematron EN16931" (header + body + boutons "Copier")
   et l'attache à containerEl. uidSuffix sert à isoler les ids quand plusieurs panneaux
   coexistent dans la même page (mode batch). Renvoie l'élément panel. */
function appendSchematronPanel(containerEl, sch, uidSuffix){
  if(!sch)return null;
  var sfx=uidSuffix||'';
  var panel=document.createElement('div');
  panel.className='schematron-panel';
  var headerCls,headerTxt;
  var synthSuffix=sch.synthetic?' — XML reconstruit depuis le RDI':'';
  if(sch.skipped){headerCls='warn';headerTxt='ℹ️ Schematron EN16931 (CII) — non exécuté';}
  else if(sch.error){headerCls='warn';headerTxt='⚠️ Schematron EN16931 — erreur de validation';}
  else if(sch.fatal>0){headerCls='err';headerTxt='❌ Schematron EN16931 (CII) — '+sch.fatal+' erreur'+(sch.fatal>1?'s':'')+synthSuffix;}
  else if(sch.total>0){headerCls='warn';headerTxt='⚠️ Schematron EN16931 (CII) — '+sch.total+' avertissement'+(sch.total>1?'s':'')+synthSuffix;}
  else{headerCls='ok';headerTxt='✅ Schematron EN16931 (CII) — conforme'+synthSuffix;}
  var badges='';
  if(!sch.error&&!sch.skipped){
    badges='<span class="badge">'+(sch.total||0)+' total</span>'+
           '<span class="badge">'+(sch.fatal||0)+' fatales</span>'+
           '<span class="badge">'+(sch.warning||0)+' warnings</span>'+
           '<span class="badge">'+(sch.matched||0)+' attachées</span>'+
           '<span class="badge">'+((sch.orphans||[]).length)+' orphelines</span>';
    if(sch.skipped_out_of_scope&&sch.skipped_out_of_scope>0){
      badges+='<span class="badge" title="Erreurs schematron dont aucun BT cité n\'est dans ce mapping — masquées">'+
        sch.skipped_out_of_scope+' hors mapping</span>';
    }
  }
  var hId='schematronHeader'+sfx, bId='schematronBody'+sfx;
  var hHtml='<div class="schematron-header '+headerCls+'" id="'+hId+'">'+
    '<div>'+headerTxt+'</div><div class="badges">'+badges+'</div></div>';
  var bHtml='<div class="schematron-body" id="'+bId+'"><div class="intro">Validation contre le schematron officiel <code>EN16931-CII v1.3.16</code> de ConnectingEurope. Les erreurs liées à un BT du mapping sont aussi affichées dans le tableau ci-dessous, à côté du champ concerné.</div>';
  if(sch.synthetic&&sch.note){bHtml+='<div class="intro" style="background:#fef3c7;border-left:3px solid #f59e0b;padding:6px 10px;border-radius:4px;color:#78350f;margin-bottom:8px"><strong>ℹ️ XML synthétique :</strong> '+escHtml(sch.note)+'</div>';}
  if(sch.skipped){
    bHtml+='<div class="empty" style="color:#b45309">'+escHtml(sch.reason||'Schematron non exécuté.')+'</div>';
  }else if(sch.error){
    bHtml+='<div class="empty" style="color:#b45309">Validation impossible : '+escHtml(sch.error)+'</div>';
  }else if((sch.errors||[]).length===0){
    bHtml+='<div class="empty">Aucun écart détecté ✨</div>';
  }else{
    bHtml+='<table><thead><tr><th>Règle</th><th>Sévérité</th><th>BT concernés</th><th>Message</th><th>XPath</th></tr></thead><tbody>';
    (sch.errors||[]).forEach(function(e){
      var bts=(e.bts||[]).map(function(b){return '<span>'+escHtml(b)+'</span>';}).join('');
      var loc=e.location||'';
      var locCell=loc
        ? '<button type="button" class="copy-xpath" data-xpath="'+escHtml(loc)+'" title="'+escHtml(loc)+'">📋 Copier</button>'
        : '<span style="color:#94a3b8">—</span>';
      bHtml+='<tr>'+
        '<td class="rule">'+escHtml(e.rule_id||'')+'</td>'+
        '<td class="flag '+(e.flag||'')+'">'+escHtml(e.severity||e.flag||'')+'</td>'+
        '<td class="bts">'+(bts||'<span style="color:#94a3b8">—</span>')+'</td>'+
        '<td>'+escHtml(e.message||'')+'</td>'+
        '<td class="location">'+locCell+'</td>'+
      '</tr>';
    });
    bHtml+='</tbody></table>';
    if((sch.orphans||[]).length>0){
      bHtml+='<div class="intro" style="margin-top:12px;color:#b45309"><strong>'+sch.orphans.length+' erreur(s) orpheline(s)</strong> : règles dont le BT cible n\'est pas mappé dans ce formulaire — elles ne sont visibles que dans ce panneau.</div>';
    }
  }
  bHtml+='</div>';
  panel.innerHTML=hHtml+bHtml;
  containerEl.appendChild(panel);
  panel.querySelector('#'+hId).addEventListener('click',function(){
    panel.querySelector('#'+bId).classList.toggle('open');
  });
  panel.querySelectorAll('button.copy-xpath').forEach(function(btn){
    btn.addEventListener('click',function(ev){
      ev.stopPropagation();
      var xp=this.getAttribute('data-xpath')||'';
      var done=this;
      var ok=function(){
        done.classList.add('copied');
        var prev=done.textContent;
        done.textContent='✓ Copié';
        setTimeout(function(){done.classList.remove('copied');done.textContent=prev;},1500);
      };
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(xp).then(ok).catch(function(){
          var ta=document.createElement('textarea');ta.value=xp;document.body.appendChild(ta);
          ta.select();try{document.execCommand('copy');ok();}catch(e){}finally{ta.remove();}
        });
      }else{
        var ta=document.createElement('textarea');ta.value=xp;document.body.appendChild(ta);
        ta.select();try{document.execCommand('copy');ok();}catch(e){}finally{ta.remove();}
      }
    });
  });
  if(!sch.error&&(sch.total||0)>0){panel.querySelector('#'+bId).classList.add('open');}
  return panel;
}

/* Construit le bloc tooltip "Schematron officiel EN16931" pour une ligne du tableau.
   Source : r.schematron_errors = [{rule_id, severity, flag, message, location, bts}, ...] */
function buildSchematronTooltip(r){
  if(!r||!r.schematron_errors||r.schematron_errors.length===0)return '';
  var html='<hr style="margin:6px 0;border-color:#7c3aed">'+
    '<strong style="color:#c4b5fd">📜 Schematron officiel EN16931 (CII)</strong>'+
    '<div style="font-size:0.85em;color:#cbd5e1;margin:2px 0 4px">'+
      r.schematron_errors.length+' règle(s) du standard non respectée(s)</div>';
  r.schematron_errors.forEach(function(e){
    var sevColor=(e.flag==='fatal')?'#fca5a5':'#fde68a';
    var sevLabel=(e.flag==='fatal')?'fatale':(e.severity||e.flag||'warning');
    html+='<div style="margin:6px 0;padding:6px 8px;background:rgba(124,58,237,0.18);border-left:3px solid #a78bfa;border-radius:4px">'+
      '<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:3px">'+
        '<strong style="color:#ddd6fe;font-family:monospace">'+escHtml(e.rule_id||'')+'</strong>'+
        '<span style="color:'+sevColor+';font-size:0.78em;font-weight:700;text-transform:uppercase">'+escHtml(sevLabel)+'</span>'+
      '</div>'+
      '<div style="color:#fde2e2;font-size:0.86em;line-height:1.35">'+escHtml(e.message||'')+'</div>';
    if(e.bts&&e.bts.length>0){
      html+='<div style="margin-top:4px;font-size:0.78em;color:#a5b4fc"><strong>BT cités :</strong> '+
        e.bts.map(function(b){return escHtml(b);}).join(', ')+'</div>';
    }
    if(e.location){
      html+='<div style="margin-top:3px;font-size:0.72em;color:#94a3b8;font-family:monospace;word-break:break-all">'+
        '<strong style="color:#cbd5e1">XPath :</strong> '+escHtml(e.location)+'</div>';
    }
    html+='</div>';
  });
  return html;
}

/* ---- MAPPING MANAGEMENT FUNCTIONS ---- */
function updateDeleteButtonVisibility() {
    const paramSelect = document.getElementById('typeFormulaireParam');
    const btn = document.getElementById('btnDeleteCurrentMapping');
    if (!paramSelect || !btn) return;
    const opt = paramSelect.options[paramSelect.selectedIndex];
    btn.style.display = (opt && opt.dataset.isDefault === 'false') ? '' : 'none';
}

function deleteCurrentMapping() {
    const paramSelect = document.getElementById('typeFormulaireParam');
    const opt = paramSelect && paramSelect.options[paramSelect.selectedIndex];
    if (!opt || !opt.dataset.mappingId) return;
    openDeleteMappingModal(opt.dataset.mappingId);
}

function openCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'block';
    document.getElementById('newMappingName').value = '';

    // Peupler la liste de tous les mappings existants
    const copySelect = document.getElementById('copyFromMapping');
    copySelect.innerHTML = '<option value="">Mapping vide</option>';

    if (mappingsIndex.mappings) {
        mappingsIndex.mappings.forEach(mapping => {
            const option = document.createElement('option');
            option.value = mapping.id;
            option.textContent = mapping.name;
            copySelect.appendChild(option);
        });
    }
}

function closeCreateMappingModal() {
    document.getElementById('createMappingModal').style.display = 'none';
}

function confirmCreateMapping() {
    const name = document.getElementById('newMappingName').value.trim();
    const copyFrom = document.getElementById('copyFromMapping').value;

    if (!name) {
        alert('Veuillez entrer un nom pour le mapping');
        return;
    }

    const payload = { name };
    if (copyFrom) {
        payload.copy_from = copyFrom;
    }
    
    fetch(BASE+'/api/mappings/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            const copyMsg = copyFrom ? ' (copié depuis un mapping existant)' : '';
            alert(`✓ Mapping "${name}" créé avec succès !${copyMsg}`);
            closeCreateMappingModal();
            updateAllMappingDropdowns();
        } else {
            alert('Erreur: ' + (data.error || 'Création impossible'));
        }
    })
    .catch(err => {
        console.error('Erreur:', err);
        alert('Erreur lors de la création du mapping');
    });
}

function openDeleteMappingModal(mappingId) {
    const mapping = mappingsIndex.mappings.find(m => m.id === mappingId);
    if (!mapping) return;
    
    mappingToDelete = mapping;
    document.getElementById('deleteMappingName').textContent = mapping.name;
    document.getElementById('deleteMappingModal').style.display = 'block';
}

function closeDeleteMappingModal() {
    document.getElementById('deleteMappingModal').style.display = 'none';
    mappingToDelete = null;
}

function confirmDeleteMapping() {
    if (!mappingToDelete) return;
    
    fetch(BASE+'/api/mappings/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: mappingToDelete.id })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            alert(`✓ Mapping "${mappingToDelete.name}" supprimé avec succès`);
            closeDeleteMappingModal();
            updateAllMappingDropdowns();
        } else {
            alert('Erreur: ' + (data.error || 'Suppression impossible'));
        }
    })
    .catch(err => {
        console.error('Erreur:', err);
        alert('Erreur lors de la suppression du mapping');
    });
}

// Fonction pour mettre à jour tous les dropdowns de mapping
function updateAllMappingDropdowns() {
    fetch(BASE+'/api/mappings/index')
        .then(r => r.json())
        .then(data => {
            mappingsIndex = data;
            const allMappings = data.mappings || [];

            const controleSelect = document.getElementById('typeFormulaire');
            if (controleSelect) updateSingleDropdown(controleSelect, allMappings);

            const batchSelect = document.getElementById('batchTypeFormulaire');
            if (batchSelect) updateSingleDropdown(batchSelect, allMappings);

            const paramSelect = document.getElementById('typeFormulaireParam');
            if (paramSelect) {
                updateSingleDropdown(paramSelect, allMappings);
                updateDeleteButtonVisibility();
            }
        })
        .catch(err => console.error('Erreur mise à jour dropdowns:', err));
}

function updateSingleDropdown(selectElement, mappings) {
    const currentValue = selectElement.value;
    selectElement.innerHTML = '';
    
    // Ajouter toutes les options sans grouper
    mappings.forEach(mapping => {
        const option = document.createElement('option');

        // Dériver la value depuis l'id DB (source de vérité)
        let value;
        if (mapping.id === 'default_simple') value = 'simple';
        else if (mapping.id === 'default_groupee') value = 'groupee';
        else if (mapping.id === 'default_flux') value = 'flux';
        else value = 'custom_' + mapping.id;

        option.value = value;
        option.textContent = mapping.name;
        option.dataset.filename = mapping.filename;
        option.dataset.mappingId = mapping.id;
        option.dataset.isDefault = mapping.is_default ? 'true' : 'false';
        option.dataset.color = mapping.color || '';
        
        selectElement.appendChild(option);
    });
    
    // Restaurer la sélection
    if (currentValue) {
        const exists = Array.from(selectElement.options).some(o => o.value === currentValue);
        if (exists) {
            selectElement.value = currentValue;
        }
    }
}

// Charger les options au démarrage
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(() => updateAllMappingDropdowns(), 500);
});

// Close modals when clicking outside
// On traque le mousedown pour éviter les faux positifs (ex: glissement depuis l'intérieur)
var _modalMousedownTarget = null;
document.addEventListener('mousedown', function(e) { _modalMousedownTarget = e.target; });

window.onclick = function(event) {
    const createModal = document.getElementById('createMappingModal');
    const deleteModal = document.getElementById('deleteMappingModal');
    const editModal = document.getElementById('editModal');
    const ruleModal = document.getElementById('editRuleModal');
    const historyModal = document.getElementById('historyModal');
    const authorModal = document.getElementById('authorModal');
    // On ne ferme que si mousedown ET click sont tous deux sur le fond
    var t = event.target;
    var md = _modalMousedownTarget;
    if (t === createModal && md === createModal) { closeCreateMappingModal(); }
    if (t === deleteModal && md === deleteModal) { closeDeleteMappingModal(); }
    if (t === editModal   && md === editModal)   { editModal.style.display = 'none'; }
    if (t === ruleModal   && md === ruleModal)   { ruleModal.style.display = 'none'; }
    if (t === historyModal && md === historyModal) { historyModal.style.display = 'none'; }
    if (t === authorModal  && md === authorModal)  { authorModal.style.display = 'none'; pendingAuditCallback = null; }
}

// Echap → ferme le modal ouvert ; Entrée dans un input (hors textarea) → sauvegarde
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var historyModal = document.getElementById('historyModal');
        if (historyModal && historyModal.style.display !== 'none') { historyModal.style.display = 'none'; return; }
        var editModal = document.getElementById('editModal');
        if (editModal && editModal.style.display !== 'none') { editModal.style.display = 'none'; return; }
        return;
    }
    var editModal = document.getElementById('editModal');
    if (!editModal || editModal.style.display === 'none') return;
    if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'SELECT') {
        e.preventDefault();
        document.getElementById('btnSave').click();
    }
});

// Add event listener to create button
document.addEventListener('DOMContentLoaded', function() {
    const btnCreate = document.getElementById('btnCreateMapping');
    if (btnCreate) {
        btnCreate.addEventListener('click', openCreateMappingModal);
    }
});

/* ---- AIDE CONTEXTUELLE + MASQUAGE PDF ---- */
function updateHelp(){
var type=document.getElementById('typeControle').value;
var help=document.getElementById('helpControle');
var groupePdf=document.getElementById('groupePdf');
var groupeCii=document.getElementById('groupeCii');
var groupeRdi=document.getElementById('groupeRdi');
if(type==='rdi'){
help.innerHTML='<strong>Mode RDI</strong><ul><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}else if(type==='cii'){
help.innerHTML='<strong>Mode CII - GCP</strong><ul><li>Controle du XML CII (Cross Industry Invoice) directement</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li></ul>';
groupePdf.style.display='none';
groupeCii.style.display='flex';
groupeRdi.style.display='none';
}else if(type==='xmlonly'){
help.innerHTML='<strong>Mode XML - Vérif facture uniquement</strong><ul><li>Controle du XML encapsule dans le PDF</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Regles metiers</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='none';
}else{
help.innerHTML='<strong>Mode RDI vs XML</strong><ul><li>Comparaison sortie SAP vs sortie Exstream</li><li>Présence obligatoire</li><li>Regles de gestion</li><li>Contrôles CEGEDIM</li><li>Comparaison RDI vs XML</li></ul>';
groupePdf.style.display='flex';
groupeCii.style.display='none';
groupeRdi.style.display='flex';
}
}
document.getElementById('typeControle').addEventListener('change',updateHelp);
updateHelp();

/* ---- AFFICHER/MASQUER BOUTONS PDF ---- */
document.getElementById('pdfFile').addEventListener('change',function(){
var file=this.files[0];
var isPdf=(file && file.name.toLowerCase().endsWith('.pdf'));
document.getElementById('btnDownloadXml').style.display=isPdf?'inline-block':'none';
document.getElementById('btnRemoveSignature').style.display=isPdf?'inline-block':'none';
});
document.getElementById('btnDownloadXml').addEventListener('click',async function(){
var pdf=document.getElementById('pdfFile').files[0];
if(!pdf){alert('Selectionnez un fichier PDF');return}
var fd=new FormData();
fd.append('pdf',pdf);
try{
var resp=await fetch(BASE+'/api/extract-xml',{method:'POST',body:fd});
if(!resp.ok){var err=await resp.json();alert('Erreur: '+(err.error||'Extraction impossible'));return}
var blob=await resp.blob();
var url=URL.createObjectURL(blob);
var a=document.createElement('a');
a.href=url;
a.download=pdf.name.replace(/\.pdf$/i,'.xml');
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
}catch(e){alert('Erreur: '+e.message)}
});

/* ---- SUPPRIMER SIGNATURE PDF ---- */
document.getElementById('btnRemoveSignature').addEventListener('click',async function(){
var pdf=document.getElementById('pdfFile').files[0];
if(!pdf){alert('Selectionnez un fichier PDF');return}
var fd=new FormData();
fd.append('pdf',pdf);
try{
this.disabled=true;this.textContent='⏳ En cours...';
var resp=await fetch(BASE+'/api/remove-signature',{method:'POST',body:fd});
if(!resp.ok){var err=await resp.json();alert('Erreur: '+(err.error||'Impossible de traiter ce PDF'));return}
var blob=await resp.blob();
var url=URL.createObjectURL(blob);
var a=document.createElement('a');
a.href=url;
a.download=pdf.name.replace(/\.pdf$/i,'_unsigned.pdf');
document.body.appendChild(a);
a.click();
a.remove();
URL.revokeObjectURL(url);
}catch(e){alert('Erreur: '+e.message)
}finally{this.disabled=false;this.innerHTML='<span>✂️</span> Supprimer signature';}
});

/* ---- LANCER CONTROLE ---- */
document.getElementById('btnControle').addEventListener('click',async function(){
var typeControle=document.getElementById('typeControle').value;
var pdf=document.getElementById('pdfFile').files[0];
var rdi=document.getElementById('rdiFile').files[0];
var cii=document.getElementById('ciiFile').files[0];
if(typeControle==='xml'&&!pdf){alert('Selectionnez le fichier PDF ou XML');return}
if(typeControle==='xmlonly'&&!pdf){alert('Selectionnez le fichier PDF');return}
if(typeControle==='cii'&&!cii){alert('Selectionnez le fichier XML CII');return}
if(typeControle!=='cii'&&typeControle!=='xmlonly'&&!rdi){alert('Selectionnez le fichier RDI');return}
document.getElementById('loading').style.display='block';
document.getElementById('results').style.display='none';
var _sumBox=document.getElementById('invoiceSummary');
if(_sumBox){_sumBox.innerHTML='';_sumBox.style.display='none';}
var fd=new FormData();
if(pdf)fd.append('pdf',pdf);
if(cii)fd.append('cii',cii);
if(rdi)fd.append('rdi',rdi);
fd.append('type_formulaire',document.getElementById('typeFormulaire').value);
fd.append('type_controle',typeControle);
try{
var resp=await fetch(BASE+'/controle',{method:'POST',body:fd});
var data=await resp.json();
if(data.error){alert('Erreur: '+data.error);return}
try{buildInvoiceSummary(data.results||[]);}catch(e){console.error('[summary]',e);var _b=document.getElementById('invoiceSummary');if(_b){_b.style.display='none';_b.innerHTML='';}}
document.getElementById('statTotal').textContent=data.stats.total;
document.getElementById('statOk').textContent=data.stats.ok;
document.getElementById('statErreur').textContent=data.stats.erreur;
document.getElementById('statIgnore').textContent=data.stats.ignore||0;
document.getElementById('statAmbigu').textContent=data.stats.ambigu||0;
var artInfo=document.getElementById('statArticles');
if(artInfo){artInfo.textContent=data.stats.nb_articles>0?data.stats.nb_articles:'—';}
var pct=data.stats.total>0?Math.round(data.stats.ok/data.stats.total*100):0;
var fill=document.getElementById('progressFill');
document.getElementById('progressPct').textContent=pct+'%';
fill.style.width=pct+'%';
fill.className='progress-fill';
var gSrc,gMsg;
if(pct<25){gSrc=BASE+'/img/0-25.jpg';fill.classList.add('pct-0');}
else if(pct<50){gSrc=BASE+'/img/25-50.jpg';fill.classList.add('pct-25');}
else if(pct<75){gSrc=BASE+'/img/50-75.jpg';fill.classList.add('pct-50');}
else{gSrc=BASE+'/img/75-100.jpg';fill.classList.add('pct-75');}
document.getElementById('gauloisImg').src=gSrc;
// Survol de la barre : afficher overlay
var track=document.querySelector('.progress-track');
var overlay=document.getElementById('gauloisOverlay');
track.onmousemove=function(e){
  overlay.classList.add('visible');
  var x=e.clientX,y=e.clientY;
  var ow=430,oh=430;
  var left=x+20; if(left+ow>window.innerWidth-10) left=x-ow-20;
  var top=y-oh/2; if(top<10) top=10; if(top+oh>window.innerHeight-10) top=window.innerHeight-oh-10;
  overlay.style.left=left+'px';
  overlay.style.top=top+'px';
};
track.onmouseleave=function(){overlay.classList.remove('visible');};
var cont=document.getElementById('categoriesContainer');
cont.innerHTML='';
// Bandeau de synthèse Schematron officiel EN16931 (CII)
if(data.schematron){appendSchematronPanel(cont, data.schematron, '');}
// Trier les catégories dans l'ordre défini
var categoryOrder={'BG-INFOS-GENERALES':1,'BG-TOTAUX':2,'BG-TVA':3,'BG-LIGNES':4,'BG-VENDEUR':5,'BG-ACHETEUR':6};
var sortedCategories=Object.keys(data.categories_results).sort(function(a,b){
var orderA=categoryOrder[a]||999;
var orderB=categoryOrder[b]||999;
return orderA-orderB;
});
for(var i=0;i<sortedCategories.length;i++){
var bgId=sortedCategories[i];
var cat=data.categories_results[bgId];
if(cat.champs.length===0)continue;
var div=document.createElement('div');
div.className='category';
var errCount=cat.stats.erreur||0;
var headerBg=errCount>0?'background:#7b1e1e':(cat.stats.ok===cat.stats.total&&cat.stats.total>0?'background:#2e7d32':'background:#366092');
var html='<div class="category-header" data-cat="'+bgId+'" style="'+headerBg+'">'+
'<div>'+cat.titre+'</div>'+
'<div>'+cat.stats.total+' champs | OK: '+cat.stats.ok+' | Err: '+errCount+'</div></div>'+
'<div class="category-content" id="cat-'+bgId+'">';
// Séparer champs non-article et champs article
var hasArticles=cat.champs.some(function(r){return r.article_index!==undefined;});
var nonArticleChamps=cat.champs.filter(function(r){return r.article_index===undefined;});
var articleChamps=cat.champs.filter(function(r){return r.article_index!==undefined;});

// 1. Rendu des champs non-article dans un tableau classique
if(nonArticleChamps.length>0){
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th class="col-regles">Regles testees</th>'+
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
nonArticleChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
if(r.regles_testees&&r.regles_testees.length>0){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles appliquées :</strong><ul style="margin:2px 0 0 0;padding-left:16px">';
r.regles_testees.forEach(function(reg){tooltipContent+='<li>'+reg+'</li>';});
tooltipContent+='</ul>';
}
// Filtrer les details_erreurs pour ne pas dupliquer les schematron (ils ont leur propre section)
var nonSchDetails=(r.details_erreurs||[]).filter(function(e){return !/^\[BR-/.test(e);});
if(nonSchDetails.length>0&&!(nonSchDetails.length===1&&nonSchDetails[0]==='RAS')){
tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0 0;padding-left:16px;color:#fcc">';
nonSchDetails.forEach(function(err){tooltipContent+='<li>'+err+'</li>';});
tooltipContent+='</ul>';
}
tooltipContent+=buildSchematronTooltip(r);
if(r.rule_details){
Object.keys(r.rule_details).forEach(function(ruleName){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+ruleName+' :</strong><ul style="margin:2px 0 0 0;padding-left:16px;font-family:monospace;font-size:0.9em">';
r.rule_details[ruleName].forEach(function(line){tooltipContent+='<li>'+line+'</li>';});
tooltipContent+='</ul>';
});
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
if(r.controles_cegedim&&r.controles_cegedim.length>0){
html+='<tr><td colspan="6" style="padding:0 12px 12px 40px;background:#faf8ff">'+
'<table class="ceg-table">'+
'<thead><tr><th>Ref</th><th>Categorie</th><th>Nature</th><th>Controle</th><th>Message</th></tr></thead><tbody>';
r.controles_cegedim.forEach(function(c){
html+='<tr><td>'+(c.ref||'')+'</td><td>'+(c.categorie||'')+'</td><td>'+(c.nature||'')+'</td><td>'+(c.description||c.controle||'')+'</td><td>'+(c.message||'')+'</td></tr>';
});
html+='</tbody></table></td></tr>';
}
});
html+='</tbody></table>';
}

// 2. Rendu des articles en blocs dépliables
if(articleChamps.length>0){
var articleGroups={};
var articleOrder=[];
articleChamps.forEach(function(r){
var key=r.article_index;
if(!articleGroups[key]){articleGroups[key]=[];articleOrder.push(key);}
articleGroups[key].push(r);
});
html+='<div style="margin-top:8px;padding:4px 10px;font-size:12px;color:#aaa;border-top:1px solid #333">'+articleOrder.length+' article(s) détecté(s) — cliquez pour déplier</div>';
articleOrder.forEach(function(artIdx){
var artChamps=articleGroups[artIdx];
var artLineId=artChamps[0].article_line_id||'?';
var artName=artChamps[0].article_name||'';
var artErrCount=artChamps.filter(function(r){return r.status==='ERREUR'}).length;
var artOkCount=artChamps.filter(function(r){return r.status==='OK'}).length;
var artHeaderBg=artErrCount>0?'background:#5a1a1a':'background:#1a3a1a';
html+='<div class="article-block" style="margin:4px 0;border:1px solid #444;border-radius:6px;overflow:hidden">'+
'<div class="article-header" data-art="art-'+artIdx+'" style="'+artHeaderBg+';padding:8px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#fff;font-size:13px">'+
'<div><strong>📦 Ligne '+artLineId+'</strong>'+(artName?' — '+artName:'')+'</div>'+
'<div>'+artChamps.length+' champs | ✅ '+artOkCount+' | ❌ '+artErrCount+'</div></div>'+
'<div class="article-content" id="art-'+artIdx+'" style="display:none">';
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th class="col-regles">Regles testees</th>'+
'<th class="col-valeurs">Valeurs</th>'+
'<th class="col-erreurs">Details erreurs</th>'+
'</tr></thead><tbody>';
artChamps.forEach(function(r){
var isXmlOnly=(data.type_controle==='cii'||data.type_controle==='xmlonly');
var tooltipContent='';
var valHtml='';
if(!isXmlOnly){
var rdiVal=r.rdi||'(vide)';
tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+rdiVal;
valHtml+='<div class="val-line"><span class="val-label">RDI:</span> '+rdiVal+'</div>';
}
if(data.type_controle==='xml'||isXmlOnly){
var xmlVal=r.xml||'(vide)';
if(tooltipContent)tooltipContent+='<br>';
tooltipContent+='<strong>XML:</strong> '+r.xml_tag_name+' = '+xmlVal;
valHtml+='<div class="val-line"><span class="val-label">XML:</span> '+xmlVal+'</div>';
}
if(r.regles_testees&&r.regles_testees.length>0){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Règles appliquées :</strong><ul style="margin:2px 0 0 0;padding-left:16px">';
r.regles_testees.forEach(function(reg){tooltipContent+='<li>'+reg+'</li>';});
tooltipContent+='</ul>';
}
var nonSchDetailsArt=(r.details_erreurs||[]).filter(function(e){return !/^\[BR-/.test(e);});
if(nonSchDetailsArt.length>0&&!(nonSchDetailsArt.length===1&&nonSchDetailsArt[0]==='RAS')){
tooltipContent+='<hr style="margin:4px 0;border-color:#c44"><strong style="color:#f88">Erreurs :</strong><ul style="margin:2px 0 0 0;padding-left:16px;color:#fcc">';
nonSchDetailsArt.forEach(function(err){tooltipContent+='<li>'+err+'</li>';});
tooltipContent+='</ul>';
}
tooltipContent+=buildSchematronTooltip(r);
if(r.rule_details){
Object.keys(r.rule_details).forEach(function(ruleName){
tooltipContent+='<hr style="margin:4px 0;border-color:#555"><strong>Détail calcul — '+ruleName+' :</strong><ul style="margin:2px 0 0 0;padding-left:16px;font-family:monospace;font-size:0.9em">';
r.rule_details[ruleName].forEach(function(line){tooltipContent+='<li>'+line+'</li>';});
tooltipContent+='</ul>';
});
}
var statusIcon=r.status==='IGNORE'?'⏸️':(r.status==='OK'?'✅':(r.status==='AMBIGU'?'⚠️':'❌'));
var btLabel=r.obligatoire==='Oui'?'<span class="bt-oblig">'+r.balise+'</span>':r.balise;
var rowBg=r.status==='ERREUR'?'background:#fff5f5':(r.status==='IGNORE'?'background:#f5f5f5':(r.status==='AMBIGU'?'background:#fffbeb':''));
var hasErrors=r.details_erreurs&&r.details_erreurs.length>0;
var errClass=hasErrors?'col-erreurs':'col-erreurs-hidden';
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-bt"><strong>'+btLabel+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td class="col-valeurs">'+valHtml+'</td><td class="'+errClass+'"><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
});
html+='</tbody></table></div></div>';
});
}
html+='</div>';
div.innerHTML=html;
div.querySelector('.category-header').addEventListener('click',function(){
document.getElementById('cat-'+this.getAttribute('data-cat')).classList.toggle('open');
});
// Event listeners pour les headers d'articles
div.querySelectorAll('.article-header').forEach(function(hdr){
hdr.addEventListener('click',function(){
var contentId=this.getAttribute('data-art');
var content=document.getElementById(contentId);
if(content){content.style.display=content.style.display==='none'?'block':'none';}
});
});
div.querySelectorAll('.data-row').forEach(function(row){
row.addEventListener('mouseenter',function(e){
tooltip.innerHTML=this.getAttribute('data-tooltip');
tooltip.style.display='block';
positionTooltip(e);
});
row.addEventListener('mousemove',function(e){
positionTooltip(e);
});
row.addEventListener('mouseleave',function(){tooltip.style.display='none'});
});
cont.appendChild(div);
}
document.getElementById('results').style.display='block';

// Filtrage par BT et par erreurs
var searchInput=document.getElementById('searchBT');
var clearBtn=document.getElementById('btnClearSearch');
var searchContentInput=document.getElementById('searchContent');
var clearContentBtn=document.getElementById('btnClearContent');
var filterErrorsCheckbox=document.getElementById('filterErrors');
var filterAmbigusCheckbox=document.getElementById('filterAmbigus');

function applyAllFilters(){
var searchTerm=searchInput.value.toLowerCase().trim();
var contentTerm=searchContentInput.value.toLowerCase().trim();
var showErrorsOnly=filterErrorsCheckbox.checked;
var showAmbigusOnly=filterAmbigusCheckbox.checked;
clearBtn.style.display=searchTerm?'inline-block':'none';
clearContentBtn.style.display=contentTerm?'inline-block':'none';
filterResults(searchTerm,contentTerm,showErrorsOnly,showAmbigusOnly);
}

searchInput.removeEventListener('input',applyAllFilters);
searchInput.addEventListener('input',applyAllFilters);
searchContentInput.removeEventListener('input',applyAllFilters);
searchContentInput.addEventListener('input',applyAllFilters);
filterErrorsCheckbox.removeEventListener('change',applyAllFilters);
filterErrorsCheckbox.addEventListener('change',applyAllFilters);
filterAmbigusCheckbox.removeEventListener('change',applyAllFilters);
filterAmbigusCheckbox.addEventListener('change',applyAllFilters);
clearBtn.onclick=function(){
searchInput.value='';
clearBtn.style.display='none';
applyAllFilters();
};
clearContentBtn.onclick=function(){
searchContentInput.value='';
clearContentBtn.style.display='none';
applyAllFilters();
};

// Tout déplier / Tout replier
document.getElementById('btnExpandAll').addEventListener('click',function(){
document.querySelectorAll('.category-content').forEach(function(c){c.classList.add('open');});
document.querySelectorAll('.article-content').forEach(function(c){c.style.display='block';});
});
document.getElementById('btnCollapseAll').addEventListener('click',function(){
document.querySelectorAll('.category-content').forEach(function(c){c.classList.remove('open');});
document.querySelectorAll('.article-content').forEach(function(c){c.style.display='none';});
});

// Afficher/masquer les contrôles CEGEDIM
var cegedimCheckbox=document.getElementById('showCegedim');
function toggleCegedim(){
var show=cegedimCheckbox.checked;
document.querySelectorAll('.ceg-table').forEach(function(t){
t.closest('tr').style.display=show?'':'none';
});
}
cegedimCheckbox.addEventListener('change',toggleCegedim);
toggleCegedim();
applyAllFilters();

function filterResults(term,contentTerm,errorsOnly,ambigusOnly){
var categories=document.querySelectorAll('.category');
var visibleCount=0;
var hasActiveFilter=!!(term||contentTerm||errorsOnly||ambigusOnly);
categories.forEach(function(cat){
var hasMatch=false;
// Filtrer les lignes standard (hors articles)
var rows=cat.querySelectorAll('.main-table > tbody > .data-row, table.main-table > tbody > .data-row');
rows.forEach(function(row){
var btStrong=row.querySelector('td:nth-child(2) strong');
if(!btStrong) return;
var btText=btStrong.textContent.toLowerCase();
var valCell=row.querySelector('.col-valeurs');
var valText=valCell?valCell.textContent.toLowerCase():'';
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var isAmbigu=(statusIcon==='⚠️');
var nextRow=row.nextElementSibling;
var isCegedimRow=nextRow && nextRow.querySelector('.ceg-table');
var matchesSearch=!term||btText.includes(term);
var matchesContent=!contentTerm||valText.includes(contentTerm);
var matchesErrorFilter=!errorsOnly||isError;
var matchesAmbigusFilter=!ambigusOnly||isAmbigu;
if(matchesSearch&&matchesContent&&matchesErrorFilter&&matchesAmbigusFilter){
row.style.display='';
if(isCegedimRow){nextRow.style.display=cegedimCheckbox.checked?'':'none';}
hasMatch=true;
}else{
row.style.display='none';
if(isCegedimRow){nextRow.style.display='none';}
}
});
// Filtrer les blocs articles
var artBlocks=cat.querySelectorAll('.article-block');
artBlocks.forEach(function(block){
var artHasMatch=false;
var artRows=block.querySelectorAll('.data-row');
artRows.forEach(function(row){
var btStrong=row.querySelector('td:nth-child(2) strong');
if(!btStrong) return;
var btText=btStrong.textContent.toLowerCase();
var valCell=row.querySelector('.col-valeurs');
var valText=valCell?valCell.textContent.toLowerCase():'';
var statusIcon=row.querySelector('.col-status').textContent.trim();
var isError=(statusIcon==='❌');
var isAmbigu=(statusIcon==='⚠️');
var matchesSearch=!term||btText.includes(term);
var matchesContent=!contentTerm||valText.includes(contentTerm);
var matchesErrorFilter=!errorsOnly||isError;
var matchesAmbigusFilter=!ambigusOnly||isAmbigu;
if(matchesSearch&&matchesContent&&matchesErrorFilter&&matchesAmbigusFilter){
row.style.display='';
artHasMatch=true;
}else{
row.style.display='none';
}
});
if(artHasMatch){
block.style.display='';
hasMatch=true;
}else{
block.style.display=hasActiveFilter?'none':'';
}
});
if(hasMatch){
cat.classList.remove('hidden');
var catContent=cat.querySelector('.category-content');
if(hasActiveFilter&&catContent){catContent.classList.add('open');}
visibleCount++;
}else{
cat.classList.add('hidden');
}
});
}

}catch(e){
console.error(e);
alert('Erreur: '+e.message);
}finally{
document.getElementById('loading').style.display='none';
}
});

/* ---- PARAMETRAGE ---- */
function getCurrentMappingColor(){
var sel=document.getElementById('typeFormulaireParam');
var opt=sel&&sel.options[sel.selectedIndex];
return (opt&&opt.dataset.color)||'';
}
function applyMappingColor(){
var color=getCurrentMappingColor();
var swatch=document.getElementById('mappingColorSwatch');
var picker=document.getElementById('mappingColorPicker');
if(color){swatch.style.background=color;picker.value=color;}
else{swatch.style.background='#667eea';picker.value='#667eea';}
}
async function loadMappings(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch(BASE+'/api/mapping/'+type);
currentMapping=await resp.json();
applyMappingColor();
var list=document.getElementById('mappingList');
list.innerHTML='';
if(!currentMapping||!currentMapping.champs||!currentMapping.champs.length){
list.innerHTML='<p style="color:#94a3b8;font-size:0.85em;padding:12px">Aucun champ dans ce mapping.</p>';
return;
}

// 1. Grouper par categorie_bg
var groups={};
var groupOrder=[];
currentMapping.champs.forEach(function(champ,index){
var bg=champ.categorie_bg||'BG-OTHER';
var rawTitre=champ.categorie_titre||bg;
var titre=rawTitre.replace(/[^\w\s\-'éèêëàâùûîïôçÉÈÊËÀÂÙÛÎÏÔÇ]/g,'').trim()||bg;
if(!groups[bg]){groups[bg]={titre:titre,champs:[],hasArticle:false};groupOrder.push(bg);}
groups[bg].champs.push({champ:champ,index:index});
if(champ.is_article) groups[bg].hasArticle=true;
});

// 2. Barre de filtres pills
var filterBar=document.createElement('div');
filterBar.className='cat-filter-bar';
var allPill=document.createElement('span');
allPill.className='cat-pill active';
allPill.dataset.bg='ALL';
allPill.textContent='Tout ('+currentMapping.champs.length+')';
filterBar.appendChild(allPill);
groupOrder.forEach(function(bg){
var g=groups[bg];
var pill=document.createElement('span');
pill.className='cat-pill'+(g.hasArticle?' art':'');
pill.dataset.bg=bg;
pill.textContent=g.titre+' ('+g.champs.length+')';
filterBar.appendChild(pill);
});
var collapseBtn=document.createElement('button');
collapseBtn.textContent='Tout replier';
collapseBtn.style.cssText='margin-left:auto;padding:3px 11px;border:1px solid #e2e8f0;border-radius:20px;font-size:0.73em;cursor:pointer;background:#f8fafc;color:#475569;font-weight:600;white-space:nowrap';
filterBar.appendChild(collapseBtn);
list.appendChild(filterBar);

var allCollapsed=false;
collapseBtn.addEventListener('click',function(){
allCollapsed=!allCollapsed;
collapseBtn.textContent=allCollapsed?'Tout déplier':'Tout replier';
document.querySelectorAll('.cat-group-body').forEach(function(b){b.style.display=allCollapsed?'none':'';});
document.querySelectorAll('.cat-group-hdr').forEach(function(h){
if(allCollapsed)h.classList.remove('open');else h.classList.add('open');
});
});

// 3. Rendu des groupes
groupOrder.forEach(function(bg){
var g=groups[bg];
var isArt=g.hasArticle;
var nbValide=g.champs.filter(function(e){return e.champ.valide===true;}).length;

var groupDiv=document.createElement('div');
groupDiv.className='cat-group';
groupDiv.dataset.bg=bg;

var hdr=document.createElement('div');
hdr.className='cat-group-hdr open'+(isArt?' art':'');
var ratioHtml=nbValide>0?'<span class="cat-group-ok-ratio">✓ '+nbValide+'/'+g.champs.length+'</span>':'';
hdr.innerHTML=
'<span class="cat-group-arrow">▶</span>'+
'<span class="cat-group-name">'+(isArt?'▤ ':'')+g.titre+'</span>'+
ratioHtml+
'<span class="cat-group-count">'+g.champs.length+' BT</span>';

var body=document.createElement('div');
body.className='cat-group-body';
var ul=document.createElement('ul');
ul.className='mapping-list';

g.champs.forEach(function(entry){
var champ=entry.champ;
var index=entry.index;
var li=document.createElement('li');
var isValide=champ.valide===true;
var isIgnored=champ.ignore==='Oui';
var isArticle=!!champ.is_article;
li.className='mapping-item'+(isValide?' valide':'')+(isArticle?' article':'')+(isIgnored?' ignored':'');
li.draggable=true;
li.setAttribute('data-index',index);
if(isIgnored)li.classList.add('has-ignored-tip');
li.innerHTML=
'<div class="mapping-item-info">'+
'<div class="item-main"><strong>'+champ.balise+'</strong> — '+champ.libelle+'</div>'+
'<div class="item-sub">RDI: <code>'+champ.rdi+'</code> | Oblig.: '+champ.obligatoire+' | Ignoré : '+(isIgnored?'Oui':'Non')+'</div>'+
'<div class="item-xpath">XPath: '+(champ.xpath||'—')+'</div>'+
'</div>'+
'<div class="mapping-actions">'+
'<label class="valide-toggle"><input type="checkbox" class="chk-valide" data-index="'+index+'"'+(isValide?' checked':'')+'> Valide</label>'+
'<button class="btn-edit" data-index="'+index+'">Editer</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>';
// Drag & drop
li.addEventListener('dragstart',function(e){this.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/html',this.innerHTML);});
li.addEventListener('dragend',function(){this.classList.remove('dragging');document.querySelectorAll('.mapping-item').forEach(function(it){it.classList.remove('drag-over');});});
li.addEventListener('dragover',function(e){e.preventDefault();var d=document.querySelector('.dragging');if(d&&d!==this)this.classList.add('drag-over');});
li.addEventListener('dragleave',function(){this.classList.remove('drag-over');});
li.addEventListener('drop',async function(e){
e.preventDefault();this.classList.remove('drag-over');
var d=document.querySelector('.dragging');
if(d&&d!==this){
var fi=parseInt(d.getAttribute('data-index'));
var ti=parseInt(this.getAttribute('data-index'));
var it=currentMapping.champs.splice(fi,1)[0];
currentMapping.champs.splice(ti,0,it);
await saveMapping();loadMappings();
}
});
ul.appendChild(li);
});

body.appendChild(ul);
groupDiv.appendChild(hdr);
groupDiv.appendChild(body);
list.appendChild(groupDiv);

hdr.addEventListener('click',function(){
var open=hdr.classList.contains('open');
hdr.classList.toggle('open');
body.style.display=open?'none':'';
});
});

// 4. Délégation d'événements chk-valide / btn-edit / btn-delete
list.addEventListener('change',async function(e){
var chk=e.target.closest('.chk-valide');
if(chk){
var idx=parseInt(chk.getAttribute('data-index'));
currentMapping.champs[idx].valide=chk.checked;
await saveMapping();loadMappings();
}
});
list.addEventListener('click',function(e){
var eb=e.target.closest('.btn-edit');
if(eb){editMapping(eb.getAttribute('data-index'));return;}
var db=e.target.closest('.btn-delete');
if(db){deleteMapping(db.getAttribute('data-index'));}
});

// 5. Pills filter
filterBar.querySelectorAll('.cat-pill').forEach(function(pill){
pill.addEventListener('click',function(){
filterBar.querySelectorAll('.cat-pill').forEach(function(p){p.classList.remove('active');});
pill.classList.add('active');
var bg=pill.dataset.bg;
document.querySelectorAll('.cat-group').forEach(function(g){
g.style.display=(bg==='ALL'||g.dataset.bg===bg)?'':'none';
});
});
});

applySearchParamFilter();
}

function applySearchParamFilter(){
var query=document.getElementById('searchBTParam').value.toLowerCase().trim();
var groups=document.querySelectorAll('.cat-group');
var items=document.querySelectorAll('.mapping-item');
if(query){
// Expand all groups
groups.forEach(function(g){
g.style.display='';
var hdr=g.querySelector('.cat-group-hdr');
var body=g.querySelector('.cat-group-body');
if(hdr)hdr.classList.add('open');
if(body)body.style.display='';
});
// Filtrer les items
items.forEach(function(item){
var mainEl=item.querySelector('.item-main');
var text=mainEl?mainEl.textContent.toLowerCase():'';
item.style.display=text.includes(query)?'flex':'none';
});
// Cacher les groupes vides
groups.forEach(function(g){
var hasVisible=false;
g.querySelectorAll('.mapping-item').forEach(function(it){if(it.style.display!=='none')hasVisible=true;});
g.style.display=hasVisible?'':'none';
});
}else{
items.forEach(function(it){it.style.display='flex';});
var activePill=document.querySelector('.cat-pill.active');
if(activePill&&activePill.dataset.bg!=='ALL'){
groups.forEach(function(g){g.style.display=g.dataset.bg===activePill.dataset.bg?'':'none';});
}else{
groups.forEach(function(g){g.style.display='';});
}
}
}

function editMapping(index){
currentIndex=parseInt(index);
var champ=currentMapping.champs[currentIndex];
var selOpt=document.getElementById('typeFormulaireParam');
var mappingName=selOpt&&selOpt.options[selOpt.selectedIndex]?selOpt.options[selOpt.selectedIndex].textContent:'';
document.getElementById('modalTitle').textContent=mappingName||'Mapping';
document.getElementById('modalSubtitle').textContent='Mise à jour du champ BT';
// Appliquer la couleur du mapping sur le header
var color=getCurrentMappingColor();
var header=document.querySelector('.edit-field-header');
if(header){
if(color){header.style.background=color;}
else{header.style.background='linear-gradient(135deg,#667eea 0%,#764ba2 100%)';}
}
document.getElementById('editBalise').value=champ.balise;
document.getElementById('editLibelle').value=champ.libelle;
// Construire la valeur du select à partir de categorie_bg et categorie_titre
// Mapper les anciennes catégories vers les nouvelles si nécessaire
var categorieValue=(champ.categorie_bg||'BG-INFOS-GENERALES')+'|'+(champ.categorie_titre||'INFORMATIONS GÉNÉRALES DE LA FACTURE');
// Si la catégorie n'existe pas dans le select, utiliser la première option
var select=document.getElementById('editCategorie');
var exists=false;
for(var i=0;i<select.options.length;i++){
if(select.options[i].value===categorieValue){
exists=true;
break;
}
}
if(!exists){
// Par défaut, mapper vers la première catégorie
categorieValue='BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE';
}
document.getElementById('editCategorie').value=categorieValue;
document.getElementById('editRdi').value=champ.rdi;
document.getElementById('editTypeEnreg').value=champ.type_enregistrement||'';
document.getElementById('editXpath').value=(champ.xpath||'').replace(/^\/\//,'');
document.getElementById('editAttribute').value=champ.attribute||'';
document.getElementById('editObligatoire').value=champ.obligatoire;
document.getElementById('editIgnore').value=champ.ignore||'Non';
document.getElementById('editRdg').value=champ.rdg||'';
document.getElementById('btnCloneField').style.display='inline-flex';
document.getElementById('editModal').style.display='block';
}
async function deleteMapping(index){
if(!confirm('Supprimer ce champ?'))return;
var idx=parseInt(index);
var deletedChamp=Object.assign({},currentMapping.champs[idx]);
currentMapping.champs.splice(idx,1);
await saveMapping();
var type=document.getElementById('typeFormulaireParam').value;
askAuthorThen(async function(author){
await logAudit(type,author,'delete',deletedChamp.balise,deletedChamp,null);
});
loadMappings();
}
// IDs des mappings cibles pour un ajout multi-mapping
var addTargetMappingIds = [];
// Mode clone (depuis le bouton "Cloner vers…" dans editModal)
var cloneMode = false;

document.getElementById('btnAdd').addEventListener('click',async function(){
// Charger la liste de tous les mappings disponibles
var resp = await fetch(BASE+'/api/mappings/index');
var data = await resp.json();
var allMappings = data.mappings || [];
var sel = document.getElementById('typeFormulaireParam');
var currentMappingId = (sel.options[sel.selectedIndex] && sel.options[sel.selectedIndex].dataset.mappingId) || sel.value;

// Remplir les checkboxes
var listEl = document.getElementById('selectMappingsList');
listEl.innerHTML = '';
allMappings.forEach(function(m){
var isCurrent = (m.id === currentMappingId);
var label = document.createElement('label');
label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8f9fa;border-radius:6px;cursor:pointer;font-size:0.95em';
label.innerHTML = '<input type="checkbox" class="chk-target-mapping" value="'+m.id+'"'+(isCurrent?' checked':'')+' style="width:16px;height:16px"> '+
'<span><strong>'+m.name+'</strong>'+(isCurrent?' <em style="color:#888;font-size:0.85em">(actuel)</em>':'')+'</span>';
listEl.appendChild(label);
});

cloneMode = false;
document.querySelector('#selectMappingsModal h2').textContent='Ajouter le champ à quel(s) mapping(s) ?';
document.querySelector('#selectMappingsModal p').textContent='Sélectionnez les mappings dans lesquels ce nouveau champ sera ajouté. Le mapping actuel est présélectionné.';
document.getElementById('selectMappingsModal').style.display='block';
});

document.getElementById('selectMappingsClose').addEventListener('click',function(){
document.getElementById('selectMappingsModal').style.display='none';
cloneMode=false;
});
document.getElementById('selectMappingsCancel').addEventListener('click',function(){
document.getElementById('selectMappingsModal').style.display='none';
cloneMode=false;
});
document.getElementById('selectMappingsConfirm').addEventListener('click',async function(){
addTargetMappingIds = Array.from(document.querySelectorAll('.chk-target-mapping:checked')).map(function(c){return c.value;});
if(addTargetMappingIds.length===0){alert('Sélectionnez au moins un mapping.');return;}
document.getElementById('selectMappingsModal').style.display='none';
if(cloneMode){
cloneMode=false;
// Lire les valeurs actuelles du formulaire
var categorieValue=document.getElementById('editCategorie').value;
var categorieParts=categorieValue.split('|');
var categorieBg=categorieParts[0]||'BG-OTHER';
var categorieTitre=categorieParts[1]||'Autres';
var clonedChamp={
balise:document.getElementById('editBalise').value,
libelle:document.getElementById('editLibelle').value,
rdi:document.getElementById('editRdi').value,
type_enregistrement:document.getElementById('editTypeEnreg').value||undefined,
xpath:document.getElementById('editXpath').value,
attribute:document.getElementById('editAttribute').value||undefined,
is_article:(function(){return (categorieBg==='BG-LIGNES'||/ligne/i.test(categorieBg+' '+categorieTitre))?true:undefined;})(),
obligatoire:document.getElementById('editObligatoire').value,
ignore:document.getElementById('editIgnore').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:categorieBg,
categorie_titre:categorieTitre,
controles_cegedim:[],
valide:false,
type:(currentIndex!==null&&currentMapping.champs[currentIndex]?currentMapping.champs[currentIndex].type:undefined)||undefined
};
var cloneTargets=addTargetMappingIds.slice();
addTargetMappingIds=[];
askAuthorThen(async function(author){
for(var i=0;i<cloneTargets.length;i++){
var tid=cloneTargets[i];
var r=await fetch(BASE+'/api/mapping/'+tid);
var targetMapping=await r.json();
targetMapping.champs.push(clonedChamp);
await fetch(BASE+'/api/mapping/'+tid,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(targetMapping)});
await logAudit(tid,author,'add',clonedChamp.balise,null,clonedChamp);
}
alert('✓ Champ "'+clonedChamp.balise+'" cloné vers '+cloneTargets.length+' mapping(s).');
});
return;
}
// Ouvrir le formulaire d'ajout
currentIndex=null;
var selOptAdd=document.getElementById('typeFormulaireParam');
var mappingNameAdd=selOptAdd&&selOptAdd.options[selOptAdd.selectedIndex]?selOptAdd.options[selOptAdd.selectedIndex].textContent:'';
document.getElementById('modalTitle').textContent=mappingNameAdd||'Mapping';
document.getElementById('modalSubtitle').textContent='Ajout d\'un nouveau champ BT'+(addTargetMappingIds.length>1?' ('+addTargetMappingIds.length+' mappings)':'');
document.getElementById('editBalise').value='';
document.getElementById('editLibelle').value='';
document.getElementById('editCategorie').value='BG-INFOS-GENERALES|INFORMATIONS GÉNÉRALES DE LA FACTURE';
document.getElementById('editRdi').value='';
document.getElementById('editTypeEnreg').value='';
document.getElementById('editXpath').value='';
document.getElementById('editAttribute').value='';
document.getElementById('editObligatoire').value='Non';
document.getElementById('editIgnore').value='Non';
document.getElementById('editRdg').value='';
document.getElementById('btnCloneField').style.display='none';
// Appliquer la couleur du mapping sur le header pour l'ajout aussi
var colorAdd=getCurrentMappingColor();
var headerAdd=document.querySelector('.edit-field-header');
if(headerAdd){
if(colorAdd){headerAdd.style.background=colorAdd;}
else{headerAdd.style.background='linear-gradient(135deg,#667eea 0%,#764ba2 100%)';}
}
document.getElementById('editModal').style.display='block';
});
document.getElementById('modalClose').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
document.getElementById('editCancelBtn').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
document.getElementById('btnCloneField').addEventListener('click',async function(){
// Ouvrir le sélecteur de mappings en mode clone (exclure le mapping courant)
var resp = await fetch(BASE+'/api/mappings/index');
var data = await resp.json();
var allMappings = data.mappings || [];
var sel2 = document.getElementById('typeFormulaireParam');
var currentMappingId2 = (sel2.options[sel2.selectedIndex] && sel2.options[sel2.selectedIndex].dataset.mappingId) || sel2.value;
var listEl = document.getElementById('selectMappingsList');
listEl.innerHTML = '';
allMappings.forEach(function(m){
if(m.id === currentMappingId2) return; // exclure le mapping courant
var label = document.createElement('label');
label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8f9fa;border-radius:6px;cursor:pointer;font-size:0.95em';
label.innerHTML = '<input type="checkbox" class="chk-target-mapping" value="'+m.id+'" style="width:16px;height:16px"> <span><strong>'+m.name+'</strong></span>';
listEl.appendChild(label);
});
if(!listEl.children.length){alert('Aucun autre mapping disponible.');return;}
document.querySelector('#selectMappingsModal h2').textContent='Cloner le champ vers quel(s) mapping(s) ?';
document.querySelector('#selectMappingsModal p').textContent='Sélectionnez les mappings dans lesquels ce champ sera copié (le mapping actuel est exclu).';
cloneMode = true;
document.getElementById('selectMappingsModal').style.display='block';
});
// ── Fonctions auteur ──────────────────────────────────────────────────────
function getAuthor(){return sessionStorage.getItem('facturix_author')||'';}
function setAuthor(name){sessionStorage.setItem('facturix_author',name);}
function askAuthorThen(callback){
var author=getAuthor();
if(author){callback(author);return;}
pendingAuditCallback=callback;
document.getElementById('authorInput').value='';
document.getElementById('authorModal').style.display='block';
setTimeout(function(){document.getElementById('authorInput').focus();},80);
}
document.getElementById('authorConfirmBtn').addEventListener('click',function(){
var name=document.getElementById('authorInput').value.trim();
if(!name){alert('Veuillez saisir votre nom.');return;}
setAuthor(name);
document.getElementById('authorModal').style.display='none';
if(pendingAuditCallback){var cb=pendingAuditCallback;pendingAuditCallback=null;cb(name);}
});
document.getElementById('authorCancelBtn').addEventListener('click',function(){
document.getElementById('authorModal').style.display='none';
pendingAuditCallback=null;
});
document.getElementById('authorInput').addEventListener('keydown',function(e){
if(e.key==='Enter')document.getElementById('authorConfirmBtn').click();
});

var AUDIT_DIFF_FIELDS=['libelle','rdi','xpath','obligatoire','ignore','rdg','categorie_bg','attribute','type_enregistrement'];
async function logAudit(type,author,action,btBalise,oldChamp,newChamp){
var payload={author:author,action:action,bt_balise:btBalise};
if(action==='edit'){
AUDIT_DIFF_FIELDS.forEach(function(f){
payload['old_'+f]=oldChamp?String(oldChamp[f]||''):'';
payload['new_'+f]=newChamp?String(newChamp[f]||''):'';
});
}else if(action==='add'&&newChamp){
payload.snapshot=JSON.stringify(newChamp);
}else if(action==='delete'&&oldChamp){
payload.snapshot=JSON.stringify(oldChamp);
}
await fetch(BASE+'/api/mapping/'+type+'/audit',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(payload)
});
}

document.getElementById('btnSave').addEventListener('click',async function(){
var base=currentIndex!==null?currentMapping.champs[currentIndex]:{};
// Parser la valeur du select (format: "BG-XX|Titre")
var categorieValue=document.getElementById('editCategorie').value;
var categorieParts=categorieValue.split('|');
var categorieBg=categorieParts[0]||'BG-OTHER';
var categorieTitre=categorieParts[1]||'Autres';
var newChamp={
balise:document.getElementById('editBalise').value,
libelle:document.getElementById('editLibelle').value,
rdi:document.getElementById('editRdi').value,
type_enregistrement:document.getElementById('editTypeEnreg').value||undefined,
xpath:document.getElementById('editXpath').value,
attribute:document.getElementById('editAttribute').value||undefined,
is_article:(function(){var bg=categorieBg||'';return (bg==='BG-LIGNES'||bg==='BG-25'||/ligne/i.test(bg+' '+(categorieTitre||'')))?true:undefined;})(),
obligatoire:document.getElementById('editObligatoire').value,
ignore:document.getElementById('editIgnore').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:categorieBg,
categorie_titre:categorieTitre,
controles_cegedim:base.controles_cegedim||[],
valide:base.valide||false,
type:base.type||undefined
};
var oldChamp=currentIndex!==null?Object.assign({},currentMapping.champs[currentIndex]):null;
var isEdit=currentIndex!==null;
// Si édition sans modification réelle : sauvegarder silencieusement, sans demander l'auteur ni logguer
if(isEdit&&oldChamp){
var AUDIT_FIELDS=['balise','libelle','rdi','type_enregistrement','xpath','attribute','obligatoire','ignore','rdg','categorie_bg','categorie_titre'];
var hasChanged=AUDIT_FIELDS.some(function(k){return (oldChamp[k]||'')!==(newChamp[k]||'');});
if(!hasChanged){
currentMapping.champs[currentIndex]=newChamp;
await saveMapping();
document.getElementById('editModal').style.display='none';
loadMappings();
return;
}
}
askAuthorThen(async function(author){
if(isEdit){
// Édition d'un champ existant
currentMapping.champs[currentIndex]=newChamp;
await saveMapping();
var type=document.getElementById('typeFormulaireParam').value;
await logAudit(type,author,'edit',newChamp.balise,oldChamp,newChamp);
}else{
// Ajout d'un nouveau champ : enregistrer dans tous les mappings sélectionnés
var currentType=document.getElementById('typeFormulaireParam').value;
for(var i=0;i<addTargetMappingIds.length;i++){
var tid=addTargetMappingIds[i];
var targetMapping;
if(tid===currentType){
targetMapping=currentMapping;
}else{
var r=await fetch(BASE+'/api/mapping/'+tid);
targetMapping=await r.json();
}
targetMapping.champs.push(newChamp);
await fetch(BASE+'/api/mapping/'+tid,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(targetMapping)
});
await logAudit(tid,author,'add',newChamp.balise,null,newChamp);
}
// Recharger le mapping courant en mémoire
var r2=await fetch(BASE+'/api/mapping/'+currentType);
currentMapping=await r2.json();
addTargetMappingIds=[];
}
document.getElementById('editModal').style.display='none';
loadMappings();
});
});


// Sauvegarder une version horodatée
async function saveMapping(){
var type=document.getElementById('typeFormulaireParam').value;
await fetch(BASE+'/api/mapping/'+type,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
}
document.getElementById('typeFormulaireParam').addEventListener('change',function(){
    loadMappings();
    updateDeleteButtonVisibility();
});

// ── Color picker ──────────────────────────────────────────────────────────
document.getElementById('mappingColorBtn').addEventListener('click',function(){
document.getElementById('mappingColorPicker').click();
});
document.getElementById('mappingColorPicker').addEventListener('input',function(){
document.getElementById('mappingColorSwatch').style.background=this.value;
});
document.getElementById('mappingColorPicker').addEventListener('change',async function(){
var color=this.value;
document.getElementById('mappingColorSwatch').style.background=color;
var type=document.getElementById('typeFormulaireParam').value;
// Mettre à jour le dataset de l'option sélectionnée
var sel=document.getElementById('typeFormulaireParam');
if(sel&&sel.options[sel.selectedIndex]){sel.options[sel.selectedIndex].dataset.color=color;}
await fetch(BASE+'/api/mapping/'+type+'/color',{
method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({color:color})
});
// Sync le select du contrôle aussi
var controleOpts=document.querySelectorAll('#typeFormulaire option');
controleOpts.forEach(function(opt){if(opt.value===sel.value)opt.dataset.color=color;});
});

// ── Historique (audit) ────────────────────────────────────────────────────
var AUDIT_FIELD_LABELS={libelle:'Libellé',rdi:'Champ RDI',xpath:'XPath',
obligatoire:'Obligatoire',ignore:'Ignorer',rdg:'Règle de gestion',
attribute:'Attribut',type_enregistrement:'Type enreg.',categorie_bg:'Catégorie'};

function buildDiffHtml(e){
var html='';
var action=e.action==='revert'?'edit':e.action;
if(action==='edit'){
AUDIT_DIFF_FIELDS.forEach(function(f){
var ov=e['old_'+f]||'',nv=e['new_'+f]||'';
if(ov===nv)return;
html+='<div class="audit-diff-row">'+
'<span class="audit-diff-key">'+(AUDIT_FIELD_LABELS[f]||f)+'</span>'+
'<span class="audit-diff-old">'+escapeHtml(ov)+'</span>'+
'<span class="audit-diff-arrow">→</span>'+
'<span class="audit-diff-new">'+escapeHtml(nv)+'</span>'+
'</div>';
});
}else if((action==='add'||action==='delete')&&e.snapshot){
try{
var snap=JSON.parse(e.snapshot);
var SNAP_FIELDS=['balise','libelle','rdi','xpath','obligatoire','ignore','rdg','categorie_bg','attribute','type_enregistrement'];
var SNAP_LABELS=Object.assign({balise:'Balise BT'},AUDIT_FIELD_LABELS);
SNAP_FIELDS.forEach(function(f){
var v=snap[f];
if(!v)return;
html+='<div class="audit-diff-row">'+
'<span class="audit-diff-key">'+(SNAP_LABELS[f]||f)+'</span>'+
'<span class="audit-diff-new" style="color:'+(action==='add'?'#059669':'#dc2626')+'">'+escapeHtml(String(v))+'</span>'+
'</div>';
});
}catch(err){}
}
return html;
}

document.getElementById('btnHistory').addEventListener('click',async function(){
var type=document.getElementById('typeFormulaireParam').value;
var entries=await (await fetch(BASE+'/api/mapping/'+type+'/audit')).json();
var list=document.getElementById('auditList');
list.innerHTML='';
if(!entries||entries.length===0){
list.innerHTML='<p style="color:#94a3b8;text-align:center;padding:20px">Aucune modification enregistrée pour ce mapping.</p>';
}else{
entries.forEach(function(e){
var item=document.createElement('div');
var isRevert=e.action==='revert';
item.className='audit-item'+(isRevert?' audit-item-revert':'');
var actionLabel={'edit':'MODIF','add':'AJOUT','delete':'SUPPRESSION','revert':'ROLLBACK'}[e.action]||e.action;
var actionClass={'edit':'edit','add':'add','delete':'delete','revert':'revert'}[e.action]||'edit';

var header=document.createElement('div');
header.className='audit-item-header';

var numSpan=document.createElement('span');numSpan.className='audit-num';numSpan.textContent='#'+e.id;
var tsSpan=document.createElement('span');tsSpan.className='audit-ts';tsSpan.textContent=e.timestamp||'';
var authSpan=document.createElement('span');authSpan.className='audit-author';authSpan.textContent=e.author||'';
var actSpan=document.createElement('span');actSpan.className='audit-action '+actionClass;actSpan.textContent=actionLabel;
var btSpan=document.createElement('span');btSpan.className='audit-bt';btSpan.textContent=e.bt_balise||'';
header.appendChild(numSpan);header.appendChild(tsSpan);header.appendChild(authSpan);header.appendChild(actSpan);header.appendChild(btSpan);

if(isRevert&&e.revert_of){
var rbSpan=document.createElement('span');rbSpan.className='audit-rollback-label';rbSpan.textContent='Rollback de la modification #'+e.revert_of;
header.appendChild(rbSpan);
}

if(e.action==='edit'||e.action==='delete'){
var btn=document.createElement('button');
btn.className='audit-revert-btn';
btn.textContent='↩ Revenir';
btn.dataset.id=String(e.id);
header.appendChild(btn);
}
item.appendChild(header);

var diffHtml=buildDiffHtml(e);
if(diffHtml){
var diffDiv=document.createElement('div');
diffDiv.className='audit-diff';
diffDiv.innerHTML=diffHtml;
item.appendChild(diffDiv);
}
list.appendChild(item);
});

list.querySelectorAll('.audit-revert-btn').forEach(function(btn){
btn.addEventListener('click',function(){
var id=this.dataset.id;
if(!confirm('Revenir à l\'état précédent de ce champ ?'))return;
askAuthorThen(async function(author){
var res=await (await fetch(BASE+'/api/mapping/'+type+'/audit/'+id+'/revert',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({author:author})
})).json();
if(res.success){
loadMappings();
document.getElementById('btnHistory').click();
}else{alert('Erreur : '+(res.error||'Impossible de revenir en arrière'));}
});
});
});
}
document.getElementById('historyModal').style.display='block';
});
document.getElementById('historyModalClose').addEventListener('click',function(){
document.getElementById('historyModal').style.display='none';
});
document.getElementById('historyCloseBtn').addEventListener('click',function(){
document.getElementById('historyModal').style.display='none';
});

function escapeHtml(s){if(!s)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}


/* ---- TOOLTIP IGNORÉ ---- */
(function(){
var tip=document.getElementById('ignored-tooltip');
document.addEventListener('mouseover',function(e){
var li=e.target.closest('.has-ignored-tip');
if(!li){tip.style.display='none';return;}
tip.style.display='block';
var r=li.getBoundingClientRect();
var tx=r.left;
var ty=r.bottom+6;
if(tx+tip.offsetWidth>window.innerWidth-12)tx=window.innerWidth-tip.offsetWidth-12;
if(ty+tip.offsetHeight>window.innerHeight-12)ty=r.top-tip.offsetHeight-6;
tip.style.left=tx+'px';
tip.style.top=ty+'px';
});
document.addEventListener('mouseout',function(e){
var li=e.target.closest('.has-ignored-tip');
if(li&&!li.contains(e.relatedTarget))tip.style.display='none';
});
})();

/* ---- RECHERCHE BT PARAMETRAGE ---- */
document.getElementById('searchBTParam').addEventListener('input',function(){
var btn=document.getElementById('btnClearSearchParam');
btn.style.display=this.value?'block':'none';
applySearchParamFilter();
});
document.getElementById('btnClearSearchParam').addEventListener('click',function(){
document.getElementById('searchBTParam').value='';
this.style.display='none';
applySearchParamFilter();
});

/* ---- RÈGLES MÉTIERS ---- */
var currentRules={rules:[]};
var availableBTs=[];
var availableRDIs=[];

async function loadAvailableBTs(){
// Charger tous les BT depuis tous les mappings
var types=['simple','groupee','ventesdiverses'];
var allBTs={};
var allRDIs={};
for(var i=0;i<types.length;i++){
try{
var resp=await fetch(BASE+'/api/mapping/'+types[i]);
var mapping=await resp.json();
if(mapping&&mapping.champs){
mapping.champs.forEach(function(champ){
if(champ.balise){
allBTs[champ.balise]=champ.libelle||champ.balise;
}
if(champ.rdi&&!allRDIs[champ.rdi]){
allRDIs[champ.rdi]=champ.balise||champ.rdi;
}
});
}
}catch(e){}
}
// Convertir en array et trier par numéro de BT
availableBTs=Object.keys(allBTs).sort(function(a,b){
// Extraire les numéros des BT (ex: BT-131-0 -> [131, 0])
var aMatch=a.match(/BT-(\d+)(?:-(\d+))?/);
var bMatch=b.match(/BT-(\d+)(?:-(\d+))?/);
if(!aMatch||!bMatch)return a.localeCompare(b);
var aNum1=parseInt(aMatch[1]);
var bNum1=parseInt(bMatch[1]);
if(aNum1!==bNum1)return aNum1-bNum1;
// Si même premier numéro, comparer le second
var aNum2=aMatch[2]?parseInt(aMatch[2]):0;
var bNum2=bMatch[2]?parseInt(bMatch[2]):0;
return aNum2-bNum2;
}).map(function(bt){
return {value:bt,label:bt+' ('+allBTs[bt]+')'};
});
// Convertir les RDI en array trié alphabétiquement
availableRDIs=Object.keys(allRDIs).sort().map(function(rdi){
return {value:rdi,label:rdi+' ('+allRDIs[rdi]+')'};
});
}

var ruleCategories=['Calculs','Exonérations TVA','B2G / Chorus','Notes & mentions','Paiement','Cohérence','Autre'];
var activeRuleCategory='ALL';
var availableForms=[]; // [{value, label, mappingId}]

function refreshCategorySelect(){
var sel=document.getElementById('ruleCategory');
if(!sel)return;
sel.innerHTML=ruleCategories.map(function(c){return '<option value="'+c+'">'+c+'</option>';}).join('');
}

function mappingIdToFormValue(id){
if(id==='default_simple')return 'simple';
if(id==='default_groupee')return 'groupee';
if(id==='default_flux')return 'flux';
if(id==='default_ventesdiverses')return 'ventesdiverses';
return 'custom_'+id;
}

function getFormLabel(value){
for(var i=0;i<availableForms.length;i++){
if(availableForms[i].value===value)return availableForms[i].label;
}
return value;
}

async function loadAvailableForms(){
try{
var resp=await fetch(BASE+'/api/mappings/index');
var data=await resp.json();
availableForms=(data.mappings||[]).map(function(m){
return {value:mappingIdToFormValue(m.id),label:m.name,mappingId:m.id};
});
}catch(e){availableForms=[];}
// Mettre à jour le select de filtre
var filterSel=document.getElementById('filterFormType');
if(filterSel){
var current=filterSel.value||'all';
filterSel.innerHTML='<option value="all">Toutes les factures</option>'+
availableForms.map(function(f){return '<option value="'+f.value+'">'+f.label+' uniquement</option>';}).join('');
filterSel.value=Array.from(filterSel.options).some(function(o){return o.value===current;})?current:'all';
}
}

function renderFormCheckboxes(selectedForms){
var container=document.getElementById('ruleFormsContainer');
if(!container)return;
var allChecked=!selectedForms||selectedForms.length===0;
container.innerHTML=availableForms.map(function(f){
var checked=allChecked||selectedForms.indexOf(f.value)!==-1;
return '<label style="display:flex;align-items:center;gap:8px;font-weight:normal">'+
'<input type="checkbox" class="rule-form-cb" data-value="'+f.value+'"'+(checked?' checked':'')+' style="width:18px;height:18px">'+
'<span>'+f.label+'</span>'+
'</label>';
}).join('');
}

async function loadRules(){
await loadAvailableBTs();
await loadAvailableForms();
var resp=await fetch(BASE+'/api/rules');
currentRules=await resp.json();
if(currentRules.categories&&currentRules.categories.length){ruleCategories=currentRules.categories;}
refreshCategorySelect();
displayRules();
}

function displayRules(){
var container=document.getElementById('rulesList');
var filter=document.getElementById('filterFormType').value;
container.innerHTML='';
if(!currentRules.rules || currentRules.rules.length===0){
container.innerHTML='<p>Aucune règle définie</p>';
return;
}
var filteredRules=currentRules.rules.filter(function(rule){
if(filter==='all')return true;
var forms=rule.applicable_forms||[];
return forms.length===0||forms.includes(filter);
});
if(filteredRules.length===0){
container.innerHTML='<p>Aucune règle applicable à ce type de factures</p>';
return;
}
// Regrouper par catégorie
var byCategory={};
filteredRules.forEach(function(rule){
var cat=rule.category||'Autre';
if(!byCategory[cat])byCategory[cat]=[];
byCategory[cat].push(rule);
});
// Ordre d'affichage : catégories connues d'abord, puis les inconnues triées
var orderedCats=ruleCategories.filter(function(c){return byCategory[c];});
Object.keys(byCategory).forEach(function(c){if(orderedCats.indexOf(c)===-1)orderedCats.push(c);});
// Si la catégorie active n'existe plus (changement de filtre type-facture), retomber sur ALL
if(activeRuleCategory!=='ALL'&&!byCategory[activeRuleCategory]){activeRuleCategory='ALL';}
// Barre de filtres pills (style Paramétrage)
var filterBar=document.createElement('div');
filterBar.className='cat-filter-bar';
var allPill=document.createElement('span');
allPill.className='cat-pill'+(activeRuleCategory==='ALL'?' active':'');
allPill.dataset.cat='ALL';
allPill.textContent='Tout ('+filteredRules.length+')';
filterBar.appendChild(allPill);
orderedCats.forEach(function(cat){
var pill=document.createElement('span');
pill.className='cat-pill'+(activeRuleCategory===cat?' active':'');
pill.dataset.cat=cat;
pill.textContent=cat+' ('+byCategory[cat].length+')';
filterBar.appendChild(pill);
});
container.appendChild(filterBar);
filterBar.querySelectorAll('.cat-pill').forEach(function(pill){
pill.addEventListener('click',function(){
activeRuleCategory=pill.dataset.cat;
displayRules();
});
});
// Filtrer selon la pill active
var visibleCats=(activeRuleCategory==='ALL')?orderedCats:[activeRuleCategory];
visibleCats.forEach(function(cat){
var rules=byCategory[cat];
if(!rules)return;
var header=document.createElement('div');
header.className='rule-category-header';
header.innerHTML='<h3 style="margin:18px 0 8px;padding:6px 12px;background:#eef2f7;border-left:4px solid #3b82f6;border-radius:4px;font-size:0.95em;color:#1e293b">'+cat+' <span style="color:#64748b;font-weight:normal;font-size:0.85em">('+rules.length+')</span></h3>';
container.appendChild(header);
rules.forEach(function(rule){
var index=currentRules.rules.indexOf(rule);
var div=document.createElement('div');
div.className='rule-card';
var enabledClass=rule.enabled?'enabled':'disabled';
var enabledText=rule.enabled?'✓ Activée':'✗ Désactivée';
// Afficher les formulaires applicables
var formsText='';
var forms=rule.applicable_forms||[];
if(forms.length===0){
formsText='<span style="color:#999;font-size:0.85em">Tous les types</span>';
}else{
formsText='<span style="color:#666;font-size:0.85em">'+forms.map(function(f){return getFormLabel(f);}).join(', ')+'</span>';
}
// Construire le texte de la règle
var conditionsText='';
if(rule.conditions && rule.conditions.length>0){
conditionsText='<strong>Si :</strong> ';
rule.conditions.forEach(function(c,i){
if(i>0)conditionsText+=' ET ';
conditionsText+=c.field+' '+getOperatorLabel(c.operator)+' "'+c.value+'"';
});
}else{
conditionsText='<strong>Toujours</strong>';
}
var actionsText='<strong>Alors :</strong> ';
rule.actions.forEach(function(a,i){
if(i>0)actionsText+=', ';
if(a.type==='make_mandatory'){
actionsText+=a.field+' devient obligatoire';
}else if(a.type==='make_optional'){
actionsText+=a.field+' devient non obligatoire';
}else if(a.type==='must_equal'){
actionsText+=a.field+' doit égaler "'+a.value+'"';
}else if(a.type==='must_be_negative'){
actionsText+=a.field+' doit être négatif';
}else if(a.type==='must_equal_sum'){
actionsText+=a.field+' doit égaler '+(a.field1||'?')+' + '+(a.field2||'?');
}else if(a.type==='must_equal_product'){
actionsText+=a.field+' doit égaler '+(a.field1||'?')+' × '+(a.field2||'?')+' (tolérance '+(a.tolerance||'0.01')+')';
}else if(a.type==='must_equal_sum_of_all'){
actionsText+=a.field+' doit égaler Σ '+(a.sum_field||'?')+' (tolérance '+(a.tolerance||'0.01')+')';
}
});
div.innerHTML='<div class="rule-header '+enabledClass+'">'+
'<div class="rule-title">'+
'<strong>'+rule.name+'</strong>'+
'<span class="rule-status">'+enabledText+'</span>'+
'</div>'+
'<div class="rule-actions-btn">'+
'<button class="btn-toggle" data-index="'+index+'">'+(rule.enabled?'Désactiver':'Activer')+'</button>'+
'<button class="btn-edit" data-index="'+index+'">Éditer</button>'+
'<button class="btn-clone" data-index="'+index+'" title="Dupliquer cette règle">⎘ Cloner</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>'+
'</div>'+
'<div class="rule-body">'+
(rule.description?'<div class="rule-description">'+rule.description+'</div>':'')+
'<div style="margin-bottom:10px"><strong>Types de factures :</strong> '+formsText+'</div>'+
'<div class="rule-logic">'+
'<div>'+conditionsText+'</div>'+
'<div>'+actionsText+'</div>'+
'</div>'+
'</div>';
container.appendChild(div);
});
});
document.querySelectorAll('.btn-toggle').forEach(function(btn){
btn.addEventListener('click',function(){
var idx=parseInt(this.getAttribute('data-index'));
currentRules.rules[idx].enabled=!currentRules.rules[idx].enabled;
saveRules();
});
});
document.querySelectorAll('.btn-edit').forEach(function(btn){
btn.addEventListener('click',function(){
editRule(parseInt(this.getAttribute('data-index')));
});
});
document.querySelectorAll('.btn-clone').forEach(function(btn){
btn.addEventListener('click',function(){
var idx=parseInt(this.getAttribute('data-index'));
var src=currentRules.rules[idx];
var copy=JSON.parse(JSON.stringify(src));
copy.id='rule_'+Date.now();
copy.name=(src.name||'Règle')+' (copie)';
currentRules.rules.splice(idx+1,0,copy);
saveRules();
});
});
document.querySelectorAll('.btn-delete').forEach(function(btn){
btn.addEventListener('click',function(){
if(confirm('Supprimer cette règle ?')){
currentRules.rules.splice(parseInt(this.getAttribute('data-index')),1);
saveRules();
}
});
});
}

function getOperatorLabel(op){
var labels={
'equals':'=',
'not_equals':'≠',
'contains':'contient',
'not_contains':'ne contient pas',
'starts_with':'commence par',
'not_starts_with':'ne commence pas par',
'less_than':'<',
'greater_than':'>',
'is_empty':'est vide',
'is_not_empty':'n\'est pas vide'
};
return labels[op]||op;
}

async function saveRules(){
await fetch(BASE+'/api/rules',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentRules)
});
displayRules();
}

document.getElementById('btnReloadRules').addEventListener('click',loadRules);
document.getElementById('filterFormType').addEventListener('change',displayRules);
document.getElementById('btnAddRule').addEventListener('click',function(){
currentRuleIndex=null;
document.getElementById('ruleModalTitle').textContent='Créer une règle';
document.getElementById('ruleName').value='';
document.getElementById('ruleDescription').value='';
refreshCategorySelect();
document.getElementById('ruleCategory').value='Autre';
document.getElementById('ruleEnabled').checked=true;
renderFormCheckboxes(null); // tout coché par défaut
editingConditions=[];
editingActions=[];
renderConditions();
renderActions();
document.getElementById('editRuleModal').style.display='block';
});

var currentRuleIndex=null;
var editingConditions=[];
var editingActions=[];

function editRule(index){
currentRuleIndex=index;
var rule=currentRules.rules[index];
document.getElementById('ruleModalTitle').textContent='Éditer la règle';
document.getElementById('ruleName').value=rule.name;
document.getElementById('ruleDescription').value=rule.description||'';
refreshCategorySelect();
var cat=rule.category||'Autre';
var catSel=document.getElementById('ruleCategory');
if(ruleCategories.indexOf(cat)===-1){
// Catégorie inconnue saisie manuellement : on l'ajoute à la liste
var opt=document.createElement('option');opt.value=cat;opt.textContent=cat;catSel.appendChild(opt);
}
catSel.value=cat;
document.getElementById('ruleEnabled').checked=rule.enabled!==false;
renderFormCheckboxes(rule.applicable_forms||[]);
editingConditions=JSON.parse(JSON.stringify(rule.conditions||[]));
editingActions=JSON.parse(JSON.stringify(rule.actions||[]));
renderConditions();
renderActions();
document.getElementById('editRuleModal').style.display='block';
}

function renderConditions(){
var container=document.getElementById('conditionsList');
container.innerHTML='';
if(editingConditions.length===0){
container.innerHTML='<p style="color:#999;font-size:0.9em">Aucune condition (la règle s\'appliquera toujours)</p>';
return;
}
editingConditions.forEach(function(cond,i){
var div=document.createElement('div');
div.className='condition-item';
var isRdi=(cond.field_type==='rdi');
// Construire les options selon le type sélectionné
var fieldOptions='<option value="">Champ...</option>';
var opts=isRdi?availableRDIs:availableBTs;
opts.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
div.innerHTML=
'<select class="cond-type" data-index="'+i+'" title="Type de champ" style="width:60px;flex-shrink:0">'+
'<option value="bt"'+(isRdi?'':' selected')+'>BT</option>'+
'<option value="rdi"'+(isRdi?' selected':'')+'>RDI</option>'+
'</select>'+
'<select class="cond-field" data-index="'+i+'">'+fieldOptions+'</select>'+
'<select class="cond-op" data-index="'+i+'">'+
'<option value="equals">= (égal)</option>'+
'<option value="not_equals">≠ (différent)</option>'+
'<option value="contains">contient</option>'+
'<option value="not_contains">ne contient pas</option>'+
'<option value="starts_with">commence par</option>'+
'<option value="not_starts_with">ne commence pas par</option>'+
'<option value="less_than">&lt; (inférieur)</option>'+
'<option value="greater_than">&gt; (supérieur)</option>'+
'<option value="is_empty">est vide</option>'+
'<option value="is_not_empty">n\'est pas vide</option>'+
'</select>'+
'<input type="text" class="cond-value" data-index="'+i+'" placeholder="Valeur" value="'+cond.value+'">'+
'<button class="btn-remove" data-index="'+i+'">Supprimer</button>';
container.appendChild(div);
div.querySelector('.cond-field').value=cond.field;
div.querySelector('.cond-op').value=cond.operator;
});
document.querySelectorAll('.cond-type').forEach(function(el){
el.addEventListener('change',function(){
var idx=parseInt(this.getAttribute('data-index'));
editingConditions[idx].field_type=this.value;
editingConditions[idx].field='';
renderConditions();
});
});
document.querySelectorAll('.cond-field').forEach(function(el){
el.addEventListener('change',function(){
editingConditions[this.getAttribute('data-index')].field=this.value;
});
});
document.querySelectorAll('.cond-op').forEach(function(el){
el.addEventListener('change',function(){
editingConditions[this.getAttribute('data-index')].operator=this.value;
});
});
document.querySelectorAll('.cond-value').forEach(function(el){
el.addEventListener('input',function(){
editingConditions[this.getAttribute('data-index')].value=this.value;
});
});
document.querySelectorAll('.condition-item .btn-remove').forEach(function(btn){
btn.addEventListener('click',function(){
editingConditions.splice(parseInt(this.getAttribute('data-index')),1);
renderConditions();
});
});
}

function renderActions(){
var container=document.getElementById('actionsList');
container.innerHTML='';
if(editingActions.length===0){
container.innerHTML='<p style="color:#999;font-size:0.9em">Aucune action</p>';
return;
}
editingActions.forEach(function(action,i){
var div=document.createElement('div');
div.className='action-item';
var isRdi=(action.field_type==='rdi');
// Construire les options dynamiquement avec libellés complets
var fieldOptions='<option value="">Champ...</option>';
var opts=isRdi?availableRDIs:availableBTs;
opts.forEach(function(bt){
fieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
// Options BT toujours pour les champs de calcul (field1/field2/sum-field)
var btFieldOptions='<option value="">Champ...</option>';
availableBTs.forEach(function(bt){
btFieldOptions+='<option value="'+bt.value+'">'+bt.label+'</option>';
});
var needsValue=(action.type==='must_equal');
var needsSum=(action.type==='must_equal_sum');
var needsProduct=(action.type==='must_equal_product');
var needsSumAll=(action.type==='must_equal_sum_of_all');
// ORDRE: Type (BT/RDI), Champ, Type d'action, Valeur (si nécessaire), Supprimer
div.innerHTML=
'<select class="action-ftype" data-index="'+i+'" title="Type de champ" style="width:60px;flex-shrink:0">'+
'<option value="bt"'+(isRdi?'':' selected')+'>BT</option>'+
'<option value="rdi"'+(isRdi?' selected':'')+'>RDI</option>'+
'</select>'+
'<select class="action-field" data-index="'+i+'">'+fieldOptions+'</select>'+
'<select class="action-type" data-index="'+i+'">'+
'<option value="make_mandatory">Rendre obligatoire</option>'+
'<option value="make_optional">Rendre non obligatoire</option>'+
'<option value="must_equal">Doit égaler</option>'+
'<option value="must_be_negative">Doit être négatif</option>'+
'<option value="must_equal_sum">Doit égaler la somme de</option>'+
'<option value="must_equal_product">Doit égaler le produit de</option>'+
'<option value="must_equal_sum_of_all">Doit égaler Σ de toutes les lignes</option>'+
'</select>'+
(needsValue?'<input type="text" class="action-value" data-index="'+i+'" placeholder="Valeur" value="'+(action.value||'')+'">':'')+
(needsSum?'<select class="action-field1" data-index="'+i+'">'+btFieldOptions+'</select><span style="padding:0 4px;font-weight:bold">+</span><select class="action-field2" data-index="'+i+'">'+btFieldOptions+'</select>':'')+
(needsProduct?'<select class="action-field1" data-index="'+i+'">'+btFieldOptions+'</select><span style="padding:0 4px;font-weight:bold">×</span><select class="action-field2" data-index="'+i+'">'+btFieldOptions+'</select><input type="number" class="action-tolerance" data-index="'+i+'" placeholder="Tolérance (€)" step="0.01" min="0" style="width:110px" value="'+(action.tolerance!=null?action.tolerance:'0.01')+'"><span style="padding:0 4px;font-size:0.85em;color:#888">€ écart max</span>':'')+
(needsSumAll?'<span style="padding:0 4px">Σ</span><select class="action-sum-field" data-index="'+i+'">'+btFieldOptions+'</select><input type="number" class="action-tolerance" data-index="'+i+'" placeholder="Tolérance (€)" step="0.01" min="0" style="width:110px" value="'+(action.tolerance!=null?action.tolerance:'0.01')+'"><span style="padding:0 4px;font-size:0.85em;color:#888">€ écart max</span>':'')+
'<button class="btn-remove" data-index="'+i+'">Supprimer</button>';
container.appendChild(div);
div.querySelector('.action-field').value=action.field;
div.querySelector('.action-type').value=action.type;
if(needsSum||needsProduct){
if(div.querySelector('.action-field1'))div.querySelector('.action-field1').value=action.field1||'';
if(div.querySelector('.action-field2'))div.querySelector('.action-field2').value=action.field2||'';
}
if(needsSumAll){
if(div.querySelector('.action-sum-field'))div.querySelector('.action-sum-field').value=action.sum_field||'';
}
});
document.querySelectorAll('.action-ftype').forEach(function(el){
el.addEventListener('change',function(){
var idx=parseInt(this.getAttribute('data-index'));
editingActions[idx].field_type=this.value;
editingActions[idx].field='';
renderActions();
});
});
document.querySelectorAll('.action-type').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].type=this.value;
renderActions();
});
});
document.querySelectorAll('.action-field').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field=this.value;
});
});
document.querySelectorAll('.action-value').forEach(function(el){
el.addEventListener('input',function(){
editingActions[this.getAttribute('data-index')].value=this.value;
});
});
document.querySelectorAll('.action-field1').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field1=this.value;
});
});
document.querySelectorAll('.action-field2').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].field2=this.value;
});
});
document.querySelectorAll('.action-sum-field').forEach(function(el){
el.addEventListener('change',function(){
editingActions[this.getAttribute('data-index')].sum_field=this.value;
});
});
document.querySelectorAll('.action-tolerance').forEach(function(el){
el.addEventListener('input',function(){
var v=parseFloat(this.value);
editingActions[this.getAttribute('data-index')].tolerance=isNaN(v)?0.01:v;
});
});
document.querySelectorAll('.action-item .btn-remove').forEach(function(btn){
btn.addEventListener('click',function(){
editingActions.splice(parseInt(this.getAttribute('data-index')),1);
renderActions();
});
});
}

document.getElementById('btnAddCondition').addEventListener('click',function(){
editingConditions.push({field_type:'bt',field:'',operator:'equals',value:''});
renderConditions();
});

document.getElementById('btnAddAction').addEventListener('click',function(){
editingActions.push({field_type:'bt',type:'make_mandatory',field:''});
renderActions();
});

document.getElementById('ruleModalClose').addEventListener('click',function(){
document.getElementById('editRuleModal').style.display='none';
});

document.getElementById('btnSaveRule').addEventListener('click',function(){
var applicableForms=[];
document.querySelectorAll('#ruleFormsContainer .rule-form-cb').forEach(function(cb){
if(cb.checked)applicableForms.push(cb.dataset.value);
});
// Si toutes cochées, on stocke un tableau vide (= tous types)
if(applicableForms.length===availableForms.length)applicableForms=[];
var rule={
id:currentRuleIndex!==null?currentRules.rules[currentRuleIndex].id:'rule_'+Date.now(),
name:document.getElementById('ruleName').value,
description:document.getElementById('ruleDescription').value,
category:document.getElementById('ruleCategory').value||'Autre',
enabled:document.getElementById('ruleEnabled').checked,
applicable_forms:applicableForms,
conditions:editingConditions.filter(function(c){return c.field}),
actions:editingActions.filter(function(a){return a.field})
};
if(!rule.name){
alert('Veuillez donner un nom à la règle');
return;
}
if(rule.actions.length===0){
alert('Veuillez ajouter au moins une action');
return;
}
if(currentRuleIndex!==null){
currentRules.rules[currentRuleIndex]=rule;
}else{
currentRules.rules.push(rule);
}
saveRules();
document.getElementById('editRuleModal').style.display='none';
});

/* ---- DETAIL MODAL : aperçu détaillé style facture ---- */
function _detFmtAmount(v,currency){
  if(!v) return '';
  var n=_parseAmtFR(v);
  if(isNaN(n)) return String(v);
  return n.toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2})+(currency?' '+currency:' €');
}
function _detFmtDate(v){
  if(!v) return '';
  var s=String(v).trim();
  var m=s.match(/^(\d{4})-?(\d{2})-?(\d{2})/);
  if(m) return m[3]+'/'+m[2]+'/'+m[1];
  if(/^\d{2}\/\d{2}\/\d{4}/.test(s)) return s.slice(0,10);
  return s;
}
function _detGet(results,balise){
  if(!results) return '';
  for(var i=0;i<results.length;i++){
    if(results[i].balise===balise){
      var v=(results[i].rdi||'').toString().trim();
      if(!v) v=(results[i].xml||'').toString().trim();
      return v;
    }
  }
  return '';
}
function _detGetAll(results,balise){
  // Récupère tous les xml_all si disponibles, sinon liste des valeurs trouvées
  if(!results) return [];
  for(var i=0;i<results.length;i++){
    if(results[i].balise===balise){
      var arr=results[i].xml_all;
      if(arr&&arr.length) return arr.slice();
      var v=(results[i].rdi||'').toString().trim()||(results[i].xml||'').toString().trim();
      return v?[v]:[];
    }
  }
  return [];
}
function _detKv(label,value){
  if(value==null||value==='') return '';
  return '<div class="kv"><span class="k">'+escHtml(label)+'</span><span class="v">'+escHtml(value)+'</span></div>';
}
function _detBuildParty(results,role){
  // role: 'seller' (BG-4: BT-27..40) ou 'buyer' (BG-7: BT-44..55, BT-49)
  var get=function(b){return _detGet(results,b);};
  var name,trade,vat,siren,reg,info,email,a1,a2,a3,city,zip,sub,ctry,id,scheme,chorus;
  if(role==='seller'){
    name=get('BT-27');trade=get('BT-28');siren=get('BT-30');vat=get('BT-31');reg=get('BT-32');info=get('BT-33');email=get('BT-34');
    a1=get('BT-35');a2=get('BT-36');a3=get('BT-162');city=get('BT-37');zip=get('BT-38');sub=get('BT-39');ctry=get('BT-40');
    id=get('BT-29');scheme='';chorus='';
  }else{
    name=get('BT-44');trade=get('BT-45');id=get('BT-46');scheme=get('BT-47');vat=get('BT-48');chorus=get('BT-49');
    a1=get('BT-50');a2=get('BT-51');a3=get('BT-163');city=get('BT-52');zip=get('BT-53');sub=get('BT-54');ctry=get('BT-55');email=get('BT-58');reg='';info='';siren='';
  }
  if(!name&&!a1&&!vat&&!id) return '';
  var label=role==='seller'?'Vendeur':'Acheteur';
  var addr=[];
  var l1=[a1,a2].filter(Boolean).join(' ').trim();if(l1) addr.push(l1);
  if(a3) addr.push(a3);
  var l3=[zip,city].filter(Boolean).join(' ').trim();if(l3) addr.push(l3);
  var l4=[sub,ctry].filter(Boolean).join(' — ').trim();if(l4) addr.push(l4);
  var ids='';
  var emptyChip='<span class="empty-chip">(vide)</span>';
  if(role==='seller'){
    if(siren) ids+='<div><b>SIREN/SIRET</b> <code>'+escHtml(siren)+'</code></div>';
    if(id&&id!==siren) ids+='<div><b>Identifiant</b> <code>'+escHtml(id)+'</code></div>';
  }else{
    // Pour l'acheteur, BT-46 = identifiant légal (SIREN/SIRET en France selon BT-47).
    // On affiche toujours la ligne, même vide, pour signaler l'absence.
    var schemeLbl='SIREN/SIRET';
    if(scheme){
      var s=String(scheme).trim();
      if(s==='0002') schemeLbl='SIREN';
      else if(s==='0009') schemeLbl='SIRET';
      // schéma inconnu → on garde "SIREN/SIRET" générique (pas d'affichage du code cryptique)
    }
    ids+='<div><b>'+escHtml(schemeLbl)+'</b> '+(id?('<code>'+escHtml(id)+'</code>'):emptyChip)+'</div>';
  }
  if(vat) ids+='<div><b>N° TVA</b> <code>'+escHtml(vat)+'</code></div>';
  if(reg) ids+='<div><b>RCS</b> '+escHtml(reg)+'</div>';
  if(chorus) ids+='<div><b>Code service</b> <code>'+escHtml(chorus)+'</code></div>';
  if(email) ids+='<div><b>Email</b> '+escHtml(email)+'</div>';
  if(info) ids+='<div style="color:#64748b;font-style:italic;font-size:0.94em;margin-top:4px">'+escHtml(info)+'</div>';
  return '<div class="inv-detail-party '+role+'">'+
    '<div class="party-label">'+label+'</div>'+
    (name?'<div class="party-name">'+escHtml(name)+'</div>':'')+
    (trade?'<div class="party-trade">'+escHtml(trade)+'</div>':'')+
    (addr.length?'<div class="party-addr">'+escHtml(addr.join('\n'))+'</div>':'')+
    (ids?'<div class="party-id">'+ids+'</div>':'')+
    '</div>';
}
function _detBuildLines(results,currency){
  // Regrouper les entrées articles par article_index
  var groups={};
  var order=[];
  for(var i=0;i<results.length;i++){
    var r=results[i];
    if(r.categorie_bg!=='BG-LIGNES') continue;
    var k=r.article_index;
    if(k==null) continue;
    if(!(k in groups)){groups[k]={index:k,line_id:r.article_line_id||'',name:r.article_name||'',fields:{}};order.push(k);}
    var v=(r.rdi||'').toString().trim()||(r.xml||'').toString().trim();
    if(v) groups[k].fields[r.balise]=v;
  }
  if(!order.length) return '';
  var rows='';
  for(var j=0;j<order.length;j++){
    var g=groups[order[j]];
    var f=g.fields;
    var lineId=g.line_id||f['BT-126']||(g.index+1);
    var name=g.name||f['BT-153']||'';
    var desc=f['BT-154']||'';
    var qty=f['BT-129']||'';
    var unit=f['BT-130']||'';
    var pu=f['BT-146']||'';
    var pug=f['BT-148']||'';
    var net=f['BT-131']||'';
    var taux=f['BT-152']||'';
    var ref=f['BT-155']||f['BT-156']||'';
    var qtyN=_parseAmtFR(qty);
    var qtyFmt=(qty&&!isNaN(qtyN))?(qtyN.toLocaleString('fr-FR',{maximumFractionDigits:3})+(unit?' '+unit:'')):(qty?String(qty)+(unit?' '+unit:''):'');
    rows+='<tr>'+
      '<td class="line-num">'+escHtml(lineId)+'</td>'+
      '<td class="designation">'+escHtml(name||'—')+
        (desc?'<small>'+escHtml(desc)+'</small>':'')+
        (ref?'<small>Réf : '+escHtml(ref)+'</small>':'')+
      '</td>'+
      '<td class="num">'+escHtml(qtyFmt)+'</td>'+
      '<td class="num">'+escHtml(pu?_detFmtAmount(pu,currency):'')+'</td>'+
      '<td class="num">'+escHtml(net?_detFmtAmount(net,currency):'')+'</td>'+
      '<td class="num">'+escHtml(taux?((isNaN(_parseAmtFR(taux))?String(taux):_parseAmtFR(taux).toLocaleString('fr-FR',{maximumFractionDigits:2}))+' %'):'')+'</td>'+
    '</tr>';
  }
  return '<table class="inv-detail-table">'+
    '<thead><tr><th>N°</th><th>Désignation</th><th style="text-align:right">Qté</th><th style="text-align:right">PU</th><th style="text-align:right">Montant HT</th><th style="text-align:right">TVA</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table>';
}
function _detBuildVat(results,currency){
  var bases=_detGetAll(results,'BT-116');
  var taxes=_detGetAll(results,'BT-117');
  var cats=_detGetAll(results,'BT-118');
  var rates=_detGetAll(results,'BT-119');
  var reasons=_detGetAll(results,'BT-121');
  var n=Math.max(bases.length,taxes.length,cats.length,rates.length);
  if(!n) return '';
  var rows='';
  for(var i=0;i<n;i++){
    var b=bases[i]||'',t=taxes[i]||'',c=cats[i]||'',r=rates[i]||'',rs=reasons[i]||'';
    rows+='<tr>'+
      '<td>'+escHtml(c||'—')+(rs?' <span style="color:#94a3b8;font-style:italic">('+escHtml(rs)+')</span>':'')+'</td>'+
      '<td>'+escHtml(r?((isNaN(_parseAmtFR(r))?String(r):_parseAmtFR(r).toLocaleString('fr-FR',{maximumFractionDigits:2}))+' %'):'—')+'</td>'+
      '<td>'+escHtml(b?_detFmtAmount(b,currency):'—')+'</td>'+
      '<td>'+escHtml(t?_detFmtAmount(t,currency):'—')+'</td>'+
    '</tr>';
  }
  return '<table class="inv-detail-vat-table"><thead><tr><th>Catégorie</th><th>Taux</th><th>Base</th><th>TVA</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
function _detBuildTotals(results,currency){
  var get=function(b){return _detGet(results,b);};
  var rows=[];
  var bt106=get('BT-106'),bt107=get('BT-107'),bt108=get('BT-108');
  var bt109=get('BT-109'),bt110=get('BT-110'),bt112=get('BT-112');
  var bt113=get('BT-113'),bt114=get('BT-114'),bt115=get('BT-115');
  if(bt106) rows.push('<div class="row sub"><span>Sous-total HT lignes</span><span class="v">'+escHtml(_detFmtAmount(bt106,currency))+'</span></div>');
  if(bt107){var nb=_parseAmtFR(bt107);if(!isNaN(nb)&&Math.abs(nb)>0.0001) rows.push('<div class="row sub"><span>Remises documentaires</span><span class="v">− '+escHtml(_detFmtAmount(bt107,currency))+'</span></div>');}
  if(bt108){var na=_parseAmtFR(bt108);if(!isNaN(na)&&Math.abs(na)>0.0001) rows.push('<div class="row sub"><span>Charges documentaires</span><span class="v">+ '+escHtml(_detFmtAmount(bt108,currency))+'</span></div>');}
  if(bt109) rows.push('<div class="row divider"><span><b>Total HT</b></span><span class="v">'+escHtml(_detFmtAmount(bt109,currency))+'</span></div>');
  if(bt110) rows.push('<div class="row"><span>Total TVA</span><span class="v">'+escHtml(_detFmtAmount(bt110,currency))+'</span></div>');
  if(bt112) rows.push('<div class="row"><span><b>Total TTC</b></span><span class="v">'+escHtml(_detFmtAmount(bt112,currency))+'</span></div>');
  if(bt113){var nc=_parseAmtFR(bt113);if(!isNaN(nc)&&Math.abs(nc)>0.0001) rows.push('<div class="row sub"><span>Acompte payé</span><span class="v">− '+escHtml(_detFmtAmount(bt113,currency))+'</span></div>');}
  if(bt114){var nd=_parseAmtFR(bt114);if(!isNaN(nd)&&Math.abs(nd)>0.0001) rows.push('<div class="row sub"><span>Arrondi</span><span class="v">'+escHtml(_detFmtAmount(bt114,currency))+'</span></div>');}
  if(bt115) rows.push('<div class="row net"><span>Net à payer</span><span class="v">'+escHtml(_detFmtAmount(bt115,currency))+'</span></div>');
  if(!rows.length) return '';
  return '<div class="inv-detail-totals">'+rows.join('')+'</div>';
}
function _detBuildPayment(results){
  var get=function(b){return _detGet(results,b);};
  var bt81=get('BT-81'),bt82=get('BT-82'),bt83=get('BT-83');
  var bt84=get('BT-84'),bt85=get('BT-85'),bt86=get('BT-86'),bt87=get('BT-87');
  var bt89=get('BT-89'),bt90=get('BT-90'),bt91=get('BT-91'),bt20=get('BT-20');
  var rows=[];
  if(bt82||bt81) rows.push(_detKv('Mode de paiement',bt82||bt81));
  if(bt83) rows.push(_detKv('Référence paiement',bt83));
  if(bt84) rows.push(_detKv('IBAN',bt84));
  if(bt85) rows.push(_detKv('Titulaire compte',bt85));
  if(bt86) rows.push(_detKv('BIC',bt86));
  if(bt87) rows.push(_detKv('N° de compte',bt87));
  if(bt89) rows.push(_detKv('Carte (4 derniers)',bt89));
  if(bt90) rows.push(_detKv('Mandat SEPA',bt90));
  if(bt91) rows.push(_detKv('Compte débiteur',bt91));
  if(bt20) rows.push(_detKv('Conditions',bt20));
  rows=rows.filter(Boolean);
  if(!rows.length) return '';
  return '<div class="inv-detail-kv">'+rows.join('')+'</div>';
}
function _detBuildNotes(results){
  var labels={
    'BAR':'Traitement attendu',
    'SUR':'Remarques fournisseur',
    'ADN':'Référence Chorus (B2G)',
    'AAB':'Escompte',
    'PMT':'Indemnité forfaitaire',
    'PMD':'Pénalités de retard'
  };
  var keys=['BAR','SUR','ADN','AAB','PMT','PMD'];
  var html='';
  for(var i=0;i<keys.length;i++){
    var sfx=keys[i];
    var code=_detGet(results,'BT-21-'+sfx);
    var text=_detGet(results,'BT-22-'+sfx);
    if(!code&&!text) continue;
    html+='<div class="inv-detail-note">'+
      '<span class="nl">'+escHtml(labels[sfx]||sfx)+(code?' · '+escHtml(code):'')+'</span>'+
      escHtml(text||'(sans texte)')+
    '</div>';
  }
  return html?'<div class="inv-detail-notes">'+html+'</div>':'';
}
function showInvoiceDetails(){
  var results=window._lastInvoiceResults||[];
  var modal=document.getElementById('invDetailOverlay');
  if(!modal){
    modal=document.createElement('div');
    modal.id='invDetailOverlay';
    modal.className='inv-detail-overlay';
    modal.addEventListener('click',function(e){if(e.target===modal) closeInvoiceDetails();});
    document.body.appendChild(modal);
    document.addEventListener('keydown',function(e){if(e.key==='Escape') closeInvoiceDetails();});
  }
  var get=function(b){return _detGet(results,b);};
  var bt1=get('BT-1'),bt2=get('BT-2'),bt3=get('BT-3'),bt5=get('BT-5')||'EUR',bt9=get('BT-9');
  var bt7=get('BT-7'),bt19=get('BT-19'),bt10=get('BT-10'),bt13=get('BT-13'),bt11=get('BT-11'),bt12=get('BT-12');
  var bt72=get('BT-72'),bt73=get('BT-73'),bt74=get('BT-74');
  var bt15=get('BT-15'),bt16=get('BT-16'),bt17=get('BT-17'),bt18=get('BT-18');

  var docTypes={'380':'Facture','381':'Avoir','384':'Facture rectificative','386':"Facture d'acompte",'389':'Autofacture','326':'Facture partielle','393':'Régularisation','751':'Facture pour information','875':'Facture pro forma'};
  var typeLbl=bt3?(docTypes[String(bt3).trim()]||'Type '+bt3):'Facture';
  var typeInfo=_summDocType(bt3);
  var bannerCls=typeInfo&&typeInfo.cls?(' '+typeInfo.cls):'';

  var destInfo=_summDestination(get('BT-22-BAR'));
  var bannerBadges='';
  if(destInfo) bannerBadges+='<span class="b">'+destInfo.label+'</span>';
  if(bt5&&bt5!=='EUR') bannerBadges+='<span class="b">'+escHtml(bt5)+'</span>';

  var headerMeta='';
  if(bt2) headerMeta+='<span>📅 Émise le <b>'+escHtml(_detFmtDate(bt2))+'</b></span>';
  if(bt9) headerMeta+='<span>⏰ Échéance <b>'+escHtml(_detFmtDate(bt9))+'</b></span>';
  if(bt1) headerMeta+='<span class="num">N° '+escHtml(bt1)+'</span>';

  var partiesHtml='<div class="inv-detail-parties">'+
    _detBuildParty(results,'seller')+
    _detBuildParty(results,'buyer')+
  '</div>';

  // Section références & dates
  var bt25=get('BT-25'),bt26=get('BT-26');
  var isAvoir=String(bt3||'').trim()==='381';
  var refsKv=[];
  if(bt2) refsKv.push(_detKv("Date d'émission",_detFmtDate(bt2)));
  if(bt9) refsKv.push(_detKv("Date d'échéance",_detFmtDate(bt9)));
  if(bt7) refsKv.push(_detKv('Date point TVA',_detFmtDate(bt7)));
  if(bt72) refsKv.push(_detKv('Date livraison',_detFmtDate(bt72)));
  if(bt73||bt74) refsKv.push(_detKv('Période',(_detFmtDate(bt73)||'…')+' → '+(_detFmtDate(bt74)||'…')));
  if(bt10) refsKv.push(_detKv('Référence acheteur',bt10));
  if(bt13) refsKv.push(_detKv('N° de commande',bt13));
  if(bt11) refsKv.push(_detKv('Référence projet',bt11));
  if(bt12) refsKv.push(_detKv('Référence contrat',bt12));
  if(bt15) refsKv.push(_detKv('N° de réception',bt15));
  if(bt16) refsKv.push(_detKv('N° bon de livraison',bt16));
  if(bt17) refsKv.push(_detKv('N° marché public',bt17));
  if(bt18) refsKv.push(_detKv('Identifiant objet facturé',bt18));
  if(bt19) refsKv.push(_detKv('Code comptable',bt19));
  refsKv=refsKv.filter(Boolean);
  // Bloc dédié facture d'origine (BG-3 : BT-25 / BT-26) — mis en valeur pour les avoirs
  var origHtml='';
  if(bt25||bt26){
    var origLbl=isAvoir?"Facture d'origine (avoir)":'Facture précédente';
    origHtml='<div class="inv-detail-orig'+(isAvoir?' avoir':'')+'">'+
      '<div class="orig-label">↩ '+escHtml(origLbl)+'</div>'+
      '<div class="orig-rows">'+
        (bt25?'<span><b>N°</b> <code>'+escHtml(bt25)+'</code></span>':'')+
        (bt26?'<span><b>Émise le</b> '+escHtml(_detFmtDate(bt26))+'</span>':'')+
      '</div>'+
    '</div>';
  }
  var refsInner='';
  if(refsKv.length) refsInner+='<div class="inv-detail-kv">'+refsKv.join('')+'</div>';
  if(origHtml) refsInner+=origHtml;
  var refsHtml=refsInner?('<div class="inv-detail-section"><div class="inv-detail-section-title"><span class="icn">📎</span>Références &amp; dates</div>'+refsInner+'</div>'):'';

  var linesHtml=_detBuildLines(results,bt5);
  var linesSection=linesHtml?('<div class="inv-detail-section"><div class="inv-detail-section-title"><span class="icn">📋</span>Lignes de facture</div>'+linesHtml+'</div>'):'';

  var totalsHtml=_detBuildTotals(results,bt5);
  var vatHtml=_detBuildVat(results,bt5);
  var totalsSection=(totalsHtml||vatHtml)?
    ('<div class="inv-detail-section"><div class="inv-detail-section-title"><span class="icn">💰</span>Totaux &amp; TVA</div>'+
      (vatHtml?vatHtml:'')+
      (totalsHtml?totalsHtml:'')+
    '</div>'):'';

  var paymentHtml=_detBuildPayment(results);
  var paymentSection=paymentHtml?('<div class="inv-detail-section"><div class="inv-detail-section-title"><span class="icn">💳</span>Paiement</div>'+paymentHtml+'</div>'):'';

  var notesHtml=_detBuildNotes(results);
  var notesSection=notesHtml?('<div class="inv-detail-section"><div class="inv-detail-section-title"><span class="icn">📝</span>Notes</div>'+notesHtml+'</div>'):'';

  modal.innerHTML='<div class="inv-detail-modal">'+
    '<button type="button" class="inv-detail-close" onclick="closeInvoiceDetails()" title="Fermer (Echap)">✕</button>'+
    '<div class="inv-detail-banner'+bannerCls+'">'+
      '<h2>'+escHtml(typeLbl)+'</h2>'+
      '<div class="meta">'+headerMeta+'</div>'+
      (bannerBadges?'<div class="meta-badges">'+bannerBadges+'</div>':'')+
    '</div>'+
    '<div class="inv-detail-body">'+
      '<div class="inv-detail-section">'+partiesHtml+'</div>'+
      refsHtml+
      linesSection+
      totalsSection+
      paymentSection+
      notesSection+
    '</div>'+
  '</div>';
  modal.classList.add('visible');
  document.body.style.overflow='hidden';
}
function closeInvoiceDetails(){
  var modal=document.getElementById('invDetailOverlay');
  if(!modal) return;
  modal.classList.remove('visible');
  document.body.style.overflow='';
}

