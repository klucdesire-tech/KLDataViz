# -*- coding: utf-8 -*-
"""Rendu des pages composées : styles et script ajoutés au modèle du tableau de bord, seulement pour ces pages.

Tout se recalcule dans le navigateur à chaque filtre, avec `agg`, `groupBy` et `pkey` du modèle. Une valeur manquante
s'affiche « — », jamais 0. Graphiques en HTML et SVG, sans dépendance, dessinés à la largeur réelle de leur carte :
le texte garde sa taille sur téléphone comme sur grand écran."""

CSS = """
#compo{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px;margin-bottom:20px}
.cp-outils{grid-column:1/-1;display:flex;align-items:center;gap:10px;font-size:13px;color:var(--muted)}
.cp-c{display:flex;flex-direction:column;background:var(--card);border:1px solid rgba(16,42,67,.06);border-radius:18px;padding:18px 20px;min-width:0;break-inside:avoid;
  box-shadow:0 1px 2px rgba(16,42,67,.04),0 10px 28px rgba(16,42,67,.06);transition:box-shadow .2s ease}
.cp-c:hover{box-shadow:0 1px 2px rgba(16,42,67,.05),0 14px 34px rgba(16,42,67,.10)}
.cp-h{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 10px;align-items:flex-start;margin-bottom:14px}
.cp-k{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#256ABF;margin-bottom:6px}
.cp-k::before{content:"";width:8px;height:8px;border-radius:50%;background:linear-gradient(135deg,#1D7A4C,#2A78D6)}
.cp-h h2{font-size:15.5px;font-weight:700;line-height:1.3;letter-spacing:-.01em}.cp-h small{display:block;color:var(--muted);font-size:12.5px;margin-top:3px}
.cp-corps{flex:1;display:flex;flex-direction:column;justify-content:center;min-width:0}
.cp-vide{color:var(--muted);font-size:13px;padding:22px 0;text-align:center}
.cp-note{color:var(--muted);font-size:12px;margin-top:8px}
.cp-svg{display:block;width:100%;height:auto}.cp-svg text{font-family:inherit}
.cp-bs{flex:1;display:flex;flex-direction:column;justify-content:space-evenly;gap:8px}
.cp-bl{display:flex;justify-content:space-between;gap:8px;font-size:13px}.cp-bl b{font-variant-numeric:tabular-nums;font-weight:600}
.cp-bt{position:relative;height:8px;background:#EEF3F8;border-radius:999px;margin-top:4px}.cp-gros .cp-bt{height:12px}.cp-bt i{display:block;height:100%;background:linear-gradient(90deg,#5598E7,#2A78D6);border-radius:999px;transition:width .5s ease}
.cp-bt u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink)}
.cp-t{overflow-x:auto}.cp-tr{display:grid;gap:2px;margin-bottom:2px}.cp-tl{position:sticky;left:0;z-index:1;background:var(--card);font-size:12.5px;font-weight:600;align-self:stretch;display:flex;align-items:center;min-width:0;overflow:hidden;white-space:nowrap}
.cp-th{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden}
.cp-cl{display:grid;place-items:center;min-height:32px;border-radius:5px;font-size:11px;font-weight:500;font-variant-numeric:tabular-nums;transition:transform .12s ease}.cp-cl:hover{transform:scale(1.06);box-shadow:0 2px 8px rgba(16,42,67,.18);position:relative;z-index:2}
.cp-leg{display:flex;align-items:center;gap:6px;margin-top:10px;font-size:12px;color:var(--muted)}.cp-leg span{width:24px;height:8px;border-radius:999px}
.cp-tm{position:relative;flex:1;min-height:280px;border-radius:12px;overflow:hidden}.cp-tm div{position:absolute;box-sizing:border-box;border:2px solid var(--card);border-radius:8px;padding:8px 10px;overflow:hidden;font-size:12px;line-height:1.25;transition:filter .15s}.cp-tm div:hover{filter:brightness(1.08)}.cp-tm b{display:block;font-weight:700;font-size:12.5px;margin-bottom:2px}
.cp-do{flex:1;display:grid;grid-template-columns:minmax(120px,170px) minmax(0,1fr);gap:18px;align-items:center}.cp-dl{list-style:none;font-size:13px}.cp-dl li{display:grid;grid-template-columns:10px minmax(0,1fr) auto;gap:6px;align-items:center;margin-bottom:5px}.cp-dl i{width:10px;height:10px;border-radius:50%}.cp-dl span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cp-dl b{font-variant-numeric:tabular-nums;font-weight:600}
.cp-cc{position:relative;touch-action:pan-y}.cp-bulle{position:absolute;top:6px;z-index:3;min-width:150px;max-width:240px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:12.5px;box-shadow:0 8px 24px rgba(16,42,67,.16);pointer-events:none}.cp-bt1{font-weight:600;color:var(--muted);margin-bottom:3px}.cp-bulle p{display:grid;grid-template-columns:10px auto minmax(0,1fr);gap:6px;align-items:center}.cp-bulle i{width:10px;height:10px;border-radius:2px}.cp-bulle b{font-weight:700;font-variant-numeric:tabular-nums}.cp-bulle span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cp-ju{flex:1;display:flex;flex-direction:column;justify-content:space-evenly;gap:8px}.cp-jl{display:grid;grid-template-columns:minmax(80px,1.1fr) minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:end;font-size:13px}.cp-jl>span{align-self:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cp-jl em{display:block;font-style:normal;font-weight:600;font-variant-numeric:tabular-nums;text-align:right}.cp-jh b{font-size:12px;font-weight:600;color:var(--muted);text-align:right}
.cp-wf{flex:1;display:flex;gap:6px;align-items:stretch;min-height:220px}.cp-wc{position:relative;flex:1;min-width:0;border-bottom:1px solid #C9D3DD}.cp-wc i{position:absolute;left:12%;right:12%;border-radius:5px}.cp-wc b{position:absolute;left:0;right:0;text-align:center;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}
.cp-wn{display:flex;gap:6px;margin-top:5px}.cp-wn span{flex:1;min-width:0;text-align:center;font-size:11.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cp-hi{display:flex;align-items:flex-end;gap:4px;height:170px;border-bottom:1px solid #C9D3DD}.cp-hi i{flex:1;background:linear-gradient(180deg,#5598E7,#2A78D6);border-radius:5px 5px 0 0;min-height:1px}.cp-hx{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:4px}
.cp-i-bt{display:flex;width:100%;justify-content:space-between;align-items:center;gap:12px;border:0;background:none;padding:0;text-align:left;cursor:pointer;color:inherit;font:inherit}.cp-i-bt b{display:block;font-size:15px;font-weight:700}.cp-i-bt small{display:block;color:var(--muted);font-size:12.5px;margin-top:2px}.cp-i-bt em{flex:none;font-style:normal;font-size:13px;font-weight:600;color:var(--navy)}
.cp-i-c{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin:12px 0 6px}.cp-i-c small{color:var(--muted);font-size:12.5px}
.cp-i-l{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(0,.8fr) minmax(0,.8fr) minmax(0,1fr);gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line);font-size:13.5px}.cp-i-h{border-top:0;padding:4px 0;font-size:12px;color:var(--muted)}.cp-i-l small{color:var(--muted);font-size:11.5px}.cp-i-n{text-align:right;font-variant-numeric:tabular-nums}.cp-i-n b{font-weight:600}.cp-i-bon{color:#0F6E56}.cp-i-mal{color:#A32D2D}
@media (max-width:900px){#compo .cp-c{grid-column:1/-1!important}}
@media (max-width:560px){.cp-i-l{grid-template-columns:minmax(0,1.5fr) minmax(0,.8fr) minmax(0,1fr)}.cp-i-r{display:none}}
@media (max-width:560px){.cp-jl{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.cp-jl>span{grid-column:1/-1}.cp-jh>span{display:none}}
"""

HTML = '<section id="compo" aria-label="Tableau de bord composé selon vos données"></section>\n'

JS = r"""
// ---- Pages composées : chaque carte vient du plan calculé côté serveur (META.composition)
var CP_TEINTES=['#86B6EF','#5598E7','#256ABF','#184F95','#0D366B'],CP_SERIES=['#2A78D6','#EB6834','#1BAF7A','#EDA100','#E87BA4','#008300'];
var CP_AGR={somme:'Total',moyenne:'Moyenne',dernier:'Dernière valeur'},CP_HAUSSE='#2A78D6',CP_BAISSE='#EB6834',CP_PRINC='#2A78D6',CP_TOTAL='#184F95',CP_NG=0,CP_L=1200,CP_R=[],CP_CC=[],CP_JUMELLES=10;
function cpVal(v){return v==null||isNaN(v)?'—':v&&Math.abs(v)<.01?Number(v).toLocaleString('fr-FR',{maximumSignificantDigits:2}):fmt(v)}
function cpEtiquettes(ts,pas){var a=ts.map(cpVal),u={},f={};a.forEach(function(x,i){u[x]=1;if(ts[i])f[x.replace(/[^a-zA-Z]/g,'')]=1});if(Object.keys(u).length===ts.length&&Object.keys(f).length<2)return a;var d=Math.min(20,Math.max(0,Math.ceil(-Math.log(pas)/Math.LN10)+1));return ts.map(function(t){return Number(t.toPrecision(12)).toLocaleString('fr-FR',{maximumFractionDigits:d})})}
function cpMarge(lab,min){return Math.max(min,8+7*Math.max.apply(null,lab.map(function(x){return x.length})))}
function cpPct(v){return v.toLocaleString('fr-FR',{maximumFractionDigits:1})+' %'}
function cpVide(t){return '<p class="cp-vide">'+esc(t||'Pas assez de données pour ce filtre.')+'</p>'}
function cpCoupe(s,n){s=String(s);return s.length>n?s.slice(0,Math.max(1,n-1))+'…':s}
function cpMot(s){var L=META.composition.libelles||{},t=short(s);if(L[s])return L[s];if(/^[A-Z0-9_]+$/.test(String(s)))return String(s);return /^[A-ZÀ-Ý][a-zà-ÿ]/.test(t)?t.charAt(0).toLowerCase()+t.slice(1):t}
function cpTete(s){var t=cpMot(s);return t.charAt(0).toUpperCase()+t.slice(1)}
function cpDe(s){var t=cpMot(s);return (/^[aeiouyàâéèêîôûAEIOUY]|^h/.test(t)?"d'":'de ')+t}
function cpPlafond(v){if(!(v>0))return 1;var p=Math.pow(10,Math.floor(Math.log(v)/Math.LN10)),n=v/p,s=[1,1.2,1.5,2,2.5,3,4,5,6,8,10];for(var i=0;i<s.length;i++)if(n<=s[i]*1.0000001)return s[i]*p;return 10*p}
function cpBas(mi){return mi<0?-cpPlafond(-mi):0}
function cpHaut(ma,bas){return ma>0?cpPlafond(ma):bas<0?0:1}
function cpPlein(){return window.innerWidth<=900}
function cpW(c){return Math.max(240,Math.round(cpPlein()?CP_L-34:(CP_L-176)/12*c.span+(c.span-1)*16-34))}
function cpNb(rows){return META.cube?rows.reduce(function(s,r){return s+r.__n},0):rows.length}
var CP_RUBRIQUES={courbe:'Évolution',classement:'Classement',anneau:'Répartition',treemap:'Répartition',chaleur:'Carte de chaleur',pente:'Progression',passage:'Variation',cible:'Objectif',bulles:'Relation',comparee:'Comparaison',histogramme:'Distribution'};
function cpCarte(c,titre,sous,corps){var m=cpMessage(c,CP_R),s=m&&m.indexOf(titre)?[titre,sous].filter(Boolean).join(' · '):sous,k=CP_RUBRIQUES[c.type];return '<article class="cp-c" style="grid-column:span '+c.span+'"><div class="cp-h"><div>'+(k?'<span class="cp-k">'+esc(k)+'</span>':'')+'<h2>'+esc(m||titre)+'</h2>'+(s?'<small>'+esc(s)+'</small>':'')+'</div></div><div class="cp-corps">'+corps+'</div></article>'}
function cpSousTitre(c){return CP_AGR[c.agregation]||''}
function cpSerie(rows,c){var g=groupBy(rows,function(r){return pkey(r.__p)});return Object.keys(g).sort().map(function(k){return [k,agg(g[k],c.mesure,c.agregation)]}).filter(function(e){return e[1]!=null})}
function cpPoints(s,W,H,G,B,T,max,bas){return s.map(function(e,i){return [s.length>1?G+i*((W-G-8)/(s.length-1)):G,H-B-(e[1]-bas)/(max-bas)*(H-B-T)]})}
function cpTrace(p){return 'M'+p.map(function(q){return q[0].toFixed(1)+' '+q[1].toFixed(1)}).join('L')}
// Courbe lissée « monotone » (Fritsch-Carlson) : elle passe exactement par chaque valeur et ne dépasse jamais, entre
// deux points, ni l'un ni l'autre. Pas de pic ni de creux inventé entre deux trimestres.
function cpLisse(p){var n=p.length;if(n<3)return cpTrace(p);var d=[],m=[],i;for(i=0;i<n-1;i++)d.push((p[i+1][1]-p[i][1])/((p[i+1][0]-p[i][0])||1));
  m[0]=d[0];m[n-1]=d[n-2];for(i=1;i<n-1;i++)m[i]=d[i-1]*d[i]<=0?0:(d[i-1]+d[i])/2;
  for(i=0;i<n-1;i++){if(d[i]===0){m[i]=0;m[i+1]=0;continue}var a=m[i]/d[i],b=m[i+1]/d[i],q=a*a+b*b;if(q>9){var t=3/Math.sqrt(q);m[i]=t*a*d[i];m[i+1]=t*b*d[i]}}
  var o='M'+p[0][0].toFixed(1)+' '+p[0][1].toFixed(1);for(i=0;i<n-1;i++){var h=(p[i+1][0]-p[i][0])/3;
    o+='C'+(p[i][0]+h).toFixed(1)+' '+(p[i][1]+m[i]*h).toFixed(1)+' '+(p[i+1][0]-h).toFixed(1)+' '+(p[i+1][1]-m[i+1]*h).toFixed(1)+' '+p[i+1][0].toFixed(1)+' '+p[i+1][1].toFixed(1)}return o}
function cpCourbe(rows,c){var s=cpSerie(rows,c);if(s.length<2)return cpVide();var W=cpW(c),H=Math.max(200,Math.min(260,Math.round(W*.42))),B=26,T=16,v=s.map(function(e){return e[1]}),n=s.length;
  var bas=cpBas(Math.min.apply(null,v)),max=cpHaut(Math.max.apply(null,v),bas),ts=bas<0?(max>0?[bas,0,max]:[bas,bas/2,0]):[0,max/2,max],lab=cpEtiquettes(ts,(max-bas)/2),G=cpMarge(lab,52),p=cpPoints(s,W,H,G,B,T,max,bas),o='<svg class="cp-svg" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc('Évolution '+cpDe(c.mesure))+'">',y0=H-B-(0-bas)/(max-bas)*(H-B-T);
  ts.forEach(function(t,a){var y=H-B-(t-bas)/(max-bas)*(H-B-T);o+='<path d="M'+G+' '+y.toFixed(1)+'H'+(W-8)+'" stroke="'+(t?'#E8EEF4':'#C9D3DD')+'"'+(t?' stroke-dasharray="3 4"':'')+'/><text x="'+(G-6)+'" y="'+(y+4).toFixed(1)+'" text-anchor="end" font-size="12" fill="#52667A">'+esc(lab[a])+'</text>'});
  var gid='cpg'+(++CP_NG);o+='<defs><linearGradient id="'+gid+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+CP_PRINC+'" stop-opacity=".28"/><stop offset="1" stop-color="'+CP_PRINC+'" stop-opacity="0"/></linearGradient></defs>';
  var m=cpComplet(rows,s).length,der=m-1,fin=n-1,w=v.slice(0,m),haut=w.indexOf(Math.max.apply(null,w)),tr=cpLisse(p.slice(0,m));
  o+='<path d="'+tr+'L'+p[der][0].toFixed(1)+' '+y0.toFixed(1)+'L'+p[0][0].toFixed(1)+' '+y0.toFixed(1)+'Z" fill="url(#'+gid+')"/><path d="'+tr+'" fill="none" stroke="'+CP_PRINC+'" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round"/>';
  if(m<n)o+='<path d="M'+p[der][0].toFixed(1)+' '+p[der][1].toFixed(1)+'L'+p[fin][0].toFixed(1)+' '+p[fin][1].toFixed(1)+'" fill="none" stroke="'+CP_PRINC+'" stroke-width="2" stroke-dasharray="4 4"/>';
  var repere=Math.abs(p[haut][0]-p[der][0])<80?[der]:[haut,der];
  o+='<circle cx="'+p[der][0].toFixed(1)+'" cy="'+p[der][1].toFixed(1)+'" r="11" fill="'+CP_PRINC+'" fill-opacity=".14"/>';
  s.forEach(function(e,i){var vu=repere.indexOf(i)>=0,ouvert=m<n&&i===fin;o+='<circle cx="'+p[i][0].toFixed(1)+'" cy="'+p[i][1].toFixed(1)+'" r="'+(vu?5:ouvert?4:9)+'" fill="'+(vu?CP_PRINC:ouvert?'#fff':'transparent')+'"'+(vu?' stroke="#fff" stroke-width="2"':ouvert?' stroke="'+CP_PRINC+'" stroke-width="2"':'')+'><title>'+esc(plabel(e[0])+' : '+cpVal(e[1]))+'</title></circle>'});
  repere.forEach(function(i){o+='<text x="'+p[i][0].toFixed(1)+'" y="'+Math.max(12,p[i][1]-9).toFixed(1)+'" text-anchor="'+(p[i][0]>W-70?'end':'middle')+'" font-size="13" font-weight="700" fill="#102A43">'+esc(cpVal(s[i][1]))+'</text>'});
  var pas=Math.max(1,Math.ceil(n/Math.max(2,Math.floor(W/90))));
  for(var i=0;i<fin;i+=pas)if(p[fin][0]-p[i][0]>=70)o+='<text x="'+p[i][0].toFixed(1)+'" y="'+(H-7)+'" text-anchor="'+(i?'middle':'start')+'" font-size="12" fill="#52667A">'+esc(plabel(s[i][0]))+'</text>';
  return o+'<text x="'+(W-8)+'" y="'+(H-7)+'" text-anchor="end" font-size="12" fill="#52667A">'+esc(plabel(s[fin][0]))+'</text></svg>'+(m<n?'<p class="cp-note">'+esc(plabel(s[fin][0]).replace(/\.$/,''))+' est incomplet : en pointillé, hors comparaison.</p>':'')}
function cpGroupes(rows,c,top){var g=groupBy(rows,c.dimension);return Object.keys(g).map(function(k){return [k,agg(g[k],c.mesure,c.agregation)]}).filter(function(e){return e[1]!=null}).sort(function(a,b){return b[1]-a[1]}).slice(0,top||12)}
function cpListe(h,n){return '<div class="cp-bs'+(n<=6?' cp-gros':'')+'">'+h+'</div>'}
function cpBarres(e,total){var v=e.map(function(x){return x[1]}),lo=Math.min.apply(null,v.concat(0)),et=(Math.max.apply(null,v.concat(0))-lo)||1;return cpListe(e.map(function(x){return '<div><div class="cp-bl"><span>'+esc(label(x[0]))+'</span><b>'+esc(cpVal(x[1]))+(total?' · '+cpPct(100*x[1]/total):'')+'</b></div><div class="cp-bt"><i style="margin-left:'+(100*(Math.min(x[1],0)-lo)/et).toFixed(1)+'%;width:'+(100*Math.abs(x[1])/et).toFixed(1)+'%'+(x[1]<0?';background:'+CP_BAISSE:'')+'"></i>'+(lo<0?'<u style="left:'+(100*-lo/et).toFixed(1)+'%"></u>':'')+'</div></div>'}).join(''),e.length)}
function cpClassement(rows,c){var e=cpGroupes(rows,c);if(!e.length)return cpVide();var n=Object.keys(groupBy(rows,c.dimension)).length;return cpBarres(e)+(n>e.length?'<p class="cp-note">Les '+e.length+' premiers sur '+n+'.</p>':'')}
function cpAnneau(rows,c){var e=cpGroupes(rows,c,5).filter(function(x){return x[1]>0}),t=e.reduce(function(s,x){return s+x[1]},0);if(e.length<2||!t)return cpVide();if(e.length<3||(e[0][1]-e[e.length-1][1])/t<.05)return cpBarres(e,t);
  var R=15.9155,cum=0,coul=cpCouleurs(c.dimension),o='<div class="cp-do"><svg class="cp-svg" viewBox="0 0 42 42" role="img" aria-label="'+esc('Répartition '+cpDe(c.mesure))+'">';
  e.forEach(function(x){var p=100*x[1]/t;o+='<circle cx="21" cy="21" r="'+R+'" fill="none" stroke="'+coul[x[0]]+'" stroke-width="6" stroke-dasharray="'+Math.max(p-.8,.2).toFixed(2)+' '+(100-Math.max(p-.8,.2)).toFixed(2)+'" stroke-dashoffset="'+(25-cum).toFixed(2)+'"><title>'+esc(label(x[0])+' : '+cpVal(x[1]))+'</title></circle>';cum+=p});
  var tt=cpVal(t);o+='<text x="21" y="21.2" text-anchor="middle" font-size="'+(tt.length>8?3.4:tt.length>6?4.2:5)+'" font-weight="700" fill="#102A43">'+esc(tt)+'</text><text x="21" y="26.2" text-anchor="middle" font-size="2.9" fill="#52667A">'+esc(CP_AGR.somme)+'</text>';
  return o+'</svg><ul class="cp-dl">'+e.map(function(x){return '<li><i style="background:'+coul[x[0]]+'"></i><span>'+esc(label(x[0]))+'</span><b>'+cpPct(100*x[1]/t)+'</b></li>'}).join('')+'</ul></div>'}
// Part de chaque groupe (sexe…) dans chaque indicateur : un groupe de bandes verticales par indicateur, chacun = 100 %.
// Les ordres de grandeur diffèrent (consultants, dépistés, positifs) : on compare des parts, les vraies valeurs au survol.
// Texte coupé en lignes de `n` caractères au plus (mots entiers), `max` lignes, « … » si le reste ne tient pas
function cpLignes(t,n,max){var mots=String(t).split(/\s+/),L=[],cur='';mots.forEach(function(w){if(!cur)cur=w;else if((cur+' '+w).length<=n)cur+=' '+w;else{L.push(cur);cur=w}});if(cur)L.push(cur);
  if(L.length>max){L=L.slice(0,max);L[max-1]=L[max-1].replace(/\s*\S*$/,'')+'…'}return L}
function cpColonnes(rows,c){var M=[c.mesure].concat(c.ensemble||[]),g=groupBy(rows,c.dimension),G=Object.keys(g).sort(),coul=cpCouleurs(c.dimension),court=cpCourts(M),B=[],sans=[],nv=[];
  M.forEach(function(m){var v=G.map(function(k){var x=agg(g[k],m,'somme');return x!=null&&x>0?x:null}),t=v.reduce(function(s,x){return s+(x||0)},0),tout=agg(rows,m,'somme');
    if(!(t>0)){sans.push(court[m]);return}if(tout!=null&&tout>t*1.0005)nv.push(court[m]+' ('+cpPct(100*(tout-t)/tout)+')');B.push({m:m,v:v,t:t})});
  if(!B.length||G.length<2)return cpVide();
  var W=cpW(c),H=Math.max(230,Math.min(290,Math.round(W*.66))),T=24,Bb=78,Gm=40,n=B.length,k=G.length,gw=(W-Gm-6)/n,bw=Math.max(8,Math.min(40,(gw-18-4*(k-1))/k)),Y=function(p){return H-Bb-p/100*(H-Bb-T)};
  var o='<svg class="cp-svg" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc('Part de chaque '+cpMot(c.dimension)+', indicateur par indicateur')+'">';
  [0,50,100].forEach(function(t){o+='<path d="M'+Gm+' '+Y(t).toFixed(1)+'H'+(W-6)+'" stroke="'+(t?'#E8EEF4':'#C9D3DD')+'"'+(t?' stroke-dasharray="3 4"':'')+'/><text x="'+(Gm-6)+'" y="'+(Y(t)+4).toFixed(1)+'" text-anchor="end" font-size="12" fill="#52667A">'+t+' %</text>'});
  B.forEach(function(b,i){var x0=Gm+i*gw+(gw-(bw*k+4*(k-1)))/2,cx=Gm+i*gw+gw/2;
    G.forEach(function(d,j){var v=b.v[j];if(v==null)return;var p=100*v/b.t,x=x0+j*(bw+4),y=Y(p);
      o+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+Math.max(1,H-Bb-y).toFixed(1)+'" rx="4" fill="'+coul[d]+'"><title>'+esc(court[b.m]+' · '+label(d)+' : '+cpVal(v)+' ('+cpPct(p)+')')+'</title></rect>'
        +'<text x="'+(x+bw/2).toFixed(1)+'" y="'+Math.max(11,y-5).toFixed(1)+'" text-anchor="middle" font-size="'+(bw<26?10.5:12)+'" font-weight="700" fill="#102A43">'+esc(cpPct(p))+'</text>'});
    var lignes=cpLignes(court[b.m],Math.max(8,Math.floor((gw-6)/6.6)),3);
    o+='<text x="'+cx.toFixed(1)+'" y="'+(H-Bb+16)+'" text-anchor="middle" font-size="11.5" font-weight="600" fill="#102A43"><title>'+esc(label(b.m))+'</title>'+lignes.map(function(l,i){return '<tspan x="'+cx.toFixed(1)+'" dy="'+(i?14:0)+'">'+esc(l)+'</tspan>'}).join('')+'</text>'
      +'<text x="'+cx.toFixed(1)+'" y="'+(H-Bb+16+14*lignes.length+2)+'" text-anchor="middle" font-size="11" fill="#52667A">'+esc('total '+cpVal(b.t))+'</text>'});
  var leg='<ul class="cp-series" style="list-style:none;display:flex;flex:none;flex-wrap:wrap;gap:6px 18px;margin:8px 0 0;padding:0;font-size:12.5px;line-height:1.35;color:#102A43">'+G.map(function(d){return '<li style="display:flex;flex:0 1 auto;align-items:baseline;gap:6px"><i style="flex:none;width:10px;height:10px;border-radius:2px;background:'+coul[d]+'"></i><span>'+esc(label(d))+'</span></li>'}).join('')+'</ul>';
  var note='Chaque groupe de bandes fait 100 % : la part de chaque '+cpMot(c.dimension)+' dans le total de l’indicateur. Au survol, les vraies valeurs.';
  if(nv.length)note+=' Non ventilé par '+cpMot(c.dimension)+', hors des parts : '+nv.join(', ')+'.';if(sans.length)note+=' Pas de ventilation par '+cpMot(c.dimension)+' pour : '+sans.join(', ')+'.';
  return o+'</svg>'+leg+'<p class="cp-note">'+esc(note)+'</p>'}
function cpDecoupe(a,x,y,w,h,o){if(a.length===1){o.push({k:a[0][0],v:a[0][1],x:x,y:y,w:w,h:h});return}var t=a.reduce(function(s,e){return s+e[1]},0),c=0,n=0;
  while(n<a.length-1&&c+a[n][1]/2<t/2){c+=a[n][1];n++}n=Math.max(1,n);var A=a.slice(0,n),B=a.slice(n),f=A.reduce(function(s,e){return s+e[1]},0)/t;
  if(w*1.7>=h){cpDecoupe(A,x,y,w*f,h,o);cpDecoupe(B,x+w*f,y,w*(1-f),h,o)}else{cpDecoupe(A,x,y,w,h*f,o);cpDecoupe(B,x,y+h*f,w,h*(1-f),o)}}
function cpTreemap(rows,c){var e=cpGroupes(rows,c,40).filter(function(x){return x[1]>0}),t=e.reduce(function(s,x){return s+x[1]},0);if(e.length<2||!t)return cpVide();var o=[],W=cpW(c),caches=[];cpDecoupe(e,0,0,100,100,o);
  var h='<div class="cp-tm" role="img" aria-label="'+esc('Répartition '+cpDe(c.mesure)+' par '+cpMot(c.dimension))+'">'+o.map(function(b){var p=100*b.v/t,lisible=b.w/100*W>=72&&b.h/100*280>=52;if(!lisible)caches.push(label(b.k)+' ('+cpPct(p)+')');
    return '<div title="'+esc(label(b.k)+' : '+cpVal(b.v)+', '+cpPct(p))+'" style="left:'+b.x.toFixed(2)+'%;top:'+b.y.toFixed(2)+'%;width:'+b.w.toFixed(2)+'%;height:'+b.h.toFixed(2)+'%;background:#9EC5F4;color:#102A43">'+(lisible?'<b>'+esc(label(b.k))+'</b>'+cpPct(p):'')+'</div>'}).join('')+'</div>';
  return h+(caches.length?'<p class="cp-note">Non nommés : '+esc(caches.slice(0,3).join(', '))+(caches.length>3?' et '+(caches.length-3)+' autres':'')+'.</p>':'')}
function cpMatrice(rows,c){var g=groupBy(rows,function(r){return pkey(r.__p)}),P=Object.keys(g).sort(),D=Object.keys(groupBy(rows,c.dimension)).sort();if(P.length<2||D.length<2)return null;
  var mx=null,mi=null,M={};D.forEach(function(d){var gd=groupBy(rows.filter(function(r){return String(r[c.dimension])===d}),function(r){return pkey(r.__p)});P.forEach(function(p){var v=gd[p]?agg(gd[p],c.mesure,c.agregation):null;M[d+'|'+p]=v;if(v!=null){mx=mx==null?v:Math.max(mx,v);mi=mi==null?v:Math.min(mi,v)}})});
  return {P:P,D:D,M:M,mi:mi,mx:mx}}
function cpChaleur(rows,c){var X=cpMatrice(rows,c);if(!X)return cpVide('Choisissez plusieurs périodes et plusieurs groupes pour lire cette carte.');var P=X.P,D=X.D,M=X.M,mi=X.mi,mx=X.mx;
  if(mx==null)return cpVide();var W=cpW(c),NOM=W<500?96:130,place=(W-NOM-2*P.length)/P.length,chiffres=place>=32||cpPlein(),mc=chiffres?32:14,cols=NOM+'px repeat('+P.length+',minmax('+mc+'px,1fr))',lg='min-width:'+(NOM+P.length*(mc+2))+'px;grid-template-columns:'+cols,et=mx-mi||1,k=Math.ceil(58/Math.max(place,mc)),o='<div class="cp-t"><div class="cp-tr" style="'+lg+'"><span></span>';
  for(var i=0;i<P.length;i+=k)o+='<span class="cp-th" style="grid-column:span '+Math.min(k,P.length-i)+'" title="'+esc(plabel(P[i]))+'">'+esc(plabel(P[i]))+'</span>';o+='</div>';
  D.forEach(function(d){o+='<div class="cp-tr" style="'+lg+'"><span class="cp-tl" title="'+esc(label(d))+'">'+esc(label(d))+'</span>'+P.map(function(p){var v=M[d+'|'+p],t=esc(label(d)+', '+plabel(p)+' : '+cpVal(v));if(v==null)return '<span class="cp-cl" title="'+t+'" aria-label="'+t+'" style="background:#F3F6F9;color:#52667A">'+(chiffres?'—':'')+'</span>';var n=Math.min(4,Math.floor(5*(v-mi)/et));return '<span class="cp-cl" title="'+t+'" aria-label="'+t+'" style="background:'+CP_TEINTES[n]+';color:'+(n>=2?'#fff':'#102A43')+'">'+(chiffres?esc(fmt(v)):'')+'</span>'}).join('')+'</div>'});
  return o+'</div><div class="cp-leg">'+esc(fmt(mi))+CP_TEINTES.map(function(t){return '<span style="background:'+t+'"></span>'}).join('')+esc(fmt(mx))+'</div>'+(chiffres?'':'<p class="cp-note">Survolez une case pour lire sa valeur.</p>')}
function cpUnSeulMois(rows){var v={};rows.forEach(function(r){if(r.__p)v[r.__p.slice(5,7)]=1});return Object.keys(v).length===1}
// Mois déclarés les deux années, recalculés sur les lignes affichées : avec un filtre, ou si un groupe n'a pas encore
// déclaré le dernier mois, comparer 5 mois d'une année à 4 de l'autre inventerait une baisse.
function cpPeriodes(rows,c){var parAn={};rows.forEach(function(r){if(r.__p){var y=r.__p.slice(0,4);(parAn[y]=parAn[y]||{})[r.__p.slice(5,7)]=1}});var y=Object.keys(parAn).sort();if(y.length<2)return null;
  var m=Object.keys(parAn[y[y.length-1]]).filter(function(k){return parAn[y[y.length-2]][k]}).sort();if(!m.length)return null;
  var n=y.length,de=function(an){return rows.filter(function(r){return r.__p&&r.__p.slice(0,4)===an&&m.indexOf(r.__p.slice(5,7))>=0})};return {a:y[n-2],b:y[n-1],A:de(y[n-2]),B:de(y[n-1]),mois:m.length,communs:m,annuel:cpUnSeulMois(rows)}}
function cpMoisTxt(p){return p.mois===12||p.annuel?p.a+' et '+p.b:'Même période, '+p.a+' et '+p.b+' ('+p.mois+' mois)'}
// À périmètre constant : un groupe présent les deux années n'est comparé que sur les mois qu'il a déclarés les deux fois
// (un mois manquant n'est pas une baisse). Un groupe présent une seule année (site ouvert ou fermé) compte tel quel.
function cpEcarts(p,c){var g0=groupBy(p.A,c.dimension),g1=groupBy(p.B,c.dimension),u={};Object.keys(g0).concat(Object.keys(g1)).forEach(function(k){u[k]=1});
  return Object.keys(u).map(function(k){var A=g0[k],B=g1[k];if(A&&B){var ma={},mb={};A.forEach(function(r){ma[r.__p.slice(5,7)]=1});B.forEach(function(r){mb[r.__p.slice(5,7)]=1});
    var com=function(r){var x=r.__p.slice(5,7);return ma[x]&&mb[x]};A=A.filter(com);B=B.filter(com);if(!A.length){A=null;B=null}}
    return [k,A?agg(A,c.mesure,c.agregation):null,B?agg(B,c.mesure,c.agregation):null]})}
function cpEspacer(a,cle,ecart){var t=a.slice().sort(function(x,y){return x[cle]-y[cle]});for(var i=1;i<t.length;i++)if(t[i][cle]-t[i-1][cle]<ecart)t[i][cle]=t[i-1][cle]+ecart}
function cpPente(rows,c){var p=cpPeriodes(rows,c);if(!p)return cpVide('Il faut deux années comparables.');var e=cpEcarts(p,c).filter(function(x){return x[1]!=null&&x[2]!=null});if(e.length<2)return cpVide();var W=cpW(c),H=Math.max(220,70+e.length*24),X1=54,X2=Math.round(W*.5),all=e.reduce(function(s,x){return s.concat([x[1],x[2]])},[]),mi=Math.min.apply(null,all),ma=Math.max.apply(null,all),et=ma-mi||1,uniq=all.filter(function(v,i){return all.indexOf(v)===i}),lab=cpEtiquettes(uniq,et/10),txt=function(v){return lab[uniq.indexOf(v)]};
  var y=function(v){return H-20-(v-mi)/et*(H-52)},L=e.map(function(x){return {nom:x[0],a:x[1],b:x[2],ya:y(x[1]),yb:y(x[2]),la:y(x[1])+4,lb:y(x[2])+4}});cpEspacer(L,'la',16);cpEspacer(L,'lb',16);
  var o='<svg class="cp-svg" viewBox="0 0 '+W+' '+(H+16)+'" role="img" aria-label="'+esc('Évolution '+cpDe(c.mesure)+' entre '+p.a+' et '+p.b)+'"><text x="'+X1+'" y="14" text-anchor="middle" font-size="12.5" font-weight="600" fill="#52667A">'+esc(p.a)+'</text><text x="'+X2+'" y="14" text-anchor="middle" font-size="12.5" font-weight="600" fill="#52667A">'+esc(p.b)+'</text>';
  L.forEach(function(l){var d=l.b-l.a,co=d>0?CP_HAUSSE:d<0?CP_BAISSE:'#8497A8',val=txt(l.b),place=Math.floor((W-X2-12)/7.2)-val.length-1;o+='<path d="M'+X1+' '+l.ya.toFixed(1)+'L'+X2+' '+l.yb.toFixed(1)+'" stroke="'+co+'" stroke-width="2" fill="none" stroke-linecap="round"><title>'+esc(label(l.nom)+' : '+txt(l.a)+' puis '+val)+'</title></path><circle cx="'+X1+'" cy="'+l.ya.toFixed(1)+'" r="3.5" fill="'+co+'" stroke="#fff" stroke-width="1.5"/><circle cx="'+X2+'" cy="'+l.yb.toFixed(1)+'" r="4.5" fill="'+co+'" stroke="#fff" stroke-width="2"/><text x="'+(X1-8)+'" y="'+l.la.toFixed(1)+'" text-anchor="end" font-size="12" fill="#102A43">'+esc(txt(l.a))+'</text><text x="'+(X2+8)+'" y="'+l.lb.toFixed(1)+'" font-size="12" fill="#102A43"><tspan font-weight="600">'+esc(val)+'</tspan> '+esc(cpCoupe(label(l.nom),place))+'</text>'});
  return o+'</svg><p class="cp-note">'+esc(cpMoisTxt(p))+'. Bleu : hausse. Orange : baisse.</p>'}
function cpPassage(rows,c){var p=cpPeriodes(rows,c);if(!p)return cpVide('Il faut deux années comparables.');var e=cpEcarts(p,c).filter(function(x){return x[1]!=null||x[2]!=null}).map(function(x){var a=x[1]||0,b=x[2]||0;return [x[0],b-a,a,b]}).sort(function(a,b){return b[1]-a[1]});if(e.length<2)return cpVide();
  var t0=e.reduce(function(s,x){return s+x[2]},0),t1=e.reduce(function(s,x){return s+x[3]},0),cour=t0,et=[{n:p.a,v:t0,d:0,h:t0,k:'t'}],pts=[t0,t1];e.forEach(function(x){et.push({n:label(x[0]),v:x[1],d:Math.min(cour,cour+x[1]),h:Math.abs(x[1]),k:x[1]>=0?'p':'n'});cour+=x[1];pts.push(cour)});et.push({n:p.b,v:t1,d:0,h:t1,k:'t'});
  var mi=Math.min.apply(null,pts),ma=Math.max.apply(null,pts),plage=ma-mi||1,base=Math.max(0,mi-plage*.8),coupe=base>0,ech=function(v){return (v-base)/(ma-base||1)*88};
  var o='<div class="cp-wf">'+et.map(function(s){var d=s.k==='t'?0:ech(s.d),h=s.k==='t'?ech(s.h):Math.max(.8,ech(s.d+s.h)-ech(s.d)),co=s.k==='t'?CP_TOTAL:s.k==='p'?CP_HAUSSE:CP_BAISSE,tx=s.k==='t'?cpVal(s.v):(s.v>0?'+':s.v<0?'−':'')+cpVal(Math.abs(s.v));return '<div class="cp-wc" title="'+esc(s.n+' : '+tx)+'"><i style="bottom:'+d.toFixed(1)+'%;height:'+h.toFixed(1)+'%;background:'+co+'"></i><b style="bottom:'+(d+h+1.5).toFixed(1)+'%">'+esc(tx)+'</b></div>'}).join('')+'</div><div class="cp-wn">'+et.map(function(s){return '<span title="'+esc(s.n)+'">'+esc(s.n)+'</span>'}).join('')+'</div>';
  return o+'<p class="cp-note">'+esc(cpMoisTxt(p))+(coupe?'. Axe coupé à '+fmt(base)+' pour lire les écarts.':'.')+'</p>'}
function cpCibleLignes(rows,c){var R=rows.filter(function(r){return num(r[c.mesure])!=null&&num(r[c.objectif])!=null}),d=c.dimension,g=d?groupBy(R,d):{'Ensemble':R},e=Object.keys(g).map(function(k){return [k,agg(g[k],c.mesure,c.agregation),agg(g[k],c.objectif,c.agregation)]}).filter(function(x){return x[1]!=null&&x[2]!=null&&x[2]>0}).sort(function(a,b){return b[1]/b[2]-a[1]/a[2]});return {R:R,e:e}}
function cpCible(rows,c){var L=cpCibleLignes(rows,c),e=L.e,d=c.dimension,P=cpNb(rows.filter(function(r){return num(r[c.mesure])!=null}))-cpNb(L.R);if(!e.length)return cpVide();
  var sans=d?Object.keys(groupBy(rows,d)).filter(function(k){return !e.some(function(x){return x[0]===k})}):[],reserve=(P?' Calcul limité aux lignes qui ont un objectif : '+P+' sans objectif écartée'+(P>1?'s':'')+'.':'')+(sans.length?' Sans objectif : '+sans.slice(0,3).map(label).join(', ')+(sans.length>3?' et '+(sans.length-3)+' autres':'')+'.':'');
  var max=Math.max.apply(null,e.map(function(x){return Math.max(x[1],x[2])}));return cpListe(e.map(function(x){return '<div title="'+esc(label(x[0])+' : '+cpVal(x[1])+', objectif '+cpVal(x[2]))+'"><div class="cp-bl"><span>'+esc(label(x[0]))+'</span><b>'+cpPct(100*x[1]/x[2])+'</b></div><div class="cp-bt"><i style="width:'+(100*Math.max(0,x[1])/max).toFixed(1)+'%"></i><u style="left:calc('+(100*x[2]/max).toFixed(1)+'% - 2px)"></u></div></div>'}).join(''),e.length)+'<p class="cp-note">Barre : réalisé. Trait : objectif. Pourcentage : réalisé par rapport à l\'objectif.'+esc(reserve)+'</p>'}
function cpBornes(lo,hi){var pas=cpPlafond((hi-lo||Math.abs(hi)||1)/3),bas=Math.floor(lo/pas+1e-9)*pas,haut=Math.ceil(hi/pas-1e-9)*pas;if(haut<=bas)haut=bas+pas;if(lo>=0&&bas<0)bas=0;var cr=Math.max(1,Math.ceil((haut-bas)/pas/4)),ts=[];for(var u=bas;u<=haut+pas*1e-6;u+=pas*cr)ts.push(u);return {bas:bas,haut:haut,pas:pas,ts:ts}}
function cpJumelles(e,c){var barre=function(i){var v=e.map(function(x){return x[i]}),lo=Math.min.apply(null,v.concat(0)),et=(Math.max.apply(null,v.concat(0))-lo)||1;return {s:function(x){return 'margin-left:'+(100*(Math.min(x,0)-lo)/et).toFixed(1)+'%;width:'+(100*Math.abs(x)/et).toFixed(1)+'%'},z:lo<0?'<u style="left:'+(100*-lo/et).toFixed(1)+'%"></u>':''}},A=barre(1),B=barre(2);
  return '<div class="cp-ju"><div class="cp-jl cp-jh"><span></span><b>'+esc(cpTete(c.mesure))+'</b><b>'+esc(cpTete(c.mesure_y))+'</b></div>'+e.slice().sort(function(a,b){return b[1]-a[1]}).map(function(x){return '<div class="cp-jl"><span title="'+esc(label(x[0]))+'">'+esc(label(x[0]))+'</span><div><em>'+esc(cpVal(x[1]))+'</em><div class="cp-bt"><i style="'+A.s(x[1])+'"></i>'+A.z+'</div></div><div><em>'+esc(cpVal(x[2]))+'</em><div class="cp-bt"><i style="'+B.s(x[2])+';background:'+CP_SERIES[1]+'"></i>'+B.z+'</div></div></div>'}).join('')+'</div>'}
function cpBulles(rows,c){var g=groupBy(rows,c.dimension),e=Object.keys(g).map(function(k){return [k,agg(g[k],c.mesure,c.agregation),agg(g[k],c.mesure_y,c.agregation_y)]}).filter(function(x){return x[1]!=null&&x[2]!=null});if(e.length<2)return cpVide();if(e.length<=CP_JUMELLES)return cpJumelles(e,c);if(e.every(function(x){return x[1]===e[0][1]&&x[2]===e[0][2]}))return cpVide('Tous les groupes ont les mêmes valeurs.');
  var W=cpW(c),H=Math.max(240,Math.min(340,Math.round(W*.62))),B=36,xs=e.map(function(x){return x[1]}),ys=e.map(function(x){return x[2]}),BX=cpBornes(Math.min.apply(null,xs),Math.max.apply(null,xs)),BY=cpBornes(Math.min.apply(null,ys),Math.max.apply(null,ys)),lx=cpEtiquettes(BX.ts,BX.pas),ly=cpEtiquettes(BY.ts,BY.pas),G=cpMarge(ly,56),X=function(v){return G+(v-BX.bas)/(BX.haut-BX.bas||1)*(W-G-16)},Y=function(v){return H-B-(v-BY.bas)/(BY.haut-BY.bas||1)*(H-B-14)};
  var o='<svg class="cp-svg" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(cpTete(c.mesure)+' et '+cpMot(c.mesure_y)+' par '+cpMot(c.dimension))+'">';
  BY.ts.forEach(function(t,i){var y=Y(t);o+='<path d="M'+G+' '+y.toFixed(1)+'H'+(W-16)+'" stroke="'+(i?'#E8EEF4':'#C9D3DD')+'"'+(i?' stroke-dasharray="3 4"':'')+'/><text x="'+(G-6)+'" y="'+(y+4).toFixed(1)+'" text-anchor="end" font-size="12" fill="#52667A">'+esc(ly[i])+'</text>'});
  BX.ts.forEach(function(t,i){var x=X(t);o+='<path d="M'+x.toFixed(1)+' 14V'+(H-B)+'" stroke="'+(i?'#E8EEF4':'#C9D3DD')+'"'+(i?' stroke-dasharray="3 4"':'')+'/><text x="'+x.toFixed(1)+'" y="'+(H-B+15)+'" text-anchor="'+(i===BX.ts.length-1?'end':i?'middle':'start')+'" font-size="12" fill="#52667A">'+esc(lx[i])+'</text>'});
  e.forEach(function(x){o+='<circle cx="'+X(x[1]).toFixed(1)+'" cy="'+Y(x[2]).toFixed(1)+'" r="7" fill="'+CP_PRINC+'" fill-opacity=".82" stroke="#fff" stroke-width="2"><title>'+esc(label(x[0])+' : '+cpVal(x[1])+' ; '+cpVal(x[2]))+'</title></circle>'});
  var poses=e.map(function(x){return {x:X(x[1])-9,y:Y(x[2])-8,w:18,h:16}}),faits=0;
  e.slice().sort(function(a,b){return b[1]-a[1]}).forEach(function(x){if(faits>=5)return;var nom=cpCoupe(label(x[0]),Math.max(10,Math.min(24,Math.round(W/26)))),l=nom.length*6.8,px=X(x[1]),fin=px+11+l>W-8,x0=fin?px-11-l:px+11,b={x:x0,y:Y(x[2])-8,w:l,h:16};if(x0<G||poses.some(function(q){return b.x<q.x+q.w&&q.x<b.x+b.w&&b.y<q.y+q.h&&q.y<b.y+b.h}))return;poses.push(b);faits++;
    o+='<text x="'+(fin?px-11:px+11).toFixed(1)+'" y="'+(Y(x[2])+4).toFixed(1)+'" text-anchor="'+(fin?'end':'start')+'" font-size="12" font-weight="600" fill="#102A43">'+esc(nom)+'</text>'});
  return o+'<text x="'+((W+G)/2)+'" y="'+(H-6)+'" text-anchor="middle" font-size="12.5" fill="#52667A">'+esc(cpTete(c.mesure))+'</text><text x="'+G+'" y="10" font-size="12.5" fill="#52667A">'+esc(cpTete(c.mesure_y))+'</text></svg>'+(BX.bas>0||BY.bas>0?'<p class="cp-note">Axes limités aux valeurs du fichier.</p>':'')}
function cpCouleurs(d){var o={};Object.keys(groupBy(DATA,d)).sort().forEach(function(k,i){o[k]=CP_SERIES[i]});return o}
// Ombre sous une courbe : chaque tronçon lissé, refermé sur l'axe du bas
function cpAires(vals,x,y,y0){var o='',seg=[];vals.concat([null]).forEach(function(v,i){if(v==null){if(seg.length>1)o+=cpLisse(seg)+'L'+seg[seg.length-1][0].toFixed(1)+' '+y0.toFixed(1)+'L'+seg[0][0].toFixed(1)+' '+y0.toFixed(1)+'Z';seg=[];return}seg.push([x(i),y(v)])});return o}
function cpTronçons(vals,x,y){var o='',seg=[];vals.concat([null]).forEach(function(v,i){if(v==null){if(seg.length)o+=cpLisse(seg);seg=[];return}seg.push([x(i),y(v)])});return o}
function cpComparees(rows,c){var g=groupBy(rows,c.dimension),T=cpSerie(rows,c),K=cpComplet(rows,T);if(K.length<2)return null;var P=K.map(function(e){return e[0]});
  var tous=Object.keys(g).sort().map(function(d){var m={};cpSerie(g[d],c).forEach(function(e){m[e[0]]=e[1]});return {d:d,vals:P.map(function(p){return m[p]==null?null:m[p]})}}),S=tous.filter(function(x){return x.vals.filter(function(v){return v!=null}).length>=2});return S.length<2?null:{T:T,K:K,P:P,k:P[P.length-1],S:S,ecartes:tous.filter(function(x){return S.indexOf(x)<0})}}
// Noms courts des courbes : sans « Nombre de » ni les mots que toutes partagent en tête (« clients… »)
function cpCourts(M){var n=M.map(function(m){return String(cpTete(m)).replace(/…$/,'')}),pre=/^(nombre|nb|total|effectif)\s+(de\s+|d['’]\s*|des\s+)/i;
  var t=M.map(function(m){return String(label(m)).replace(pre,'')}),mots=t.map(function(x){return x.split(/\s+/)}),k=0;
  while(mots.every(function(w){return w.length>k+1&&w[k].toLowerCase()===mots[0][k].toLowerCase()}))k++;
  var o={};M.forEach(function(m,i){var c=mots[i].slice(k).join(' ');o[m]=c?c.charAt(0).toUpperCase()+c.slice(1):n[i]});return o}
// Base 100 : la 1re période couverte à 90 % au moins (déclarations) de la mieux couverte. Les périodes d'avant, moins
// bien couvertes, ne sont pas tracées : leur écart viendrait du manque de déclarations, pas de l'activité.
var CP_COUVERT=.9;
function cpBaseIndice(rows,c,P){var n=P.map(function(k){return cpDeclarees(rows,c,k)}),mx=Math.max.apply(null,n);for(var i=0;i<P.length;i++)if(n[i]>=CP_COUVERT*mx)return {i:i,n:n,mx:mx};return {i:0,n:n,mx:mx}}
function cpIndices(rows,c){var T=cpSerie(rows,c),K=cpComplet(rows,T);if(K.length<2)return null;var P0=K.map(function(e){return e[0]}),B=cpBaseIndice(rows,c,P0),avant=P0.slice(0,B.i),P=P0.slice(B.i),b=0,S=[],ecartes=[];if(P.length<2)return null;
  var faibles=P.filter(function(p,i){return B.n[B.i+i]<CP_COUVERT*B.mx});
  [c.mesure].concat(c.ensemble||[]).forEach(function(m){var mm={};cpSerie(rows,{mesure:m,agregation:'somme'}).forEach(function(e){mm[e[0]]=e[1]});
    var reel=P.map(function(p){return mm[p]==null?null:mm[p]}),base=reel[b];
    if(!(base>0)||reel.filter(function(v){return v!=null}).length<2){ecartes.push(m);return}
    S.push({d:m,reel:reel,vals:reel.map(function(v){return v==null?null:100*v/base})})});
  return S.length<2?null:{T:T,K:K,P:P,k:P[P.length-1],S:S,ecartes:ecartes,base:b,indice:true,avant:avant,faibles:faibles,court:cpCourts(S.map(function(x){return x.d}))}}
function cpComparee(rows,c,Zi){var Z=Zi||cpComparees(rows,c);if(!Z)return cpVide();var T=Z.T,K=Z.K,P=Z.P,k=Z.k,S=Z.S,ecartes=Z.ecartes,n=P.length,nom=Z.indice?function(m){return Z.court[m]||cpTete(m)}:label,coul={};
  if(Z.indice)S.forEach(function(x,i){coul[x.d]=CP_SERIES[i]});else coul=cpCouleurs(c.dimension);
  var tout=S.reduce(function(a,x){return a.concat(x.vals.filter(function(v){return v!=null}))},[]),lo=Math.min.apply(null,tout),hi=Math.max.apply(null,tout),pas=cpPlafond((hi-lo||Math.abs(hi)||1)/3),bas=Math.floor(lo/pas+1e-9)*pas,haut=Math.ceil(hi/pas-1e-9)*pas;if(haut<=bas)haut=bas+pas;
  if((lo>=0&&bas<0)||(lo>=0&&lo<=hi*.25))bas=0;var cr=Math.max(1,Math.ceil((haut-bas)/pas/4)),ts=[];for(var u=bas;u<=haut+pas*1e-6;u+=pas*cr)ts.push(u);var lab=cpEtiquettes(ts,pas),W=cpW(c),H=Math.max(240,Math.min(320,Math.round(W*.5))),G=Math.min(Math.round(W*.3),cpMarge(lab,48)),R=Z.indice?Math.min(Math.round(W*.4),Math.max(46,30+7.4*Math.max.apply(null,Z.S.map(function(x){var v=x.reel[x.reel.length-1];return v==null?3:cpVal(v).length})))):Math.min(170,Math.round(W*.34)),B=26,Th=12,dx=(W-G-R)/(n-1),X=function(i){return G+i*dx},Y=function(v){return H-B-(v-bas)/(haut-bas||1)*(H-B-Th)};
  var o='<div class="cp-cc" data-cc="'+CP_CC.length+'"><svg class="cp-svg" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(Z.indice?'Évolution comparée, base 100 en '+plabel(P[Z.base]):'Évolution '+cpDe(c.mesure)+' par '+cpMot(c.dimension))+'">';
  ts.forEach(function(t,a){var ty=Y(t);o+='<path d="M'+G+' '+ty.toFixed(1)+'H'+(W-R)+'" stroke="'+(a?'#E8EEF4':'#C9D3DD')+'"'+(a?' stroke-dasharray="3 4"':'')+'/><text x="'+(G-6)+'" y="'+(ty+4).toFixed(1)+'" text-anchor="end" font-size="12" fill="#52667A">'+esc(lab[a])+'</text>'});
  var ps=Math.max(1,Math.ceil(n/Math.max(2,Math.floor((W-G-R)/80))));for(var i=0;i<n-1;i+=ps)if(X(n-1)-X(i)>=64)o+='<text x="'+X(i).toFixed(1)+'" y="'+(H-7)+'" text-anchor="'+(i?'middle':'start')+'" font-size="12" fill="#52667A">'+esc(plabel(P[i]))+'</text>';
  o+='<text x="'+X(n-1).toFixed(1)+'" y="'+(H-7)+'" text-anchor="end" font-size="12" fill="#52667A">'+esc(plabel(k))+'</text><path class="cp-ligne" d="M0 '+Th+'V'+(H-B)+'" stroke="#8497A8" stroke-dasharray="3 3" style="display:none"/>';
  var L=S.map(function(x){var f=x.vals.length-1;while(x.vals[f]==null)f--;return {d:x.d,c:coul[x.d],f:f,v:x.vals[f],aff:Z.indice?x.reel[n-1]:x.vals[n-1],y:Y(x.vals[f]),ly:Y(x.vals[f])+4}}),q=W-R+18;cpEspacer(L,'ly',17);
  var bout=H-4;L.slice().sort(function(a,b){return b.ly-a.ly}).forEach(function(l){if(l.ly>bout)l.ly=bout;bout=l.ly-17});
  if(Z.indice){var y0=H-B,op=S.length>2?.16:.22;S.forEach(function(x,i){var gid='cpg'+(++CP_NG);
    o+='<defs><linearGradient id="'+gid+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+L[i].c+'" stop-opacity="'+op+'"/><stop offset="1" stop-color="'+L[i].c+'" stop-opacity="0"/></linearGradient></defs><path d="'+cpAires(x.vals,X,Y,y0)+'" fill="url(#'+gid+')"/>'})}
  S.forEach(function(x,i){o+='<path d="'+cpTronçons(x.vals,X,Y)+'" fill="none" stroke="'+L[i].c+'" stroke-width="'+(Z.indice?2.25:2)+'" stroke-linejoin="round" stroke-linecap="round"/>'});
  L.forEach(function(l){var val=cpVal(l.aff),place=Math.max(4,Math.floor((W-q-12)/7.2)-val.length-1);o+='<path d="M'+X(l.f).toFixed(1)+' '+l.y.toFixed(1)+'L'+(q-8)+' '+(l.ly-4).toFixed(1)+'" stroke="'+l.c+'" stroke-opacity=".5"/><circle cx="'+X(l.f).toFixed(1)+'" cy="'+l.y.toFixed(1)+'" r="4" fill="'+l.c+'" stroke="#fff" stroke-width="2"/><rect x="'+(q-5)+'" y="'+(l.ly-9).toFixed(1)+'" width="9" height="9" rx="2" fill="'+l.c+'"/><text x="'+(q+8)+'" y="'+l.ly.toFixed(1)+'" font-size="12" fill="#102A43"><tspan font-weight="600">'+esc(val)+'</tspan> '+(Z.indice?'':esc(cpCoupe(nom(l.d),place)))+'<title>'+esc(nom(l.d))+'</title></text>'});
  CP_CC.push({W:W,G:G,pas:dx,P:P.map(plabel),S:S.map(function(x,i){return {nom:nom(x.d),coul:L[i].c,vals:x.vals,reel:x.reel}})});
  var note='De '+plabel(P[0])+' à '+plabel(k).replace(/\.$/,'')+'.';if(bas>0||haut<0){var bornes=cpEtiquettes([bas,haut],pas);note+=' Axe de '+bornes[0]+' à '+bornes[1]+', resserré pour lire les écarts.'}if(K.length<T.length)note+=' '+plabel(T[T.length-1][0]).replace(/\.$/,'')+' est incomplet : non affiché.';if(Z.indice){var g=GRAN[gran].toLowerCase();note='Base 100 = '+plabel(P[Z.base])+' : 150 = une fois et demie la valeur de '+plabel(P[Z.base])+'. À droite et au survol, les vraies valeurs. '+note;
    if(Z.avant.length)note+=' Non tracé'+(Z.avant.length>1?'s':'')+', car moins bien couvert'+(Z.avant.length>1?'s':'')+' (moins de 90 % des déclarations du '+g+' le mieux couvert) : '+Z.avant.map(plabel).join(', ')+'.';
    if(Z.faibles.length)note+=' Attention, moins bien couvert'+(Z.faibles.length>1?'s':'')+' : '+Z.faibles.map(plabel).join(', ')+' ; une baisse peut venir des déclarations manquantes.';
    if(ecartes.length)note+=' Sans valeur de référence : '+ecartes.map(cpTete).join(', ')+'.'}
  else if(ecartes.length)note+=' Trop peu de périodes pour : '+ecartes.map(function(x){return label(x.d)}).join(', ')+'.';
  var leg=Z.indice?'<ul class="cp-series" style="list-style:none;display:flex;flex:none;flex-wrap:wrap;gap:6px 18px;margin:8px 0 0;padding:0;font-size:12.5px;line-height:1.35;color:#102A43">'+S.map(function(x){return '<li style="display:flex;flex:0 1 auto;max-width:100%;align-items:baseline;gap:6px"><i style="flex:none;width:10px;height:10px;border-radius:2px;background:'+coul[x.d]+'"></i><span>'+esc(nom(x.d))+'</span></li>'}).join('')+'</ul>':'';
  return o+'</svg><div class="cp-bulle" hidden></div></div>'+leg+'<p class="cp-note">'+esc(note)+'</p>'}
function cpSurvol(el,e){var D=CP_CC[+el.dataset.cc],r=el.getBoundingClientRect(),n=D.P.length,x=(e.clientX-r.left)*D.W/r.width,i=Math.max(0,Math.min(n-1,Math.round((x-D.G)/D.pas))),vx=D.G+i*D.pas,px=vx*r.width/D.W,b=el.querySelector('.cp-bulle'),l=el.querySelector('.cp-ligne');
  l.setAttribute('transform','translate('+vx.toFixed(1)+' 0)');l.style.display='';b.textContent='';var t=document.createElement('div');t.className='cp-bt1';t.textContent=D.P[i];b.appendChild(t);
  D.S.slice().sort(function(a,z){var u=a.vals[i],w=z.vals[i];return u==null?1:w==null?-1:w-u}).forEach(function(s){var p=document.createElement('p'),m=document.createElement('i'),v=document.createElement('b'),o=document.createElement('span');m.style.background=s.coul;v.textContent=s.reel?(s.reel[i]==null?'—':cpVal(s.reel[i])+' (indice '+fmt(Math.round(s.vals[i]))+')'):cpVal(s.vals[i]);o.textContent=s.nom;p.appendChild(m);p.appendChild(v);p.appendChild(o);b.appendChild(p)});
  b.hidden=false;b.style.left=(px>r.width/2?Math.max(0,px-b.offsetWidth-12):px+12)+'px'}
function cpTranches(rows,c){var v=rows.map(function(r){return num(r[c.mesure])}).filter(function(x){return x!=null});if(v.length<5)return null;var mi=Math.min.apply(null,v),ma=Math.max.apply(null,v),n=10,l=(ma-mi)/n||1,b=[];for(var i=0;i<n;i++)b.push(0);v.forEach(function(x){b[Math.min(n-1,Math.floor((x-mi)/l))]++});var m=Math.max.apply(null,b);return {v:v,mi:mi,ma:ma,l:l,b:b,m:m,top:b.indexOf(m)}}
function cpHisto(rows,c){var t=cpTranches(rows,c);if(!t)return cpVide();var v=t.v,mi=t.mi,ma=t.ma,l=t.l,b=t.b,m=t.m,top=t.top;
  return '<div class="cp-hi">'+b.map(function(q,i){return '<i title="'+esc(fmt(mi+i*l)+' à '+fmt(mi+(i+1)*l)+' : '+q+' lignes')+'" style="height:'+(100*q/m).toFixed(1)+'%"></i>'}).join('')+'</div><div class="cp-hx"><span>'+esc(fmt(mi))+'</span><span>'+esc(fmt(ma))+'</span></div><p class="cp-note">Tranche la plus fournie : '+esc(fmt(mi+top*l))+' à '+esc(fmt(mi+(top+1)*l))+' ('+m+' lignes sur '+v.length+').</p>'}
// ---- Titres-messages : les formules de `titres.py`, remplies à chaque rendu avec les variables calculées ici
function cpRemplir(t,v){if(!t)return null;var ok=true,s=t.replace(/\{(\w+)\}/g,function(a,k){if(v[k]==null)ok=false;return String(v[k]).replace(/[{}]/g,'')});return ok?s.charAt(0).toUpperCase()+s.slice(1):null}
function cpMessage(c,rows){try{var m=CP_MSG[c.type](c,rows);if(!m)return null;m.v.mesure=cpTete(c.mesure);var t=cpRemplir(c.formule,m.v);return t&&m.note?t+'. '+m.note:t}catch(e){return null}}
// Valeurs déclarées pour une mesure sur une période (cases résumées d'un gros fichier : leur effectif)
function cpDeclarees(rows,c,k){return rows.reduce(function(s,r){if(!r.__p||pkey(r.__p)!==k)return s;if(META.cube){var v=num(r[c.mesure+'__v']);return s+(v||0)}return s+(num(r[c.mesure])!=null?1:0)},0)}
// Une somme baisse aussi quand des lignes manquent : on le dit plutôt que d'annoncer une baisse qui n'en est peut-être pas une
// Les sites qui ont déclaré la période précédente et pas la dernière (colonne des sites connue par le bilan qualité)
function cpSitesManquants(c,rows,a,b){var D=META.qualite&&META.qualite.declarations,u=D&&D.colonne;if(!u)return null;var ua={},ub={},vu=false;
  rows.forEach(function(r){if(!r.__p||r[u]==null)return;vu=true;var k=pkey(r.__p),ok=META.cube?num(r[c.mesure+'__v'])>0:num(r[c.mesure])!=null;if(!ok)return;if(k===a)ua[String(r[u])]=1;else if(k===b)ub[String(r[u])]=1});
  if(!vu)return null;var m=Object.keys(ua).filter(function(k){return !ub[k]}).sort();return {m:m,na:Object.keys(ua).length,nb:Object.keys(ub).length,col:u}}
function cpNoteManques(c,rows,a,b,va,vb){if(!/^(somme|sum|compte|count|dernier)$/.test((c.agregation||'').toLowerCase()))return null;
  var S=cpSitesManquants(c,rows,a,b);if(S&&S.m.length)return 'Attention : '+(S.m.length<=3?S.m.join(', '):S.m.length+' '+cpMot(S.col))+' : pas de déclaration en '+plabel(b)+' ('+S.nb+' sur '+S.na+')'+(vb<va?', la baisse peut venir de ce manque':'');
  var na=cpDeclarees(rows,c,a),nb=cpDeclarees(rows,c,b);if(!na||nb>=na*.9)return null;
  return 'Attention : moins de valeurs déclarées en '+plabel(b)+' ('+fmt(nb)+' contre '+fmt(na)+' en '+plabel(a)+')'+(vb<va?', la baisse peut venir des données manquantes':'')}
function cpEvolution(a,b,c){var d=b-a;if(c.nature==='taux'&&Math.max(Math.abs(a),Math.abs(b))>2){var p=Math.round(Math.abs(d)*10)/10;return p?(d>0?'en hausse de ':'en baisse de ')+fmt(p)+' point'+(p>=2?'s':''):'sans changement'}
  if(a>0){var x=100*Math.abs(d)/a;return x>=.05?(d>0?'en hausse de ':'en baisse de ')+cpPct(x):'sans changement'}return d?(d>0?'en hausse de ':'en baisse de ')+fmt(Math.abs(d)):'sans changement'}
function cpSigne(d){return (d>0?'+':d<0?'−':'')+cpVal(Math.abs(d))}
function cpComplet(rows,s){var n=s.length;if(n<2)return s;var d=s[n-1][0],L=gran==='W'?10:7,compte=function(k){var u={};rows.forEach(function(r){if(r.__p&&pkey(r.__p)===k)u[r.__p.slice(0,L)]=1});return Object.keys(u).length};
  return (META.partiel&&pkey(META.partiel)===d)||(gran!=='M'&&gran!=='D'&&compte(d)<compte(s[n-2][0]))?s.slice(0,n-1):s}
function cpEcartMax(e){return e.reduce(function(m,x){return !m||Math.abs(x[1])>Math.abs(m[1])?x:m},null)}
var CP_MSG={
  courbe:function(c,r){var s=cpComplet(r,cpSerie(r,c)),n=s.length;if(n<2)return null;var a=s[n-2],b=s[n-1];return {v:{dernier:cpVal(b[1]),periode:plabel(b[0]),precedente:plabel(a[0]),evolution:cpEvolution(a[1],b[1],c)},note:cpNoteManques(c,r,a[0],b[0],a[1],b[1])}},
  classement:function(c,r){var e=cpGroupes(r,c);if(e.length<2||e[0][1]===e[1][1])return null;return {v:{premier:label(e[0][0]),valeur:cpVal(e[0][1]),second:label(e[1][0]),valeur2:cpVal(e[1][1])}}},
  anneau:function(c,r){var e=cpGroupes(r,c,5).filter(function(x){return x[1]>0}),t=e.reduce(function(s,x){return s+x[1]},0);return e.length<2||!t?null:{v:{premier:label(e[0][0]),part:cpPct(100*e[0][1]/t)}}},
  treemap:function(c,r){var e=cpGroupes(r,c,40).filter(function(x){return x[1]>0}),t=e.reduce(function(s,x){return s+x[1]},0);return e.length<2||!t?null:{v:{premier:label(e[0][0]),part:cpPct(100*e[0][1]/t)}}},
  chaleur:function(c,r){var X=cpMatrice(r,c);if(!X||X.mx==null||X.mx===X.mi)return null;var h=null;X.D.forEach(function(d){X.P.forEach(function(p){if(!h&&X.M[d+'|'+p]===X.mx)h=[d,p]})});return {v:{groupe:label(h[0]),periode:plabel(h[1]),valeur:cpVal(X.mx)}}},
  pente:function(c,r){var p=cpPeriodes(r,c);if(!p)return null;var e=cpEcarts(p,c).filter(function(x){return x[1]!=null&&x[2]!=null}).map(function(x){return [x[0],x[2]-x[1]]});if(e.length<2)return null;var h=e.filter(function(x){return x[1]>0}).length,m=cpEcartMax(e);if(!m[1])return null;return {v:{bilan:h?h+' sur '+e.length+' en hausse':'aucun en hausse sur '+e.length,premier:label(m[0]),ecart:cpSigne(m[1])}}},
  passage:function(c,r){var p=cpPeriodes(r,c);if(!p)return null;var e=cpEcarts(p,c).filter(function(x){return x[1]!=null||x[2]!=null}).map(function(x){return [x[0],(x[2]||0)-(x[1]||0),x[1]||0,x[2]||0]});if(e.length<2)return null;var m=cpEcartMax(e),t0=e.reduce(function(s,x){return s+x[2]},0),t1=e.reduce(function(s,x){return s+x[3]},0);if(!m[1])return null;return {v:{evolution:cpEvolution(t0,t1,c),annee_a:p.a,annee_b:p.b,premier:label(m[0]),ecart:cpSigne(m[1])}}},
  cible:function(c,r){var L=cpCibleLignes(r,c),e=L.e,n=e.length;if(!n)return null;var a=e.filter(function(x){return x[1]>=x[2]}).length,o=agg(L.R,c.objectif,c.agregation),taux=o>0?cpPct(100*agg(L.R,c.mesure,c.agregation)/o):null;if(!c.dimension)return taux?{v:{taux:taux}}:null;
    var bilan=a+' sur '+n+' à l\'objectif ou au-dessus';return {v:{bilan:bilan,taux:taux}}},
  bulles:function(c,r){var g=groupBy(r,c.dimension),e=Object.keys(g).map(function(k){return [agg(g[k],c.mesure,c.agregation),agg(g[k],c.mesure_y,c.agregation_y)]}).filter(function(x){return x[0]!=null&&x[1]!=null}),n=e.length;if(n<5)return null;var mx=e.reduce(function(s,x){return s+x[0]},0)/n,my=e.reduce(function(s,x){return s+x[1]},0)/n,sx=0,sy=0,sxy=0;e.forEach(function(x){sx+=(x[0]-mx)*(x[0]-mx);sy+=(x[1]-my)*(x[1]-my);sxy+=(x[0]-mx)*(x[1]-my)});if(!sx||!sy)return null;var k=sxy/Math.sqrt(sx*sy);return {v:{mesure_y:cpMot(c.mesure_y),lien:k>=.7?'vont de pair':k<=-.7?'évoluent en sens inverse':'sans lien net'}}},
  comparee:function(c,r){var Z=cpComparees(r,c);if(!Z)return null;var i=Z.P.length-1,e=Z.S.filter(function(x){return x.vals[i]!=null}).sort(function(a,b){return b.vals[i]-a.vals[i]});if(e.length<2||e[0].vals[i]===e[1].vals[i])return null;return {v:{premier:label(e[0].d),valeur:cpVal(e[0].vals[i]),periode:plabel(Z.k)}}},
  histogramme:function(c,r){var t=cpTranches(r,c);return t?{v:{de:fmt(t.mi+t.top*t.l),a:fmt(t.mi+(t.top+1)*t.l),part:cpPct(100*t.m/t.v.length)}}:null}};
var CP_DESSINS={
  courbe:function(c,r){var Z=c.ensemble&&c.ensemble.length?cpIndices(r,c):null;
    return Z?cpCarte(c,'Évolution '+cpDe(c.mesure),'Base 100 en '+plabel(Z.P[Z.base])+' · '+Z.S.map(function(x){return Z.court[x.d]}).join(', '),cpComparee(r,c,Z))
      :cpCarte(c,'Évolution '+cpDe(c.mesure),cpSousTitre(c)+' par '+GRAN[gran].toLowerCase(),cpCourbe(r,c))},
  classement:function(c,r){return cpCarte(c,cpTete(c.mesure)+' par '+cpMot(c.dimension),cpSousTitre(c),cpClassement(r,c))},
  anneau:function(c,r){return c.ensemble&&c.ensemble.length?cpCarte(c,'Part de chaque '+cpMot(c.dimension),'Par indicateur · chaque groupe de bandes = 100 %',cpColonnes(r,c))
    :cpCarte(c,'Part de chaque '+cpMot(c.dimension),cpTete(c.mesure),cpAnneau(r,c))},
  treemap:function(c,r){return cpCarte(c,cpTete(c.mesure)+' par '+cpMot(c.dimension),'La surface suit la valeur',cpTreemap(r,c))},
  chaleur:function(c,r){return cpCarte(c,cpTete(c.mesure)+' par '+cpMot(c.dimension)+' et par '+GRAN[gran].toLowerCase(),cpSousTitre(c)+'. Plus c\'est foncé, plus la valeur est élevée.',cpChaleur(r,c))},
  pente:function(c,r){return cpCarte(c,'Qui progresse, qui recule : '+cpMot(c.mesure),cpSousTitre(c),cpPente(r,c))},
  passage:function(c,r){return cpCarte(c,'Ce qui explique la variation : '+cpMot(c.mesure),'Par '+cpMot(c.dimension),cpPassage(r,c))},
  cible:function(c,r){return cpCarte(c,cpTete(c.mesure)+' par rapport à l\'objectif',c.dimension?'Par '+cpMot(c.dimension):'',cpCible(r,c))},
  bulles:function(c,r){return cpCarte(c,cpTete(c.mesure)+' et '+cpMot(c.mesure_y),(Object.keys(groupBy(r,c.dimension)).length<=CP_JUMELLES?'Par ':'Chaque point : ')+cpMot(c.dimension),cpBulles(r,c))},
  comparee:function(c,r){return cpCarte(c,'Évolution '+cpDe(c.mesure)+' par '+cpMot(c.dimension),cpSousTitre(c),cpComparee(r,c))},
  histogramme:function(c,r){return cpCarte(c,'Répartition '+cpDe(c.mesure),'Nombre de lignes par tranche',cpHisto(r,c))}};
function cpTemporel(){return META.composition.lignes.some(function(l){return l.some(function(c){return /^(courbe|chaleur|comparee)$/.test(c.type)})})}
function renderCompo(rows){var el=document.getElementById('compo'),html='';CP_R=rows;CP_CC=[];if(cpTemporel()&&GOK&&GOK.length>1)html='<div class="cp-outils"><span>Regrouper par</span>'+seg()+'</div>';document.getElementById('row2').style.display='none';document.getElementById('row3').style.display='none';CP_L=+el.clientWidth||1200;
  META.composition.lignes.forEach(function(l){l.forEach(function(c){try{html+=CP_DESSINS[c.type](c,rows)}catch(e){html+=cpCarte(c,cpTete(c.mesure),'',cpVide('Ce graphique n\'a pas pu être dessiné.'))}})});el.innerHTML=html;
  [].forEach.call(el.querySelectorAll('.seg button'),function(b){b.onclick=function(){gran=b.dataset.g;render()}});
  [].forEach.call(el.querySelectorAll('.cp-cc'),function(b){b.onpointermove=b.onpointerdown=function(e){cpSurvol(b,e)};b.onpointerleave=function(){b.querySelector('.cp-bulle').hidden=true;b.querySelector('.cp-ligne').style.display='none'}});return []}
// ---- Indicateurs clés : tableau compact, replié tant qu'on ne le demande pas
var CP_I_OUVERT=false;
function cpITon(g){return g.bon!==-1||g.pct==null||g.sens==='stable'||Math.abs(g.pct)<1?'':g.pct>0?' cp-i-mal':' cp-i-bon'}
function cpIRendre(cible){renderGaps(filtered());var e=document.querySelector(cible);if(e)e.focus()}
function cpIndicateurs(el,o){var G=o.G,h=0,b=0,st=0,filtre=FCOLS.some(function(c){return state[c]!=null&&state[c]!=='all'});
  G.forEach(function(g){if(g.pct==null)return;if(g.sens==='stable'||!(Math.round(Math.abs(g.pct)*10)/10))st++;else if(g.pct>0)h++;else b++});
  var bilan=[h?h+' en hausse':'',b?b+' en baisse':'',st?st+' stable'+(st>1?'s':''):''].filter(Boolean).join(', '),resume=G.length+' indicateur'+(G.length>1?'s':'')+(o.cur!=null?' · '+gkey(o.cur):'')+(bilan?' · '+bilan:'');
  var titre='Indicateurs clés de suivi'+(o.D?' · '+o.D.nom:''),gel=!o.K.live&&filtre?'<p class="cp-note">Ce panneau porte sur tout le fichier : il ne suit pas les filtres sur un fichier de cette taille.</p>':'';
  var tete='<div class="cp-i-l cp-i-h" role="row"><span role="columnheader">Indicateur</span><span class="cp-i-n" role="columnheader">'+esc(o.cur!=null?gkey(o.cur):'Valeur')+'</span><span class="cp-i-n cp-i-r" role="columnheader">'+esc(o.ref!=null?gkey(o.ref):'—')+'</span><span class="cp-i-n" role="columnheader">Écart</span></div>';
  var lignes=G.map(function(g){var etat=(g.k&&g.k.etat)||g.etat;return '<div class="cp-i-l" role="row"><span role="cell" title="'+esc((g.indicateur||'')+(g.libelle?'\n'+g.libelle:''))+'">'+esc(g.indicateur)+(etat?' <small>statut actuel</small>':'')+'</span><span class="cp-i-n" role="cell"><b>'+o.val(g.derniere,g.pts)+'</b></span><span class="cp-i-n cp-i-r" role="cell">'+o.val(g.precedente,g.pts)+'</span><span class="cp-i-n'+cpITon(g)+'" role="cell">'+(g.pct==null?'—':g.sens==='stable'?'sans changement':o.delta(g))+'</span></div>'}).join('');
  el.innerHTML='<button type="button" class="cp-i-bt" id="cp-i-bt" aria-expanded="'+CP_I_OUVERT+'" aria-controls="cp-i-corps"><span><b>'+esc(titre)+'</b><small>'+esc(resume)+'</small></span><em>'+(CP_I_OUVERT?'Masquer':'Afficher')+'</em></button>'
    +'<div id="cp-i-corps"'+(CP_I_OUVERT?'':' hidden')+'><div class="cp-i-c"><small>'+o.sub+'</small>'+o.ctl+'</div>'+(o.warn?'<p class="gaps-note">'+o.warn+'</p>':'')+gel+'<div class="cp-i-t" role="table" aria-label="'+esc(titre)+'">'+tete+lignes+'</div></div>';
  document.getElementById('cp-i-bt').onclick=function(){CP_I_OUVERT=!CP_I_OUVERT;cpIRendre('#cp-i-bt')};
  var sel=document.getElementById('gapP');if(sel)sel.onchange=function(){GAP.p=sel.value;cpIRendre('#gapP')};
  [].forEach.call(el.querySelectorAll('.gaps-ctl .seg button'),function(b){b.onclick=function(){GAP.mode=b.dataset.m;cpIRendre('.gaps-ctl .seg button[data-m="'+b.dataset.m+'"]')}})}
// Gros fichiers : les lignes de DATA sont des cases déjà résumées, et `agg` les additionne au lieu de compter des lignes
if(META.cube){agg=function(rows,col,a){a=(a||'').toLowerCase();
  if(a==='dernier'){var lp=null;rows.forEach(function(r){var m=r.__p?String(r.__p).slice(0,7):null;if(m&&(lp==null||m>lp))lp=m});rows=rows.filter(function(r){return r.__p&&String(r.__p).slice(0,7)===lp});a='somme'}
  var som=function(k){var s=null;rows.forEach(function(r){var v=num(r[k]);if(v!=null)s=(s||0)+v});return s},bout=function(k,f){var b=null;rows.forEach(function(r){var v=num(r[k]);if(v!=null)b=b==null?v:f(b,v)});return b};
  if(a==='compte'||a==='count'||!col)return col?som(col+'__n')||0:som('__n')||0;
  if(isRate(a)){var o=som(col+'__oui');if(o!=null){var t=som(col+'__n');return t?100*o/t:null}var e=som(col),q=som(col+'__v');if(e==null||!q)return null;return bout(col+'__ma',Math.max)<=1?100*e/q:e/q}
  var s=som(col);if(s==null)return null;if(a==='moyenne')return s/som(col+'__v');if(a==='max')return bout(col+'__ma',Math.max);if(a==='min')return bout(col+'__mi',Math.min);if(/^m[ée]diane?$|^median$/.test(a))return null;return s};
  howCalc=function(k,rows){return /^m[ée]dian/.test((k.agregation||'').toLowerCase())?'Médiane de toutes les lignes du fichier. Sur un fichier de cette taille, elle ne se recalcule pas avec un filtre.':'Calculé sur toutes les lignes retenues par les filtres, sans échantillon.'}}
var cpAttente;window.addEventListener('resize',function(){clearTimeout(cpAttente);cpAttente=setTimeout(function(){var el=document.getElementById('compo');if(el&&Math.abs(el.clientWidth-CP_L)>24)render()},200)});
"""
