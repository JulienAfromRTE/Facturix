#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Factur-X V11.0"""
from flask import Flask, render_template_string, request, jsonify
import os, json, PyPDF2
import logging
from lxml import etree
from collections import defaultdict

app = Flask(__name__)
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)  # ← dossier du .exe ✅
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(_file_))  # ← mode devUPLOAD_FOLDER = os.path.join(SCRIPT_DIR, 'uploads_temp')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def load_mapping(type_formulaire='CARTsimple'):
    filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None

def save_mapping(data, type_formulaire='simple'):
    filepath = os.path.join(SCRIPT_DIR, f'mapping_v5_{type_formulaire}.json')
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def parse_rdi(rdi_path):
    data = {}
    try:
        with open(rdi_path, 'r', encoding='cp1252') as f:
            for line in f:
                if line.startswith('DHEADER') or line.startswith('DMAIN'):
                    if len(line) >= 176:
                        try:
                            length_str = line[172:175]
                            length = int(length_str)
                            value = line[175:175+length] if len(line) > 175 else ''
                            tag_section = line[41:172].strip()
                            tag_parts = tag_section.split()
                            if tag_parts:
                                tag = tag_parts[-1]
                                if tag not in data:
                                    data[tag] = value
                        except:
                            pass
    except:
        pass
    return data

def extract_xml_from_pdf(pdf_path):
    try:
        with open(pdf_path, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            if '/Names' in pdf_reader.trailer['/Root']:
                names = pdf_reader.trailer['/Root']['/Names']
                if '/EmbeddedFiles' in names:
                    embedded = names['/EmbeddedFiles']['/Names']
                    for i in range(0, len(embedded), 2):
                        file_name = embedded[i]
                        if isinstance(file_name, str) and file_name.lower().endswith('.xml'):
                            file_spec = embedded[i + 1].get_object()
                            file_obj = file_spec['/EF']['/F'].get_object()
                            xml_content = file_obj.get_data()
                            return xml_content.decode('utf-8') if isinstance(xml_content, bytes) else xml_content
    except:
        pass
    return None

def get_xml_short_name(xpath):
    if not xpath:
        return ''
    parts = xpath.split('/')
    for part in reversed(parts):
        if ':' in part:
            return part.split(':')[1]
    return parts[-1] if parts else ''

def normalize_value(value):
    if not value:
        return ''
    value_str = str(value).strip()
    if any(char.isdigit() for char in value_str):
        value_str = value_str.replace(' ', '')
        if '.' in value_str and ',' in value_str:
            value_str = value_str.replace('.', '').replace(',', '.')
        elif ',' in value_str and '.' not in value_str:
            value_str = value_str.replace(',', '.')
        elif value_str.count('.') > 1:
            value_str = value_str.replace('.', '')
        try:
            num_value = float(value_str)
            if '.' in value_str:
                return f"{num_value:.10f}".rstrip('0').rstrip('.')
            else:
                return str(num_value)
        except ValueError:
            pass
    return value_str.upper()

def perform_controls(field, rdi_value, xml_value, type_controle):
    regles_testees = []
    details_erreurs = []
    status = 'OK'

    if field.get('obligatoire') == 'Oui':
        regles_testees.append('RTE: Presence obligatoire')
        if not rdi_value:
            status = 'ERREUR'
            details_erreurs.append('Champ obligatoire absent du RDI')

    if field.get('rdg'):
        regles_testees.append(f"RTE: {field['rdg'][:50]}...")

    controles_cegedim = field.get('controles_cegedim', [])
    for controle in controles_cegedim:
        regles_testees.append(f"CEG: {controle.get('ref')} - {controle.get('nature')}")
        if controle.get('nature') == 'Presence':
            if not rdi_value:
                status = 'ERREUR'
                details_erreurs.append(f"{controle.get('ref')}: {controle.get('message', 'Controle CEGEDIM echoue')}")

    if type_controle == 'xml':
        regles_testees.append('RTE: Comparaison RDI vs XML')
        if not xml_value and field.get('obligatoire') == 'Oui':
            status = 'ERREUR'
            details_erreurs.append('Absent du XML (obligatoire)')
        elif not xml_value and rdi_value:
            status = 'ERREUR'
            details_erreurs.append('Present dans RDI mais absent du XML')
        elif rdi_value and xml_value:
            rdi_normalized = normalize_value(rdi_value)
            xml_normalized = normalize_value(xml_value)
            if rdi_normalized != xml_normalized:
                status = 'ERREUR'
                details_erreurs.append(f"Valeurs differentes: RDI='{rdi_value}' vs XML='{xml_value}'")

    if not details_erreurs:
        details_erreurs = ['RAS']

    return status, regles_testees, details_erreurs

HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Factur-X V11</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#667eea;padding:20px}
.container{max-width:1600px;margin:0 auto;background:#fff;border-radius:20px;overflow:hidden}
.header{background:#366092;color:#fff;padding:30px;text-align:center}
.version{font-size:0.9em;opacity:0.8;margin-top:5px}
.tabs{display:flex;background:#f0f0f0}
.tab{padding:15px 30px;cursor:pointer;border:none;background:transparent;font-weight:600}
.tab.active{background:#fff;color:#366092}
.tab-content{display:none;padding:40px}
.tab-content.active{display:block}
.section{background:#f8f9fa;border-radius:12px;padding:25px;margin-bottom:25px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.form-group{display:flex;flex-direction:column}
.form-group label{font-weight:600;margin-bottom:8px}
.form-group select,.form-group input,.form-group textarea{padding:12px;border:2px solid #366092;border-radius:8px;font-size:1em}
.form-group textarea{min-height:80px;font-family:monospace;font-size:0.9em}
.help-box{background:#e7f3ff;border-left:4px solid #2196F3;padding:15px;margin:15px 0}
.btn{background:#70ad47;color:#fff;padding:18px;border:none;border-radius:10px;font-size:1.2em;cursor:pointer;width:100%}
.btn:hover{background:#5a8c39}
.btn-secondary{background:#366092;color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;margin-right:10px}
.btn-add{background:#28a745;color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer}
.loading{display:none;text-align:center;padding:20px}
.spinner{border:4px solid #f3f3f3;border-top:4px solid #366092;border-radius:50%;width:50px;height:50px;animation:spin 1s linear infinite;margin:0 auto}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.results{display:none}
/* Stats : 3 colonnes */
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:30px}
.stat-card{background:#fff;padding:20px;border-radius:10px;text-align:center}
.stat-value{font-size:2em;font-weight:bold}
.ok .stat-value{color:#70ad47}
.erreur .stat-value{color:#c00000}
.category{background:#fff;border-radius:10px;margin-bottom:15px}
.category-header{background:#366092;color:#fff;padding:20px;cursor:pointer;display:flex;justify-content:space-between}
.category-content{max-height:0;overflow:hidden;transition:max-height 0.3s}
.category-content.open{max-height:50000px}
table.main-table{width:100%;border-collapse:collapse;margin-top:10px}
table.main-table th{background:#366092;color:#fff;padding:12px;text-align:left;font-weight:600}
table.main-table td{padding:12px;border-bottom:1px solid #eee;vertical-align:top}
table.main-table tr.data-row:hover{background:#f0f4ff}
.col-status{width:50px;text-align:center;font-size:1.4em}
.col-oblig{width:55px;text-align:center;font-size:1.2em}
.col-bt{width:85px}
.col-libelle{width:200px}
/* Sous-tableau CEGEDIM */
table.ceg-table{width:100%;border-collapse:collapse;margin:6px 0 0 0;font-size:0.85em}
table.ceg-table th{background:#5b3fa0;color:#fff;padding:6px 10px;text-align:left}
table.ceg-table td{padding:6px 10px;border-bottom:1px solid #e0d0ff;background:#f8f4ff}
.ceg-row-header td{background:#f0e8ff;font-style:italic;font-size:0.8em;color:#5b3fa0;padding:4px 10px;border-bottom:1px dashed #ccc}
.tooltip{position:absolute;background:#333;color:#fff;padding:10px;border-radius:6px;font-size:0.9em;z-index:1000;display:none;max-width:420px;box-shadow:0 4px 8px rgba(0,0,0,0.3);pointer-events:none}
.tooltip strong{color:#ffc107;display:block;margin-bottom:4px}
/* Paramétrage */
.mapping-list{list-style:none}
.mapping-item{padding:14px 18px;margin:8px 0;border-radius:8px;border-left:4px solid #366092;display:flex;justify-content:space-between;align-items:center;background:#fff}
.mapping-item.valide{background:#e8f5e9;border-left-color:#388e3c}
.mapping-item-info{flex:1}
.mapping-item-info .item-main{font-weight:600}
.mapping-item-info .item-sub{font-size:0.82em;color:#555;margin-top:3px}
.mapping-item-info .item-xpath{font-size:0.78em;color:#888;font-family:monospace;margin-top:2px;word-break:break-all}
.mapping-actions{display:flex;align-items:center;gap:8px;flex-shrink:0}
.mapping-actions button{padding:7px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:600}
.btn-edit{background:#2196F3;color:#fff}
.btn-delete{background:#f44336;color:#fff}
.valide-toggle{display:flex;align-items:center;gap:5px;font-size:0.85em;color:#388e3c;font-weight:600;cursor:pointer}
.valide-toggle input{width:16px;height:16px;cursor:pointer;accent-color:#388e3c}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000}
.modal-content{background:#fff;margin:5% auto;padding:30px;border-radius:12px;max-width:800px;max-height:80vh;overflow-y:auto}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}
.modal-close{font-size:2em;cursor:pointer;color:#999;line-height:1}
.modal .form-group{margin-bottom:15px}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>Controle Factur-X V11.0</h1>
<div class="version">Made with love by Julien ❤️ enjoy</div>
</div>
<div class="tabs">
<button class="tab active" id="tabControle">Controle</button>
<button class="tab" id="tabParam">Parametrage</button>
<button class="tab" id="tabAide">Aide</button>
</div>

<!-- ONGLET CONTROLE -->
<div id="contentControle" class="tab-content active">
<div class="section">
<h2>Configuration</h2>
<div class="form-row">
<div class="form-group">
<label>Type de Formulaire SAP :</label>
<select id="typeFormulaire">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupe</option>
</select>
</div>
<div class="form-group">
<label>Type de Controle :</label>
<select id="typeControle">
<option value="xml">XML - Sortie Exstream (complet)</option>
<option value="rdi">RDI - Sortie SAP</option>
</select>
</div>
</div>
<div class="help-box" id="helpControle"></div>
</div>
<div class="section">
<h3>Fichiers</h3>
<div class="form-row">
<div class="form-group" id="groupePdf">
<label>PDF ou XML :</label>
<input type="file" id="pdfFile" accept=".pdf,.xml">
</div>
<div class="form-group">
<label>Fichier RDI :</label>
<input type="file" id="rdiFile" accept=".txt,.rdi">
</div>
</div>
<button class="btn" id="btnControle">LANCER LE CONTROLE</button>
</div>
<div class="loading" id="loading"><div class="spinner"></div><p>Controle en cours...</p></div>
<div class="results" id="results">
<div class="section">
<div class="stats">
<div class="stat-card"><div>Total</div><div class="stat-value" id="statTotal">0</div></div>
<div class="stat-card ok"><div>OK</div><div class="stat-value" id="statOk">0</div></div>
<div class="stat-card erreur"><div>Erreurs</div><div class="stat-value" id="statErreur">0</div></div>
</div>
</div>
<div class="section"><div id="categoriesContainer"></div></div>
</div>
</div>

<!-- ONGLET PARAMETRAGE -->
<div id="contentParam" class="tab-content">
<div class="section">
<h2>Gestion des Mappings</h2>
<div class="form-row">
<div class="form-group">
<label>Type de formulaire :</label>
<select id="typeFormulaireParam">
<option value="simple">CART Simple</option>
<option value="groupee">CART Groupe</option>
</select>
</div>
</div>
<button class="btn-secondary" id="btnReload">Actualiser</button>
<button class="btn-add" id="btnAdd">+ Ajouter un champ</button>
</div>
<div class="section">
<ul class="mapping-list" id="mappingList"></ul>
</div>
</div>

<!-- ONGLET AIDE -->
<div id="contentAide" class="tab-content">
<div class="section">
<h2>Guide V11.0</h2>
<h3>Nouveautes V11</h3>
<ul>
<li>Case a cocher "Valide" dans le parametrage, fond vert</li>
<li>Tableau CEGEDIM detaille par BT dans les resultats</li>
<li>XPath visible dans le parametrage</li>
<li>Stats simplifiees : Total / OK / Erreurs</li>
<li>Upload PDF masque en mode RDI</li>
</ul>
<h3>Mode RDI - Sortie SAP</h3>
<ol><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ol>
<h3>Mode XML - Sortie Exstream</h3>
<ol><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ol>
</div>
</div>
</div>

<!-- MODAL EDITION -->
<div id="editModal" class="modal">
<div class="modal-content">
<div class="modal-header">
<h2 id="modalTitle">Editer le Champ</h2>
<span class="modal-close" id="modalClose">&times;</span>
</div>
<div class="form-group"><label>Balise BT :</label><input type="text" id="editBalise"></div>
<div class="form-group"><label>Libelle :</label><input type="text" id="editLibelle"></div>
<div class="form-group"><label>Champ RDI :</label><input type="text" id="editRdi"></div>
<div class="form-group"><label>XPath :</label><input type="text" id="editXpath"></div>
<div class="form-group"><label>Type :</label>
<select id="editType"><option value="String">String</option><option value="Decimal">Decimal</option><option value="Date">Date</option></select>
</div>
<div class="form-group"><label>Obligatoire :</label>
<select id="editObligatoire"><option value="Oui">Oui</option><option value="Non">Non</option><option value="Dependant">Dependant</option></select>
</div>
<div class="form-group"><label>Regle de Gestion (RDG) :</label><textarea id="editRdg"></textarea></div>
<button class="btn" id="btnSave">Sauvegarder</button>
</div>
</div>

<div id="tooltip" class="tooltip"></div>
<script>
console.log('Init V11');
var currentMapping=null;
var currentIndex=null;
var tooltip=document.getElementById('tooltip');

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
});
document.getElementById('tabAide').addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
this.classList.add('active');
document.getElementById('contentAide').classList.add('active');
});

/* ---- AIDE CONTEXTUELLE + MASQUAGE PDF ---- */
function updateHelp(){
var type=document.getElementById('typeControle').value;
var help=document.getElementById('helpControle');
var groupePdf=document.getElementById('groupePdf');
if(type==='rdi'){
help.innerHTML='<strong>Mode RDI</strong><ul><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li></ul>';
groupePdf.style.display='none';
}else{
help.innerHTML='<strong>Mode XML</strong><ul><li>Presence obligatoire</li><li>Regles de gestion</li><li>Controles CEGEDIM</li><li>Comparaison RDI vs XML</li></ul>';
groupePdf.style.display='flex';
}
}
document.getElementById('typeControle').addEventListener('change',updateHelp);
updateHelp();

/* ---- LANCER CONTROLE ---- */
document.getElementById('btnControle').addEventListener('click',async function(){
var typeControle=document.getElementById('typeControle').value;
var pdf=document.getElementById('pdfFile').files[0];
var rdi=document.getElementById('rdiFile').files[0];
if(typeControle==='xml'&&!pdf){alert('Selectionnez le fichier PDF ou XML');return}
if(!rdi){alert('Selectionnez le fichier RDI');return}
document.getElementById('loading').style.display='block';
document.getElementById('results').style.display='none';
var fd=new FormData();
if(pdf)fd.append('pdf',pdf);
fd.append('rdi',rdi);
fd.append('type_formulaire',document.getElementById('typeFormulaire').value);
fd.append('type_controle',typeControle);
try{
var resp=await fetch('/controle',{method:'POST',body:fd});
var data=await resp.json();
if(data.error){alert('Erreur: '+data.error);return}
document.getElementById('statTotal').textContent=data.stats.total;
document.getElementById('statOk').textContent=data.stats.ok;
document.getElementById('statErreur').textContent=data.stats.erreur;
var cont=document.getElementById('categoriesContainer');
cont.innerHTML='';
for(var bgId in data.categories_results){
var cat=data.categories_results[bgId];
if(cat.champs.length===0)continue;
var div=document.createElement('div');
div.className='category';
var errCount=cat.stats.erreur||0;
var headerBg=errCount>0?'background:#7b1e1e':'background:#366092';
var html='<div class="category-header" data-cat="'+bgId+'" style="'+headerBg+'">'+
'<div>'+cat.titre+'</div>'+
'<div>'+cat.stats.total+' champs | OK: '+cat.stats.ok+' | Err: '+errCount+'</div></div>'+
'<div class="category-content" id="cat-'+bgId+'">';
html+='<table class="main-table"><thead><tr>'+
'<th class="col-status"></th>'+
'<th class="col-oblig">Oblig.</th>'+
'<th class="col-bt">BT</th>'+
'<th class="col-libelle">Libelle</th>'+
'<th>Regles testees</th>'+
'<th>Details erreurs</th>'+
'</tr></thead><tbody>';
cat.champs.forEach(function(r){
var tooltipContent='<strong>RDI:</strong> '+r.rdi_field+' = '+(r.rdi||'(vide)');
if(data.type_controle==='xml'){
tooltipContent+='<br><strong>XML:</strong> '+r.balise+' ('+r.xml_short_name+') = '+(r.xml||'(vide)');
}
var statusIcon=r.status==='OK'?'✅':'❌';
var obligIcon=r.obligatoire==='Oui'?'⚠️':'';
var rowBg=r.status==='ERREUR'?'background:#fff5f5':'';
/* Ligne principale */
html+='<tr class="data-row" data-tooltip="'+tooltipContent.replace(/"/g,'&quot;')+'" style="'+rowBg+'">'+
'<td class="col-status">'+statusIcon+'</td>'+
'<td class="col-oblig">'+obligIcon+'</td>'+
'<td><strong>'+r.balise+'</strong></td>'+
'<td>'+r.libelle+'</td>'+
'<td><ul>';
r.regles_testees.forEach(function(regle){html+='<li>'+regle+'</li>'});
html+='</ul></td><td><ul>';
r.details_erreurs.forEach(function(err){html+='<li>'+err+'</li>'});
html+='</ul></td></tr>';
/* Sous-ligne CEGEDIM si des controles existent */
if(r.controles_cegedim&&r.controles_cegedim.length>0){
html+='<tr><td colspan="6" style="padding:0 12px 12px 40px;background:#faf8ff">'+
'<table class="ceg-table">'+
'<thead><tr><th>Ref</th><th>Categorie</th><th>Nature</th><th>Controle</th><th>Message</th></tr></thead><tbody>';
r.controles_cegedim.forEach(function(c){
html+='<tr>'+
'<td>'+( c.ref||'')+'</td>'+
'<td>'+(c.categorie||'')+'</td>'+
'<td>'+(c.nature||'')+'</td>'+
'<td>'+(c.description||c.controle||'')+'</td>'+
'<td>'+(c.message||'')+'</td>'+
'</tr>';
});
html+='</tbody></table></td></tr>';
}
});
html+='</tbody></table></div>';
div.innerHTML=html;
div.querySelector('.category-header').addEventListener('click',function(){
document.getElementById('cat-'+this.getAttribute('data-cat')).classList.toggle('open');
});
div.querySelectorAll('.data-row').forEach(function(row){
row.addEventListener('mouseenter',function(e){
tooltip.innerHTML=this.getAttribute('data-tooltip');
tooltip.style.display='block';
tooltip.style.left=(e.pageX+14)+'px';
tooltip.style.top=(e.pageY+14)+'px';
});
row.addEventListener('mousemove',function(e){
tooltip.style.left=(e.pageX+14)+'px';
tooltip.style.top=(e.pageY+14)+'px';
});
row.addEventListener('mouseleave',function(){tooltip.style.display='none'});
});
cont.appendChild(div);
}
document.getElementById('results').style.display='block';
}catch(e){
console.error(e);
alert('Erreur: '+e.message);
}finally{
document.getElementById('loading').style.display='none';
}
});

/* ---- PARAMETRAGE ---- */
async function loadMappings(){
var type=document.getElementById('typeFormulaireParam').value;
var resp=await fetch('/api/mapping/'+type);
currentMapping=await resp.json();
var list=document.getElementById('mappingList');
list.innerHTML='';
if(!currentMapping||!currentMapping.champs){list.innerHTML='<li>Aucun mapping</li>';return}
currentMapping.champs.forEach(function(champ,index){
var li=document.createElement('li');
var isValide=champ.valide===true;
li.className='mapping-item'+(isValide?' valide':'');
li.innerHTML=
'<div class="mapping-item-info">'+
'<div class="item-main"><strong>'+champ.balise+'</strong> — '+champ.libelle+'</div>'+
'<div class="item-sub">RDI: <code>'+champ.rdi+'</code> | Type: '+champ.type+' | Oblig.: '+champ.obligatoire+'</div>'+
'<div class="item-xpath">XPath: '+(champ.xpath||'—')+'</div>'+
'</div>'+
'<div class="mapping-actions">'+
'<label class="valide-toggle">'+
'<input type="checkbox" class="chk-valide" data-index="'+index+'"'+(isValide?' checked':'')+'> Valide'+
'</label>'+
'<button class="btn-edit" data-index="'+index+'">Editer</button>'+
'<button class="btn-delete" data-index="'+index+'">Supprimer</button>'+
'</div>';
list.appendChild(li);
});
document.querySelectorAll('.chk-valide').forEach(function(chk){
chk.addEventListener('change',async function(){
var idx=parseInt(this.getAttribute('data-index'));
currentMapping.champs[idx].valide=this.checked;
await saveMapping();
loadMappings();
});
});
document.querySelectorAll('.btn-edit').forEach(function(btn){
btn.addEventListener('click',function(){editMapping(this.getAttribute('data-index'))});
});
document.querySelectorAll('.btn-delete').forEach(function(btn){
btn.addEventListener('click',function(){deleteMapping(this.getAttribute('data-index'))});
});
}

function editMapping(index){
currentIndex=parseInt(index);
var champ=currentMapping.champs[currentIndex];
document.getElementById('modalTitle').textContent='Editer le Champ';
document.getElementById('editBalise').value=champ.balise;
document.getElementById('editLibelle').value=champ.libelle;
document.getElementById('editRdi').value=champ.rdi;
document.getElementById('editXpath').value=champ.xpath||'';
document.getElementById('editType').value=champ.type;
document.getElementById('editObligatoire').value=champ.obligatoire;
document.getElementById('editRdg').value=champ.rdg||'';
document.getElementById('editModal').style.display='block';
}
async function deleteMapping(index){
if(!confirm('Supprimer ce champ?'))return;
currentMapping.champs.splice(parseInt(index),1);
await saveMapping();
loadMappings();
}
document.getElementById('btnAdd').addEventListener('click',function(){
currentIndex=null;
document.getElementById('modalTitle').textContent='Ajouter un Champ';
document.getElementById('editBalise').value='';
document.getElementById('editLibelle').value='';
document.getElementById('editRdi').value='';
document.getElementById('editXpath').value='';
document.getElementById('editType').value='String';
document.getElementById('editObligatoire').value='Non';
document.getElementById('editRdg').value='';
document.getElementById('editModal').style.display='block';
});
document.getElementById('modalClose').addEventListener('click',function(){
document.getElementById('editModal').style.display='none';
});
document.getElementById('btnSave').addEventListener('click',async function(){
var base=currentIndex!==null?currentMapping.champs[currentIndex]:{};
var newChamp={
balise:document.getElementById('editBalise').value,
libelle:document.getElementById('editLibelle').value,
rdi:document.getElementById('editRdi').value,
xpath:document.getElementById('editXpath').value,
type:document.getElementById('editType').value,
obligatoire:document.getElementById('editObligatoire').value,
rdg:document.getElementById('editRdg').value,
categorie_bg:base.categorie_bg||'BG-OTHER',
categorie_titre:base.categorie_titre||'Autres',
controles_cegedim:base.controles_cegedim||[],
valide:base.valide||false
};
if(currentIndex!==null){
currentMapping.champs[currentIndex]=newChamp;
}else{
currentMapping.champs.push(newChamp);
}
await saveMapping();
document.getElementById('editModal').style.display='none';
loadMappings();
});
async function saveMapping(){
var type=document.getElementById('typeFormulaireParam').value;
await fetch('/api/mapping/'+type,{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(currentMapping)
});
}
document.getElementById('btnReload').addEventListener('click',loadMappings);
document.getElementById('typeFormulaireParam').addEventListener('change',loadMappings);
console.log('Ready V11');
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/mapping/<type_formulaire>')
def get_mapping(type_formulaire):
    data = load_mapping(type_formulaire)
    return jsonify(data if data else {'champs': []})

@app.route('/api/mapping/<type_formulaire>', methods=['POST'])
def save_mapping_route(type_formulaire):
    data = request.json
    success = save_mapping(data, type_formulaire)
    return jsonify({'success': success})

@app.route('/controle', methods=['POST'])
def controle():
    try:
        pdf_file = request.files.get('pdf')
        rdi_file = request.files.get('rdi')
        type_formulaire = request.form.get('type_formulaire', 'simple')
        type_controle = request.form.get('type_controle', 'xml')

        print(f"Controle: {type_formulaire}, {type_controle}")

        if not rdi_file:
            return jsonify({'error': 'Fichier RDI manquant'}), 400
        if type_controle == 'xml' and not pdf_file:
            return jsonify({'error': 'Fichier PDF/XML manquant pour le mode XML'}), 400

        rdi_path = os.path.join(UPLOAD_FOLDER, rdi_file.filename)
        rdi_file.save(rdi_path)

        xml_doc = None
        pdf_path = None
        if type_controle == 'xml' and pdf_file:
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_file.filename)
            pdf_file.save(pdf_path)
            if pdf_path.lower().endswith('.pdf'):
                xml_content = extract_xml_from_pdf(pdf_path)
                if not xml_content:
                    return jsonify({'error': 'XML introuvable dans le PDF'}), 400
            else:
                with open(pdf_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
            try:
                xml_doc = etree.fromstring(xml_content.encode('utf-8'))
            except:
                return jsonify({'error': 'XML invalide'}), 400

        rdi_data = parse_rdi(rdi_path)
        print("==== rdi_data ====")
        print(rdi_data)

        mapping_data = load_mapping(type_formulaire)
        if not mapping_data:
            return jsonify({'error': 'Mapping introuvable'}), 500

        mapping = mapping_data.get('champs', [])
        namespaces = {
            'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
            'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'
        }

        results = []
        for field in mapping:
            rdi_field_name = field.get('rdi', '')
            rdi_value = rdi_data.get(rdi_field_name, '').strip()
            if not rdi_value and rdi_field_name:
                for key in rdi_data.keys():
                    if key.upper() == rdi_field_name.upper():
                        rdi_value = rdi_data[key].strip()
                        break

            xml_value = ''
            if xml_doc is not None:
                try:
                    elements = xml_doc.xpath(field.get('xpath', '//none'), namespaces=namespaces)
                    xml_value = elements[0].text.strip() if elements and hasattr(elements[0], 'text') and elements[0].text else ''
                except:
                    pass

            status, regles_testees, details_erreurs = perform_controls(field, rdi_value, xml_value, type_controle)
            xml_short_name = get_xml_short_name(field.get('xpath', ''))

            # Construire la liste CEGEDIM détaillée pour le tableau dédié
            ceg_details = []
            for c in field.get('controles_cegedim', []):
                ceg_details.append({
                    'ref': c.get('ref', ''),
                    'categorie': c.get('categorie', ''),
                    'nature': c.get('nature', ''),
                    'description': c.get('description', c.get('controle', '')),
                    'message': c.get('message', '')
                })

            results.append({
                'balise': field.get('balise', ''),
                'libelle': field.get('libelle', ''),
                'rdi': rdi_value,
                'xml': xml_value,
                'rdi_field': rdi_field_name,
                'xml_short_name': xml_short_name,
                'status': status,
                'regles_testees': regles_testees,
                'details_erreurs': details_erreurs,
                'controles_cegedim': ceg_details,
                'categorie_bg': field.get('categorie_bg', 'BG-OTHER'),
                'categorie_titre': field.get('categorie_titre', 'Autres'),
                'obligatoire': field.get('obligatoire', 'Non')
            })

        stats = {
            'total': len(results),
            'ok': sum(1 for r in results if r['status'] == 'OK'),
            'erreur': sum(1 for r in results if r['status'] == 'ERREUR'),
        }

        categories_results = defaultdict(lambda: {'champs': [], 'stats': {'total': 0, 'ok': 0, 'erreur': 0}})
        for result in results:
            bg_id = result['categorie_bg']
            categories_results[bg_id]['champs'].append(result)
            categories_results[bg_id]['titre'] = result['categorie_titre']
            categories_results[bg_id]['stats']['total'] += 1
            if result['status'] == 'OK':
                categories_results[bg_id]['stats']['ok'] += 1
            elif result['status'] == 'ERREUR':
                categories_results[bg_id]['stats']['erreur'] += 1

        for bg_id in categories_results:
            categories_results[bg_id]['champs'].sort(key=lambda x: (0 if x['obligatoire'] == 'Oui' else 1, x['balise']))

        # Nettoyage
        os.remove(rdi_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

        return jsonify({
            'results': results,
            'stats': stats,
            'categories_results': dict(categories_results),
            'type_controle': type_controle
        })
    except Exception as e:
        print(f"ERREUR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("APPLICATION FACTUR-X V8.0")
    print("http://0.0.0.0:5000")  # Vérifiez que ça affiche bien 0.0.0.0
    print("="*60)
    import os
    os.environ['FLASK_RUN_HOST'] = '0.0.0.0'
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
