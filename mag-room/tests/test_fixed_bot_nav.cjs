// mag-test-timeout: 60
require('./mag_deadline.cjs').arm(60);
'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=fs.readFileSync('mag-room/web/index.html','utf8');
const begin=html.indexOf("<script>\n'use strict';");
assert.ok(begin>=0,'main room script exists');
const end=html.indexOf('</script>',begin);
assert.ok(end>begin,'main room script closes');
new vm.Script(html.slice(begin+8,end),'mag-room-main.js');
assert.match(html, /id="fixed-bot-nav" role="group"/);
assert.match(html, /#app-header #fixed-bot-nav\{display:flex!important/);
assert.match(html, /#room-menu-btn,#room-menu,#proaktiv-row,#bot-switch-row/);
assert.match(html, /!state.configured_model\)actList.push\(\['Umbenennen'/);
assert.match(html, /state.dual_numa\|\|state.configured_model/);
const start=html.indexOf('function syncFixedBotNav(next){');
const stop=html.indexOf('function syncModelChoice(next){',start);
assert.ok(start>0&&stop>start);
const code=html.slice(start,stop);
const nav={
  hidden:true,items:[],replaceChildren(){this.items=[]},
  append(x){this.items.push(x)}
};
const context={
  document:{createElement:(tag)=>({tag,attrs:{},classes:[],classList:{add(v){this.classes.push(v)}},
    setAttribute(key,val){this.attrs[key]=val;}})},
  $:id=>id==='fixed-bot-nav'?nav:null,
  focusedBotId:'ds',
  focusBot:id=>{context.focusedBotId=id}
};
vm.runInNewContext(code,context);
context.syncFixedBotNav({configured_model:true,agents:[
  {id:'ds',slot:1,model:'deepseek-v4-flash-0731',vm:'agent1'},
  {id:'qw',slot:2,model:'qwen3.8-flash-next-uncensored',vm:'agent2'}
],model_instances:[
  {slot:1,model:'deepseek-v4-flash-0731',state:'ready'},
  {slot:2,model:'qwen3.8-flash-next-uncensored',state:'ready'}
]});
assert.equal(nav.hidden,false);
assert.deepEqual(nav.items.map(b=>b.textContent),['DeepSeek','Qwen']);
assert.equal(nav.items[0].attrs['aria-current'],'true');
assert.equal(nav.items[1].disabled,false);
nav.items[1].onclick();
assert.equal(context.focusedBotId,'qw');
context.syncFixedBotNav({configured_model:true,agents:[],model_instances:[
 {slot:1,model:'deepseek-v4-flash-0731',state:'down'},
 {slot:2,model:'qwen3.8-flash-next-uncensored',state:'down'}
]});
assert.deepEqual(nav.items.map(b=>b.textContent),['DeepSeek','Qwen']);
assert.ok(nav.items.every(b=>b.disabled),'missing bot never appears clickable');
context.syncFixedBotNav({configured_model:false,agents:[]});
assert.equal(nav.hidden,true);
assert.equal(nav.items.length,0);
console.log('Fixed bot nav PASS');
