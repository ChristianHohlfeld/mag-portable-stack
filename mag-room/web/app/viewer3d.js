// A small WebGL2 viewer for the GLB results of photo-to-3D: drag to rotate,
// pinch or wheel to zoom, two fingers to pan, double tap to reset, fullscreen.
// No library: one mesh, one base-colour texture, unlit.
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

  function start(canvas, mesh) {
    const gl = canvas.getContext('webgl2', { antialias: true, alpha: false });
    if (!gl) throw new Error('WebGL2 fehlt auf diesem Gerät');
    const compile = (type, code) => { const s = gl.createShader(type); gl.shaderSource(s, code); gl.compileShader(s); return s; };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, '#version 300 es\nin vec3 p;in vec3 c;in vec2 t;out vec3 vc;out vec2 vt;uniform mat4 m;void main(){vc=c;vt=t;gl_Position=m*vec4(p,1.);}'));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, '#version 300 es\nprecision mediump float;in vec3 vc;in vec2 vt;uniform bool useTex;uniform sampler2D base;out vec4 o;void main(){o=vec4(useTex?texture(base,vt).rgb:vc,1.);}'));
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
    const center = min.map((v, i) => (v + max[i]) / 2), scale = 2 / Math.max(...max.map((v, i) => v - min[i]), 1e-5);
    const uniform = gl.getUniformLocation(program, 'm');
    let yaw = 0.5, pitch = 0.2, zoom = 2.3, panX = 0, panY = 0;

    function render() {
      const rect = canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = clamp(Math.round((rect.width || 600) * dpr), 1, 2400), h = clamp(Math.round((rect.height || 400) * dpr), 1, 2400);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      gl.viewport(0, 0, w, h);
      gl.clearColor(0.06, 0.065, 0.08, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cx = Math.cos(pitch), sx = Math.sin(pitch), f = 1.7, fx = f / (w / h), s = scale / zoom;
      // The GLB is Y-up already; z is squeezed so the whole model stays inside the clip volume.
      gl.uniformMatrix4fv(uniform, false, new Float32Array([
        cy * s * fx, sx * sy * s * f, sy * cx * s * 0.2, 0,
        0, cx * s * f, -sx * s * 0.2, 0,
        -sy * s * fx, sx * cy * s * f, cy * cx * s * 0.2, 0,
        -(cy * center[0] - sy * center[2]) * s * fx + panX,
        -(sx * (sy * center[0] + cy * center[2]) + cx * center[1]) * s * f + panY,
        -(sy * cx * center[0] - sx * center[1] + cy * cx * center[2]) * s * 0.2, 1]));
      gl.bindVertexArray(vao);
      if (mesh.indices) gl.drawElements(gl.TRIANGLES, count, mesh.indices instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
      else gl.drawArrays(gl.TRIANGLES, 0, count);
    }
    const reset = () => { yaw = 0.5; pitch = 0.2; zoom = 2.3; panX = panY = 0; render(); };

    // Pointer gestures: one pointer rotates, two zoom and pan.
    const pointers = new Map();
    let previous = null, lastTap = 0, lastPos = null;
    const gesture = () => {
      const v = [...pointers.values()];
      if (v.length === 1) return { n: 1, x: v[0].x, y: v[0].y };
      if (v.length >= 2) return { n: 2, x: (v[0].x + v[1].x) / 2, y: (v[0].y + v[1].y) / 2, d: Math.max(1, Math.hypot(v[0].x - v[1].x, v[0].y - v[1].y)) };
      return null;
    };
    canvas.onpointerdown = (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      e.preventDefault();
      try { canvas.setPointerCapture(e.pointerId); } catch {}
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      previous = gesture();
    };
    canvas.onpointermove = (e) => {
      if (!pointers.has(e.pointerId)) return;
      e.preventDefault();
      const before = previous;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const now = gesture();
      if (before?.n === 1 && now?.n === 1) {
        yaw += (now.x - before.x) * 0.008;
        pitch = clamp(pitch + (now.y - before.y) * 0.008, -1.5, 1.5);
      } else if (before?.n === 2 && now?.n === 2) {
        zoom = clamp((zoom * before.d) / now.d, 0.5, 16);
        const rect = canvas.getBoundingClientRect();
        panX = clamp(panX + (2 * (now.x - before.x)) / Math.max(1, rect.width), -2, 2);
        panY = clamp(panY - (2 * (now.y - before.y)) / Math.max(1, rect.height), -2, 2);
      }
      previous = now;
      render();
    };
    const end = (e) => {
      if (!pointers.has(e.pointerId)) return;
      const single = pointers.size === 1;
      pointers.delete(e.pointerId);
      previous = gesture();
      if (single && e.type === 'pointerup' && e.pointerType !== 'mouse') {
        const now = Date.now();
        if (now - lastTap < 320 && lastPos && Math.hypot(e.clientX - lastPos[0], e.clientY - lastPos[1]) < 26) { reset(); lastTap = 0; }
        else { lastTap = now; lastPos = [e.clientX, e.clientY]; }
      }
    };
    canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = end;
    canvas.ondblclick = (e) => { e.preventDefault(); reset(); };
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoom = clamp(zoom * Math.exp(e.deltaY * 0.001), 0.5, 16); render(); }, { passive: false });
    new ResizeObserver(render).observe(canvas);
    canvas.addEventListener('webglcontextrestored', () => MAG.viewer3d.attach(canvas, canvas.dataset.url, true));
    render();
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
      canvas.replaceWith(MAG.el('div', { class: 'note err', text: '3D-Vorschau: ' + err.message }));
    }
  }

  MAG.viewer3d = {
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
