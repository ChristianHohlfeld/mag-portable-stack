#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const view=read('web/app/viewer3d.js'),files=read('web/app/files.js'),
      messages=read('web/app/messages.js'),css=read('web/app/app.css');
assert(view.includes('before?.n === 2 && now?.n === 2'),'pinch must work regardless of fullscreen');
assert(!view.includes('before?.n === 2 && now?.n === 2 && free()'));
assert(view.includes('cam.zoom = clamp((cam.zoom * before.d) / now.d'));
assert(view.includes('e.preventDefault()'));
assert(css.includes('.m3 canvas') && css.includes('touch-action: none'),
  'two-finger gestures remain canvas-scoped with manual vertical scroll handling');
assert(css.includes('.m3 .stage.fs canvas') && css.includes('touch-action: none'));
assert(files.includes("fileUrl(f, '/thumb')"),'GLB thumbnail available in explorer');
assert(files.includes('glbLiveTile(f,e.target.closest'), 'WebGL fallback to failed GLB PNG');
assert(css.includes('.glb-live canvas') && css.includes('touch-action: none'), 'file-grid touch is canvas-owned; 3D viewer forwards one-finger vertical scroll');
assert(files.includes('MAG.viewer3d.attach(canvas, fileUrl(f))'),
  'interactive GLB sheet uses existing viewer');
assert(messages.includes('MAG.viewer3d.attach(canvas, fileUrl(glb))'),
  'chat card uses same viewer');
console.log('GLB: thumbnail, interactive explorer/chat, inline pinch and page scroll contracts OK');
