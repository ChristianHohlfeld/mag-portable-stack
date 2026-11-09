/* MAG live tool timeline. Consumes only existing SSE tool/tool_result/step data.
 * Tools do not stream stdout while running: show true stdout tail on tool_result.
 */
(function(){
'use strict';
const state=new Map();
function key(node,s){return String(s?.displayMs||s?.started||'')+':'+String(node?.dataset?.bot||'')}
function fmt(ms){let t=Math.max(0,Math.floor(ms/1000));return Math.floor(t/60)+':'+String(t%60).padStart(2,'0')}
function text(value){return String(value==null?'':value)}
function pretty(value){
 if(typeof value==='string'){try{return JSON.stringify(JSON.parse(value),null,2)}catch{return value}}
 try{return JSON.stringify(value??{},null,2)}catch{return '{}'}
}
function redact(src){
 // The local room is owner-only, but do not expose common credentials by default.
 return text(src)
 .replace(/(authorization\s*[:=]\s*bearer\s+)\S+/ig,'$1[redacted]')
 .replace(/((?:token|password|secret|api_key|private_key|access_key)\s*[:=]\s*["']?)[^\s"'}]+/ig,'$1[redacted]')
 .replace(/\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/g,'[redacted]');
}
function current(t){return t.result==null}
function status(t){
 if(current(t))return 'läuft';
 if(t.ok===false){
   const output=text(t.result).toLowerCase();
   return /timeout|timed out|zeitüberschreitung/.test(output)?'Timeout':
     /abgebrochen|cancelled|canceled/.test(output)?'Abgebrochen':'Fehler';
 }
 return 'fertig';
}
function make(tag,cls,value){
 const el=document.createElement(tag);if(cls)el.className=cls;
 if(value!==undefined)el.textContent=value;
 return el;
}
function safeArgs(t){
 const a=t?.args_text||t?.args||{};
 const raw=typeof a==='object'&&a!==null&&typeof a.command==='string'?a.command:pretty(a);
 return redact(raw);
}
function patchRow(row,t,i,now){
 row.dataset.idx=String(i);
 const openArgs=row.querySelector('.mag-tool-args')?.open||false;
 const openOut=row.querySelector('.mag-tool-output')?.open||false;
 const active=current(t);
 const duration=active?Math.max(0,now-(t.at||now)):Math.max(0,+t.ms||0);
 const label=t.name||t.tool||'Tool';
 const preview=safeArgs(t);
 const end=active?text(t.output_tail||''):text(t.result||'');
 const body=make('div','mag-tool-entry');
 const line=make('div','mag-tool-entry-line');
 line.append(make('span','mag-tool-state '+(active?'running':status(t)==='fertig'?'ok':'bad'),status(t)),
             make('strong','mag-tool-name',label),
             make('span','mag-tool-timer',fmt(duration)));
 body.append(line);
 const args=make('details','mag-tool-args');
 args.open=openArgs;
 args.append(make('summary','',preview.length>100?preview.slice(0,100)+'…':preview));
 args.append(make('pre','',preview));
 body.append(args);
 const out=make('details','mag-tool-output');
 out.open=openOut;
 const output=end?end.slice(-3000):active?'Noch keine stdout-Daten vom laufenden Befehl':'Keine Ausgabe gemeldet';
 out.append(make('summary','',active?'stdout live · '+(end.length?end.length+' Zeichen':'wartet'):'stdout · '+(end.length?end.length+' Zeichen':'leer')));
 out.append(make('pre','',redact(output)));
 body.append(out);
 row.replaceChildren(body);
}
function paint(node,s,cancel){
 if(!node||!s)return;
 const tools=Array.isArray(s.tools)?s.tools:[];
 const steps=Array.isArray(s.steps)?s.steps:[];
 let card=node.querySelector(':scope > .mag-tool-timeline');
 if(!card){
   card=make('section','mag-tool-timeline');
   card.dataset.run=key(node,s);
   card.setAttribute('aria-label','Live-Werkzeugaktivität');
   const details=make('details','mag-tool-history');
   const summary=make('summary','mag-tool-summary');
   const title=make('span','mag-tool-summary-title');
   const clock=make('span','mag-tool-summary-clock');
   summary.append(title,clock);
   const body=make('div','mag-tool-body');
   const entries=make('ol','mag-tool-entries');
   const prior=make('details','mag-tool-prior');
   prior.append(make('summary','','Bisherige Schritte'),make('ol','mag-tool-prior-list'));
   body.append(entries,prior);
   details.append(summary,body);
   const cancelButton=make('button','mag-tool-stop','Abbrechen');
   cancelButton.type='button';cancelButton.title='Diesen Botlauf über die bestehende Stop-Funktion abbrechen';
   cancelButton.addEventListener('click',e=>{e.stopPropagation();cancelButton.disabled=true;
     Promise.resolve(cancel(node.dataset.bot||'')).catch(()=>{}).finally(()=>{cancelButton.disabled=false})});
   card.append(details,cancelButton);
   node.append(card);
 }
 const now=Date.now();
 const selected=tools.at(-1);
 const active=selected&&current(selected);
 const head=card.querySelector('.mag-tool-summary-title');
 const clock=card.querySelector('.mag-tool-summary-clock');
 if(selected){
   head.textContent=(s.name||'Bot')+' · '+(selected.name||selected.tool||'Tool')+' · '+status(selected);
   clock.textContent=fmt(active?now-(selected.at||s.started||now):(+selected.ms||0));
 }else{
   head.textContent=(s.name||'Bot')+' · '+(s.label||'Bereitet vor');
   clock.textContent=fmt(now-(s.started||now));
 }
 const btn=card.querySelector('.mag-tool-stop');
 btn.hidden=!active;
 btn.disabled=!!s.stopping;
 btn.textContent=s.stopping?'Stoppt…':'Abbrechen';
 const entries=card.querySelector('.mag-tool-entries');
 tools.forEach((t,i)=>{
   let row=entries.children[i];
   if(!row){row=make('li','mag-tool-item');entries.append(row)}
   patchRow(row,t,i,now);
 });
 while(entries.children.length>tools.length)entries.lastChild.remove();
 const prior=card.querySelector('.mag-tool-prior'),ol=prior.querySelector('ol'),isOpen=prior.open;
 if(steps.length){
   prior.hidden=false;
   const last=steps.slice(-40);
   while(ol.children.length>last.length)ol.lastChild.remove();
   last.forEach((step,i)=>{
     let li=ol.children[i];if(!li){li=make('li','');ol.append(li)}
     const word=typeof step==='string'?step:pretty(step);
     if(li.textContent!==word)li.textContent=word;
   });
   prior.querySelector('summary').textContent='Bisherige Schritte ('+steps.length+')';
   prior.open=isOpen;
 }else prior.hidden=true;
 card.hidden=false;
}
function tick(node,s){if(!node?.isConnected||!s)return;const card=node.querySelector(':scope > .mag-tool-timeline');if(!card)return;
 const t=s.tools?.at(-1),active=t&&current(t),now=Date.now();
 card.querySelector('.mag-tool-summary-clock').textContent=fmt(active?now-(t.at||s.started||now):t?+t.ms||0:now-(s.started||now));
 if(active){const row=card.querySelector('.mag-tool-entries > li:last-child');const time=row?.querySelector('.mag-tool-timer');
   if(time)time.textContent=fmt(now-(t.at||now))}
}
window.magToolTimeline={paint,tick,status,redact};
})();
