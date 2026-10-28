// mag-test-timeout: 60
require('./mag_deadline.cjs').arm(60);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const start = html.indexOf('let composerSending=false;');
assert.ok(start >= 0);
const handler = html.slice(start, html.indexOf(';window.addEventListener(\'load\'', start));
let focusCalls=0, blurCalls=0, coarse=false;
const elements = {'send-form':{}, 'send-button':{dataset:{mode:'send',state:''}}, message:{value:'First',focus(){focusCalls++},blur(){blurCalls++}},think:{checked:false}};
let resolve, calls=0, focusAndBusy=0;
const context = {window:{}, matchMedia:()=>({matches:coarse}), $:id=>elements[id],selected:new Set(['a']),state:{agents:[{id:'a',enabled:true}],busy_bots:[],pending:[]},streams:{},async refresh(){},chosenBots(){return []},stagedPhotos:[],stagedFiles:[],jumpForSend(){},render(){},notice(){},fitMessageHeight(){},syncComposerButton(){focusAndBusy++},api(){calls++;return new Promise(r=>resolve=r)}};
vm.runInNewContext(handler, context);
(async()=>{
 const first=elements['send-form'].onsubmit({preventDefault(){}});
 await elements['send-form'].onsubmit({preventDefault(){}});
 assert.equal(calls,1,'double submit must not duplicate the request');
 elements.message.value='New unsent draft';
 resolve({});await first;
 assert.equal(elements.message.value,'New unsent draft','pending response must preserve later typing');
 const next=elements['send-form'].onsubmit({preventDefault(){}});
 resolve({});await next;
 assert.equal(elements.message.value,'','unchanged submitted text clears normally');
 assert.equal(focusCalls,2,'desktop keeps focus after both sends');
 assert.equal(blurCalls,0,'desktop never blurs the composer');
 coarse=true;
 elements.message.value='Mobile message';
 const phone=elements['send-form'].onsubmit({preventDefault(){}});
 assert.equal(blurCalls,1,'phone releases focus immediately on submit, before API response');
 resolve({});await phone;
 assert.equal(focusCalls,2,'mobile must not reopen the keyboard after the API response');
 assert.equal(elements.message.value,'','mobile submitted text clears normally');
 const savedButton=elements['send-button'];
 delete elements['send-button'];
 elements.message.value='No button yet';
 const bare=elements['send-form'].onsubmit({preventDefault(){}});
 resolve({});await bare;
 assert.equal(calls,4,'a missing send button still sends');
 assert.equal(calls,4,'a missing send button cannot be mistaken for Stop');
 elements['send-button']=savedButton;
 assert.ok(focusAndBusy>=8,'busy and idle transitions repaint the composer');
 // The send button never changes into Stop: Stop has its own independent
 // control. A stale send-button dataset must not suppress a valid new send.
 elements['send-button'].dataset.mode='stop';
 elements.message.value='Still a send';
 const sent=elements['send-form'].onsubmit({preventDefault(){}});
 assert.equal(calls,5,'independent Stop button never hijacks the send form');
 resolve({});await sent;
 assert.equal(elements.message.value,'','message was submitted despite stale legacy mode');
 // Unacknowledged uploads must leave both the text and the remaining
 // files/photos in the composer. They were previously spliced out up front.
 context.stagedFiles.push({name:'draft.txt',size:10});
 context.flushStagedFiles=async()=>{throw Error('Upload fehlgeschlagen')};
 elements.message.value='Nachricht mit Anhang';
 await elements['send-form'].onsubmit({preventDefault(){}});
 assert.equal(elements.message.value,'Nachricht mit Anhang');
 assert.equal(context.stagedFiles.length,1,'a failed upload retains its staged file');
 context.stagedFiles.length=0;
 context.stagedPhotos.push({name:'draft.jpg',blob:{},url:'blob:test'});
 context.flushStagedPhotos=async()=>{throw Error('Bild-Upload fehlgeschlagen')};
 elements.message.value='Nachricht mit Bild';
 await elements['send-form'].onsubmit({preventDefault(){}});
 assert.equal(elements.message.value,'Nachricht mit Bild');
 assert.equal(context.stagedPhotos.length,1,'a failed upload retains its staged image');
 context.stagedPhotos.length=0;
 console.log('composer regressions PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
