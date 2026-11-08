// Banc des chiffres affichés : exécute la page composée sous Node (DOM simulé), puis relève, pour chaque tuile et
// chaque carte, les valeurs que la page affiche et les valeurs de sa phrase de synthèse. Python les compare ensuite à
// un calcul indépendant (tests/oracle_chiffres.py).
// Usage : node banc_chiffres.js page.html '[{"filtres":{...},"gran":"Q"}, ...]'
const vm = require('vm'), fs = require('fs');
const el = {};
function noeud(id) {
  if (el[id]) return el[id];
  const base = { id, style: {}, innerHTML: '', textContent: '', hidden: false, value: '', dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelectorAll() { return []; }, querySelector() { return noeud('q' + Math.random()); },
    getBoundingClientRect() { return { height: 0, width: 0 }; } };
  return el[id] = new Proxy(base, { get: (t, k) => k in t ? t[k] : typeof k === 'string' ? () => undefined : undefined });
}
const ctx = { console, document: { getElementById: noeud, createElement: () => noeud('x' + Math.random()), write() {}, body: noeud('body'), querySelectorAll: () => [] },
  matchMedia: () => ({ matches: true }), addEventListener() {}, Math, Date, JSON, parent: { postMessage() {} } };
ctx.window = ctx; ctx.self = ctx; ctx.top = ctx;
vm.createContext(ctx);
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
vm.runInContext(scripts[scripts.length - 1], ctx);

const RELEVE = `(function(){
  var rows=filtered(),out={gran:gran,lignes:typeof cpNb==='function'?cpNb(rows):rows.length,periode:META.period||null,partiel:META.partiel||null,cube:!!META.cube,kpis:[],cartes:[]};
  KPIDEFS.slice(0,5).forEach(function(k){
    var R=k.hors?rows.filter(function(r){return k.hors.valeurs.indexOf(String(r[k.hors.colonne]).trim().toLowerCase())<0}):rows,
      G=kpiGros(k),v=G.v!=null?G.v:(k.formule?evalF(R,k.formule):agg(R,k.colonne,k.agregation));if(G.v==null&&v!=null)v*=G.f;
    var t=k.dimension?(k.formule?rankedF(R,k.formule,k.dimension,1):ranked(R,k.colonne,k.agregation,k.dimension,1))[0]:null;
    out.kpis.push({nom:k.nom,colonne:k.colonne,agregation:k.agregation,dimension:k.dimension||null,formule:!!k.formule,
      hors:k.hors||null,valeur:v,approx:!!G.a,tete:t?[t[0],G.t?t[1]:t[1]*G.f]:null,texte:fmt(v)})});
  var C=META.composition;if(!C)return out;
  C.lignes.forEach(function(ligne){ligne.forEach(function(c){
    var d=null;
    try{
      if(c.type==='courbe')d={serie:cpSerie(rows,c),complete:cpComplet(rows,cpSerie(rows,c))};
      else if(c.type==='classement')d={groupes:cpGroupes(rows,c)};
      else if(c.type==='anneau')d={groupes:cpGroupes(rows,c,5)};
      else if(c.type==='treemap')d={groupes:cpGroupes(rows,c,40)};
      else if(c.type==='chaleur'){var X=cpMatrice(rows,c);d=X?{P:X.P,D:X.D,M:X.M}:null}
      else if(c.type==='pente'||c.type==='passage'){var p=cpPeriodes(rows,c);d=p?{a:p.a,b:p.b,communs:p.communs,ecarts:cpEcarts(p,c)}:null}
      else if(c.type==='cible'){var L=cpCibleLignes(rows,c);d={e:L.e,mesure:agg(L.R,c.mesure,c.agregation),objectif:agg(L.R,c.objectif,c.agregation)}}
      else if(c.type==='bulles'){var g=groupBy(rows,c.dimension);d={e:Object.keys(g).map(function(k){return [k,agg(g[k],c.mesure,c.agregation),agg(g[k],c.mesure_y,c.agregation_y)]})}}
      else if(c.type==='comparee'){var Z=cpComparees(rows,c);d=Z?{P:Z.P,S:Z.S}:null}
      else if(c.type==='histogramme'){var T=cpTranches(rows,c);d=T?{mi:T.mi,ma:T.ma,b:T.b}:null}
    }catch(e){d={erreur:String(e)}}
    var m=null;try{m=CP_MSG[c.type](c,rows)}catch(e){m={erreur:String(e)}}
    out.cartes.push({type:c.type,mesure:c.mesure,agregation:c.agregation,dimension:c.dimension||null,objectif:c.objectif||null,
      mesure_y:c.mesure_y||null,agregation_y:c.agregation_y||null,mois_communs:c.mois_communs||null,nature:c.nature||null,
      donnees:d,message:m&&m.v?m.v:null,texte:cpMessage(c,rows)})})});
  return out})()`;

const sortie = [];
for (const s of JSON.parse(process.argv[3] || '[{}]')) {
  vm.runInContext('Object.keys(state).forEach(function(k){state[k]="all"})', ctx);
  for (const k in (s.filtres || {})) vm.runInContext('state[' + JSON.stringify(k) + ']=' + JSON.stringify(s.filtres[k]), ctx);
  if (s.gran) vm.runInContext('gran=' + JSON.stringify(s.gran), ctx);
  vm.runInContext('render()', ctx);
  sortie.push(Object.assign({scenario: s}, vm.runInContext(RELEVE, ctx)));
}
console.log(JSON.stringify(sortie));
