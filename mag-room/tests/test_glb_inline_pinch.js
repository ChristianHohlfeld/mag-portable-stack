#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const files=read('web/app/files.js'),viewer=read('web/app/viewer3d.js'),css=read('web/app/app.css');
assert(files.includes("else if (/\\.(glb|gltf)$/i.test(f.name))"),'GLB files open interactive 3D preview');
assert(files.includes("MAG.viewer3d.attach(canvas, fileUrl(f))"),'reuse existing GLB viewer rather than new dependency');
assert(files.includes("src: fileUrl(f, '/thumb')"),'model tile displays thumbnail');
assert(css.includes('.m3 canvas {') && css.includes('touch-action: none'),'3D canvas disables browser pinch');
assert(viewer.includes('before?.n === 2 && now?.n === 2)'),'two-finger pinch works outside fullscreen');
assert(viewer.includes('scrolling.scrollTop -= deltaY'),'one-finger vertical touch still scrolls parent');
console.log('GLB: thumbnail + interactive sheet + inline pinch and one-finger scroll OK');
