// A small WebGL2 viewer for the GLB results of photo-to-3D: an orbit camera
// with inertia, pinch or wheel zoom and two-finger pan in fullscreen, double
// tap to reset. No library: one mesh, one base-colour texture.
'use strict';
(function () {
  const { MAG } = window;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  async function load(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('Modell nicht ladbar (' + res.status + ')');
    const buffer = await res.arrayBuffer(), view = new DataView(buffer);
    if (view.getUint32(0, true) !== 0x46546c67) throw new Error('Keine GLB-Datei');
    let offset = 12, json, bin;
    while (offset + 8 <= buffer.byteLength) {
      const size = view.getUint32(offset, true), type = view.getUint32(offset + 4, true);
      offset += 8;
      if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, offset, size)));
      if (type === 0x004e4942) bin = new Uint8Array(buffer, offset, size);
      offset += size;
    }
    const prim = json?.meshes?.[0]?.primitives?.[0];
    if (!prim || !bin || (prim.mode !== undefined && prim.mode !== 4)) throw new Error('Geometrie fehlt');
    const accessor = (id) => {
      const a = json.accessors[id], b = json.bufferViews[a.bufferView];
      const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
      const Type = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array }[a.componentType];
      const start = (b.byteOffset || 0) + (a.byteOffset || 0), stride = b.byteStride || width * Type.BYTES_PER_ELEMENT;
      const out = new Type(a.count * width), dv = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
      const read = Type === Float32Array ? 'getFloat32' : Type === Uint32Array ? 'getUint32' : Type === Uint16Array ? 'getUint16' : 'getUint8';
      for (let i = 0; i < a.count; i++)
        for (let j = 0; j < width; j++) out[i * width + j] = dv[read](start + i * stride + j * Type.BYTES_PER_ELEMENT, true);
      return { data: out, width, type: a.componentType };
    };
    const pos = accessor(prim.attributes.POSITION).data;
    const col = prim.attributes.COLOR_0 !== undefined ? accessor(prim.attributes.COLOR_0) : null;
    const colors = new Float32Array(pos.length);
    for (let i = 0; i < pos.length / 3; i++)
      for (let j = 0; j < 3; j++) colors[i * 3 + j] = col ? (col.type === 5121 ? col.data[i * col.width + j] / 255 : col.data[i * col.width + j]) : 0.76;
    const uv = prim.attributes.TEXCOORD_0 !== undefined ? accessor(prim.attributes.TEXCOORD_0).data : null;
    let indices = prim.indices !== undefined ? accessor(prim.indices).data : null;
    if (indices instanceof Uint8Array) indices = new Uint16Array(indices);
    let image = null;
    const tex = json.materials?.[prim.material ?? 0]?.pbrMetallicRoughness?.baseColorTexture?.index;
    const def = tex === undefined ? null : json.images?.[json.textures?.[tex]?.source];
    if (uv && def && def.bufferView !== undefined) {
      const bv = json.bufferViews[def.bufferView], start = bv.byteOffset || 0;
      image = await createImageBitmap(new Blob([bin.slice(start, start + bv.byteLength)], { type: def.mimeType || 'image/png' }));
    }
    return { pos, colors, uv, indices, image };
  }

  // 4x4 matrices, column-major like WebGL.
  const mul = (a, b) => {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++)
      for (let r = 0; r < 4; r++) o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    return o;
  };

  // Orbit camera around the model: a real perspective, turning with inertia,
  // a slow turn until the first touch. In the timeline a finger turns the
  // model sideways and the page still scrolls; fullscreen gives every axis,
  // pinch zoom and two-finger pan.
  function start(canvas, mesh) {
    const gl = canvas.getContext('webgl2', { antialias: true, alpha: true });
    if (!gl) throw new Error('WebGL2 fehlt auf diesem Gerät');
    const compile = (type, code) => { const s = gl.createShader(type); gl.shaderSource(s, code); gl.compileShader(s); return s; };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER,
      '#version 300 es\nin vec3 p;in vec3 c;in vec2 t;out vec3 vc;out vec2 vt;out vec3 vp;uniform mat4 mv;uniform mat4 pr;' +
      'void main(){vc=c;vt=t;vec4 e=mv*vec4(p,1.);vp=e.xyz;gl_Position=pr*e;}'));
    // Unlit (the texture is baked colour); a little facing-ratio shade gives the shape depth.
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER,
      '#version 300 es\nprecision highp float;in vec3 vc;in vec2 vt;in vec3 vp;uniform bool useTex;uniform sampler2D base;out vec4 o;' +
      'void main(){vec3 n=normalize(cross(dFdx(vp),dFdy(vp)));vec3 col=useTex?texture(base,vt).rgb:vc;o=vec4(col*(0.84+0.16*abs(n.z)),1.);}'));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Shader');
    gl.useProgram(program);
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const attribute = (name, values, size) => {
      const loc = gl.getAttribLocation(program, name);
      if (loc < 0) return;
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, values, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    };
    attribute('p', mesh.pos, 3);
    attribute('c', mesh.colors, 3);
    attribute('t', mesh.uv || new Float32Array((mesh.pos.length / 3) * 2), 2);
    const textured = !!(mesh.uv && mesh.image);
    gl.uniform1i(gl.getUniformLocation(program, 'useTex'), textured ? 1 : 0);
    if (textured) {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, mesh.image);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.uniform1i(gl.getUniformLocation(program, 'base'), 0);
    }
    let count = mesh.pos.length / 3;
    if (mesh.indices) {
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
      count = mesh.indices.length;
    }
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < mesh.pos.length; i += 3)
      for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], mesh.pos[i + k]); max[k] = Math.max(max[k], mesh.pos[i + k]); }
    const center = min.map((v, i) => (v + max[i]) / 2), unit = 2 / Math.max(...max.map((v, i) => v - min[i]), 1e-5);
    const radius = Math.max(0.5, (Math.hypot(...max.map((v, i) => v - min[i])) / 2) * unit); // of the sphere around the model
    const uMv = gl.getUniformLocation(program, 'mv'), uPr = gl.getUniformLocation(program, 'pr');
    const home = { yaw: 0.6, pitch: 0.22, zoom: 1, panX: 0, panY: 0 };
    const cam = { ...home };
    let spin = 0.35;            // rad/s until the first touch
    let velYaw = 0, velPitch = 0, visible = true, frame = 0, last = 0, lost = false;
    const free = () => !!canvas.closest('.stage')?.classList.contains('fs');

    function draw() {
      const rect = canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = clamp(Math.round((rect.width || 600) * dpr), 1, 2600), h = clamp(Math.round((rect.height || 400) * dpr), 1, 2600);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      const aspect = w / h, f = 1 / Math.tan((34 * Math.PI) / 360);
      // Far enough to hold the whole model at any angle, also in a narrow portrait frame.
      const half = Math.atan(Math.min(1, aspect) / f);
      const dist = (cam.zoom * 1.06 * radius) / Math.sin(half), near = Math.max(0.03, dist - 2.5 * radius), far = dist + 2.5 * radius;
      const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cx = Math.cos(cam.pitch), sx = Math.sin(cam.pitch);
      const model = new Float32Array([unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, unit, 0, -center[0] * unit, -center[1] * unit, -center[2] * unit, 1]);
      const ry = new Float32Array([cy, 0, -sy, 0, 0, 1, 0, 0, sy, 0, cy, 0, 0, 0, 0, 1]);
      const rx = new Float32Array([1, 0, 0, 0, 0, cx, sx, 0, 0, -sx, cx, 0, 0, 0, 0, 1]);
      const move = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, cam.panX, cam.panY, -dist, 1]);
      gl.uniformMatrix4fv(uMv, false, mul(move, mul(rx, mul(ry, model))));
      gl.uniformMatrix4fv(uPr, false, new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0]));
      gl.bindVertexArray(vao);
      if (mesh.indices) gl.drawElements(gl.TRIANGLES, count, mesh.indices instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
      else gl.drawArrays(gl.TRIANGLES, 0, count);
    }
    // Draws while something moves; otherwise the viewer is idle.
    function tick(now) {
      frame = 0;
      if (lost) return;
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
      last = now;
      const held = pointers.size > 0;
      if (!held) {
        cam.yaw += (spin + velYaw) * dt;
        cam.pitch = clamp(cam.pitch + velPitch * dt, -1.45, 1.45);
        const drag = Math.pow(0.04, dt); // inertia fades within about a second
        velYaw *= drag;
        velPitch *= drag;
        if (Math.abs(velYaw) < 0.01) velYaw = 0;
        if (Math.abs(velPitch) < 0.01) velPitch = 0;
      }
      draw();
      if (visible && (held || spin || velYaw || velPitch)) wake();
      else last = 0;
    }
    function wake() { if (!frame && !lost) frame = requestAnimationFrame(tick); }
    const reset = () => { Object.assign(cam, home); velYaw = velPitch = 0; spin = 0.35; wake(); };

    const pointers = new Map();
    let previous = null, lastTap = 0, lastPos = null, moved = 0;
    const gesture = () => {
      const v = [...pointers.values()];
      if (v.length === 1) return { n: 1, x: v[0].x, y: v[0].y, t: performance.now() };
      if (v.length >= 2) return { n: 2, x: (v[0].x + v[1].x) / 2, y: (v[0].y + v[1].y) / 2, d: Math.max(1, Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y)), t: performance.now() };
      return null;
    };
    canvas.onpointerdown = (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      try { canvas.setPointerCapture(e.pointerId); } catch {}
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      previous = gesture();
      spin = velYaw = velPitch = 0;
      moved = 0;
      canvas.closest('.stage')?.classList.add('touched');
      wake();
    };
    canvas.onpointermove = (e) => {
      if (!pointers.has(e.pointerId)) return;
      const before = previous;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const now = gesture();
      if (before?.n === 1 && now?.n === 1) {
        const dt = Math.max(0.004, (now.t - before.t) / 1000), dx = (now.x - before.x) * 0.0085, dy = (now.y - before.y) * 0.0085;
        // In the timeline a touch turns sideways only: up and down belongs to the page.
        const tilt = free() || e.pointerType === 'mouse';
        cam.yaw += dx;
        velYaw = dx / dt;
        if (tilt) { cam.pitch = clamp(cam.pitch + dy, -1.45, 1.45); velPitch = dy / dt; }
        moved += Math.abs(now.x - before.x) + Math.abs(now.y - before.y);
      } else if (before?.n === 2 && now?.n === 2 && free()) {
        cam.zoom = clamp((cam.zoom * before.d) / now.d, 0.25, 4);
        const rect = canvas.getBoundingClientRect();
        cam.panX = clamp(cam.panX + (3 * cam.zoom * (now.x - before.x)) / Math.max(1, rect.height), -3, 3);
        cam.panY = clamp(cam.panY - (3 * cam.zoom * (now.y - before.y)) / Math.max(1, rect.height), -3, 3);
        velYaw = velPitch = 0;
      }
      previous = now;
      wake();
    };
    const end = (e) => {
      if (!pointers.has(e.pointerId)) return;
      const single = pointers.size === 1;
      pointers.delete(e.pointerId);
      previous = gesture();
      if (e.type !== 'pointerup' || performance.now() - (previous?.t || 0) > 0) { /* velocity stays as measured */ }
      if (single && e.type === 'pointerup' && moved < 8) {
        velYaw = velPitch = 0;
        const now = Date.now();
        if (now - lastTap < 320 && lastPos && Math.hypot(e.clientX - lastPos[0], e.clientY - lastPos[1]) < 26) { reset(); lastTap = 0; }
        else { lastTap = now; lastPos = [e.clientX, e.clientY]; }
      }
      wake();
    };
    canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = end;
    // The wheel zooms in fullscreen; in the timeline it scrolls the chat.
    canvas.addEventListener('wheel', (e) => {
      if (!free()) return;
      e.preventDefault();
      cam.zoom = clamp(cam.zoom * Math.exp(e.deltaY * 0.0012), 0.25, 4);
      wake();
    }, { passive: false });
    new ResizeObserver(() => { draw(); }).observe(canvas);
    if ('IntersectionObserver' in window) new IntersectionObserver((entries) => { visible = entries[entries.length - 1].isIntersecting; if (visible) wake(); }).observe(canvas);
    canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); lost = true; });
    canvas.addEventListener('webglcontextrestored', () => MAG.viewer3d.attach(canvas, canvas.dataset.url, true));
    canvas._viewer = { reset };
    canvas.closest('.stage')?.classList.add('ready');
    wake();
  }

  // Loads the model when its canvas scrolls into view, not before.
  const watcher = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      watcher.unobserve(entry.target);
      boot(entry.target);
    }
  }, { rootMargin: '300px' }) : null;
  async function boot(canvas) {
    try {
      start(canvas, await load(canvas.dataset.url));
    } catch (err) {
      canvas.closest('.stage')?.classList.add('ready');
      canvas.replaceWith(MAG.el('div', { class: 'note err', text: '3D-Vorschau: ' + err.message }));
    }
  }

  MAG.viewer3d = {
    reset(canvas) { canvas._viewer?.reset(); },
    attach(canvas, url, now) {
      canvas.dataset.url = url;
      if (now || !watcher) boot(canvas);
      else watcher.observe(canvas);
    },
    // Fullscreen: the native API where it exists, a fixed overlay elsewhere
    // (iOS Safari). The back gesture closes it.
    fullscreen(stage) {
      const on = !stage.classList.contains('fs');
      const leave = () => {
        stage.classList.remove('fs');
        document.body.style.overflow = '';
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        window.removeEventListener('popstate', leave);
        document.removeEventListener('keydown', key);
      };
      const key = (e) => e.key === 'Escape' && (history.state?.mag3d ? history.back() : leave());
      if (!on) return history.state?.mag3d ? history.back() : leave();
      stage.classList.add('fs');
      document.body.style.overflow = 'hidden';
      stage.requestFullscreen?.({ navigationUI: 'hide' }).catch(() => {});
      history.pushState({ mag3d: true }, '');
      window.addEventListener('popstate', leave);
      document.addEventListener('keydown', key);
      stage.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && stage.classList.contains('fs') && history.state?.mag3d) history.back(); }, { once: true });
    },
  };
})();
