// A small WebGL2 viewer for the 3D results in the chat: an orbit camera with
// inertia, pinch or wheel zoom and two-finger pan in fullscreen, double tap
// to reset. No library. It shows the model of photo-to-3D (one mesh, one
// base-colour texture) and plays a scene: several figures on a ground, each
// moved by the animation of the file (as a whole, and limb by limb where it
// has a skeleton), with the camera staying on them.
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
    if (!json || !bin) throw new Error('Geometrie fehlt');
    const types = { 5120: [Int8Array, 'getInt8'], 5121: [Uint8Array, 'getUint8'], 5122: [Int16Array, 'getInt16'], 5123: [Uint16Array, 'getUint16'], 5125: [Uint32Array, 'getUint32'], 5126: [Float32Array, 'getFloat32'] };
    const accessor = (id) => {
      const a = json.accessors[id], b = json.bufferViews[a.bufferView];
      const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[a.type];
      const [Type, read] = types[a.componentType];
      const start = (b.byteOffset || 0) + (a.byteOffset || 0), stride = b.byteStride || width * Type.BYTES_PER_ELEMENT;
      const out = new Type(a.count * width), dv = new DataView(bin.buffer, bin.byteOffset, bin.byteLength);
      for (let i = 0; i < a.count; i++)
        for (let j = 0; j < width; j++) out[i * width + j] = dv[read](start + i * stride + j * Type.BYTES_PER_ELEMENT, true);
      return { data: out, width, type: a.componentType };
    };
    const pictures = new Map(); // image index -> bitmap, decoded once
    const picture = (index) => {
      const def = json.images?.[index];
      if (!def || def.bufferView === undefined) return null;
      if (!pictures.has(index)) {
        const bv = json.bufferViews[def.bufferView], start = bv.byteOffset || 0;
        pictures.set(index, createImageBitmap(new Blob([bin.slice(start, start + bv.byteLength)], { type: def.mimeType || 'image/png' })));
      }
      return pictures.get(index);
    };

    // The nodes of the scene, parents before their children.
    const nodes = (json.nodes || []).map((n) => ({ t: n.translation || [0, 0, 0], r: n.rotation || [0, 0, 0, 1], s: n.scale || [1, 1, 1],
      fixed: n.matrix ? new Float32Array(n.matrix) : null, parent: -1, kids: n.children || [], mesh: n.mesh, skin: n.skin, moves: false }));
    nodes.forEach((n, i) => n.kids.forEach((k) => { if (nodes[k]) nodes[k].parent = i; }));
    const order = [];
    const visit = (i) => { if (nodes[i] && !order.includes(i)) { order.push(i); nodes[i].kids.forEach(visit); } };
    (json.scenes?.[json.scene ?? 0]?.nodes ?? nodes.map((n, i) => i).filter((i) => nodes[i].parent < 0)).forEach(visit);

    const draws = [];
    for (const index of order)
      for (const prim of json.meshes?.[nodes[index].mesh]?.primitives || []) {
        if ((prim.mode !== undefined && prim.mode !== 4) || prim.attributes?.POSITION === undefined) continue;
        const pos = accessor(prim.attributes.POSITION).data;
        const col = prim.attributes.COLOR_0 !== undefined ? accessor(prim.attributes.COLOR_0) : null;
        const colors = new Float32Array(pos.length).fill(1);
        if (col) {
          const full = col.type === 5121 ? 255 : col.type === 5123 ? 65535 : 1;
          for (let i = 0; i < pos.length / 3; i++)
            for (let j = 0; j < 3; j++) colors[i * 3 + j] = col.data[i * col.width + j] / full;
        }
        const uv = prim.attributes.TEXCOORD_0 !== undefined ? accessor(prim.attributes.TEXCOORD_0).data : null;
        let indices = prim.indices !== undefined ? accessor(prim.indices).data : null;
        if (indices instanceof Uint8Array) indices = new Uint16Array(indices);
        const pbr = json.materials?.[prim.material ?? 0]?.pbrMetallicRoughness || {};
        const tex = pbr.baseColorTexture?.index;
        const image = uv && tex !== undefined ? await picture(json.textures?.[tex]?.source) : null;
        // A plain colour of the material (the ground of a scene); bare geometry is grey.
        const tint = pbr.baseColorFactor ? pbr.baseColorFactor.slice(0, 3).map((v) => Math.pow(clamp(v, 0, 1), 1 / 2.2)) : image || col ? [1, 1, 1] : [0.76, 0.76, 0.76];
        const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
        for (let i = 0; i < pos.length; i += 3)
          for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], pos[i + k]); max[k] = Math.max(max[k], pos[i + k]); }
        // A skeleton: every vertex follows up to four bones.
        let skin = null;
        const bound = json.skins?.[nodes[index].skin];
        if (bound && bound.joints?.length <= BONES && bound.inverseBindMatrices !== undefined && prim.attributes.JOINTS_0 !== undefined && prim.attributes.WEIGHTS_0 !== undefined) {
          const weights = accessor(prim.attributes.WEIGHTS_0), full = weights.type === 5121 ? 255 : weights.type === 5123 ? 65535 : 1;
          skin = { joints: bound.joints, inverse: accessor(bound.inverseBindMatrices).data, bones: new Float32Array(accessor(prim.attributes.JOINTS_0).data),
            weights: full === 1 ? weights.data : Float32Array.from(weights.data, (v) => v / full) };
        }
        draws.push({ node: index, pos, colors, uv, indices, image, tint, min, max, skin });
      }
    if (!draws.length) throw new Error('Geometrie fehlt');

    // Every channel of every animation plays together, as one film.
    const clips = [];
    let duration = 0;
    for (const anim of json.animations || [])
      for (const channel of anim.channels || []) {
        const sampler = anim.samplers?.[channel.sampler], node = nodes[channel.target?.node], path = channel.target?.path;
        if (!sampler || !node || !['translation', 'rotation', 'scale'].includes(path)) continue;
        const times = accessor(sampler.input).data, width = path === 'rotation' ? 4 : 3;
        let values = accessor(sampler.output).data;
        if (sampler.interpolation === 'CUBICSPLINE') { // the values without their tangents
          const plain = new Float32Array(times.length * width);
          for (let k = 0; k < times.length; k++)
            for (let j = 0; j < width; j++) plain[k * width + j] = values[(k * 3 + 1) * width + j];
          values = plain;
        }
        if (!times.length || values.length < times.length * width) continue;
        clips.push({ node: channel.target.node, path, times, values, width, step: sampler.interpolation === 'STEP' });
        node.moves = true;
        duration = Math.max(duration, times[times.length - 1]);
      }
    for (const index of order) if (nodes[index].parent >= 0 && nodes[nodes[index].parent].moves) nodes[index].moves = true;
    return { nodes, order, draws, clips, duration };
  }

  // 4x4 matrices, column-major like WebGL.
  const mul = (a, b) => {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++)
      for (let r = 0; r < 4; r++) o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    return o;
  };
  const place = (t, q, s) => {
    const [x, y, z, w] = q, xx = x * x, yy = y * y, zz = z * z, xy = x * y, xz = x * z, yz = y * z, wx = w * x, wy = w * y, wz = w * z;
    return new Float32Array([
      (1 - 2 * (yy + zz)) * s[0], 2 * (xy + wz) * s[0], 2 * (xz - wy) * s[0], 0,
      2 * (xy - wz) * s[1], (1 - 2 * (xx + zz)) * s[1], 2 * (yz + wx) * s[1], 0,
      2 * (xz + wy) * s[2], 2 * (yz - wx) * s[2], (1 - 2 * (xx + yy)) * s[2], 0,
      t[0], t[1], t[2], 1]);
  };
  const identity = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  const BONES = 40; // of one figure

  // Orbit camera around the model: a real perspective, turning with inertia.
  // A model turns slowly until the first touch; a scene plays instead. In the
  // timeline a finger turns sideways and the page still scrolls; fullscreen
  // gives every axis, pinch zoom and two-finger pan.
  function start(canvas, scene) {
    const gl = canvas.getContext('webgl2', { antialias: true, alpha: true });
    if (!gl) throw new Error('WebGL2 fehlt auf diesem Gerät');
    const compile = (type, code) => { const s = gl.createShader(type); gl.shaderSource(s, code); gl.compileShader(s); return s; };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER,
      '#version 300 es\nin vec3 p;in vec3 c;in vec2 t;in vec4 j;in vec4 g;out vec3 vc;out vec2 vt;out vec3 vp;out vec3 vw;uniform mat4 wm;uniform mat4 vm;uniform mat4 pr;' +
      'uniform bool skinned;uniform mat4 bones[' + BONES + '];' +
      'void main(){vc=c;vt=t;mat4 m=wm;if(skinned)m=g.x*bones[int(j.x)]+g.y*bones[int(j.y)]+g.z*bones[int(j.z)]+g.w*bones[int(j.w)];' +
      'vec4 w=m*vec4(p,1.);vw=w.xyz;vec4 e=vm*w;vp=e.xyz;gl_Position=pr*e;}'));
    // Unlit (the texture is baked colour); a little facing-ratio shade gives the shape depth.
    // On the ground of a scene every figure casts a soft shadow of its own outline:
    // blob = where it stands and how dark, axes = the ground under it as a unit circle.
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER,
      '#version 300 es\nprecision highp float;in vec3 vc;in vec2 vt;in vec3 vp;in vec3 vw;uniform bool useTex;uniform sampler2D base;uniform vec3 tint;' +
      'uniform int blobs;uniform vec4 blob[8];uniform vec4 axes[8];out vec4 o;' +
      'void main(){vec3 n=normalize(cross(dFdx(vp),dFdy(vp)));vec3 col=(useTex?texture(base,vt).rgb:vc)*tint;float dark=0.;' +
      'for(int i=0;i<8;i++){if(i>=blobs)break;vec2 d=vw.xz-blob[i].xy;' +
      'dark=max(dark,blob[i].z*(1.-smoothstep(.25,1.,length(vec2(dot(d,axes[i].xy),dot(d,axes[i].zw))))));}' +
      'o=vec4(col*(0.84+0.16*abs(n.z))*(1.-dark),1.);}'));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Shader');
    gl.useProgram(program);
    const uniform = (name) => gl.getUniformLocation(program, name);
    const uWm = uniform('wm'), uVm = uniform('vm'), uPr = uniform('pr'), uTex = uniform('useTex'), uTint = uniform('tint'), uBlobs = uniform('blobs'), uBlob = uniform('blob'), uAxes = uniform('axes'), uSkinned = uniform('skinned'), uBones = uniform('bones');
    gl.uniform1i(uniform('base'), 0);
    const textures = new Map(); // bitmap -> texture
    const texture = (image) => {
      if (textures.has(image)) return textures.get(image);
      const made = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, made);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.generateMipmap(gl.TEXTURE_2D);
      textures.set(image, made);
      return made;
    };
    const items = scene.draws.map((mesh) => {
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
      if (mesh.skin) { attribute('j', mesh.skin.bones, 4); attribute('g', mesh.skin.weights, 4); }
      let count = mesh.pos.length / 3;
      if (mesh.indices) {
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
        count = mesh.indices.length;
      }
      // A figure on a skeleton is where its first bone has taken it; anything else where its node is.
      const node = scene.nodes[mesh.node], root = mesh.skin ? scene.nodes[mesh.skin.joints[0]] : null;
      const at = () => (root ? mul(root.world || identity, mesh.skin.inverse.subarray(0, 16)) : node.world || identity);
      return { vao, count, mesh, node, at, moves: root ? root.moves : node.moves, texture: mesh.uv && mesh.image ? texture(mesh.image) : null };
    });
    gl.bindVertexArray(null);

    // ---- the pose at a moment of the film -----------------------------------
    const cursors = scene.clips.map(() => 0);
    function pose(time) {
      scene.clips.forEach((clip, n) => {
        const { times, values, width } = clip, last = times.length - 1;
        let k = cursors[n];
        if (k > last || times[k] > time) k = 0;
        while (k < last && times[k + 1] <= time) k++;
        cursors[n] = k;
        const next = Math.min(last, k + 1), span = times[next] - times[k];
        const f = clip.step || span <= 0 ? 0 : clamp((time - times[k]) / span, 0, 1);
        const out = new Array(width);
        // A rotation takes the short way round.
        let sign = 1;
        if (width === 4) {
          let dot = 0;
          for (let j = 0; j < 4; j++) dot += values[k * 4 + j] * values[next * 4 + j];
          if (dot < 0) sign = -1;
        }
        for (let j = 0; j < width; j++) out[j] = values[k * width + j] * (1 - f) + sign * values[next * width + j] * f;
        if (width === 4) { const len = Math.hypot(...out) || 1; for (let j = 0; j < 4; j++) out[j] /= len; }
        scene.nodes[clip.node][clip.path === 'translation' ? 't' : clip.path === 'rotation' ? 'r' : 's'] = out;
      });
      for (const index of scene.order) {
        const node = scene.nodes[index], local = node.fixed || place(node.t, node.r, node.s);
        node.world = node.parent >= 0 ? mul(scene.nodes[node.parent].world, local) : local;
      }
    }
    // The box around a set of draws as they stand now.
    function bounds(list) {
      const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
      for (const item of list) {
        const { min, max } = item.mesh, m = item.at();
        for (let corner = 0; corner < 8; corner++) {
          const x = corner & 1 ? max[0] : min[0], y = corner & 2 ? max[1] : min[1], z = corner & 4 ? max[2] : min[2];
          for (let k = 0; k < 3; k++) {
            const v = m[k] * x + m[4 + k] * y + m[8 + k] * z + m[12 + k];
            lo[k] = Math.min(lo[k], v);
            hi[k] = Math.max(hi[k], v);
          }
        }
      }
      return { lo, hi };
    }

    // ---- what the camera looks at -------------------------------------------
    // A model: its box. A scene: the figures that move, moment by moment, so
    // the view stays on them and comes closer when they do (the ground is
    // only what they stand on).
    const playable = scene.duration > 0;
    const actors = items.filter((item) => item.moves), cast = playable && actors.length ? actors : items;
    const floor = playable ? items.filter((item) => !item.moves) : [];
    pose(0);
    const whole = bounds(items), size = whole.hi.map((v, i) => v - whole.lo[i]);
    const framed = bounds(cast), unit = 2 / Math.max(...framed.hi.map((v, i) => v - framed.lo[i]), 1e-5);
    const reach = Math.max(0.5, (Math.hypot(...size) / 2) * unit); // of the sphere around everything
    const track = [];
    const stops = playable ? Math.max(2, Math.ceil(scene.duration * 12)) : 1;
    let travel = [0, 0];
    for (let k = 0; k < stops; k++) {
      pose(playable ? (scene.duration * k) / (stops - 1) : 0);
      const { lo, hi } = bounds(cast);
      track.push({ c: lo.map((v, i) => (v + hi[i]) / 2), r: Math.max(playable ? 0.3 : 0.5, (Math.hypot(...hi.map((v, i) => v - lo[i])) / 2) * unit) });
      if (playable && (k === 0 || k === stops - 1))
        for (const item of actors) { const m = item.at(); travel[0] += (k ? 1 : -1) * m[12]; travel[1] += (k ? 1 : -1) * m[14]; }
    }
    // A calm camera: each moment is the average of about a second around it.
    const calm = track.map((_, k) => {
      const c = [0, 0, 0];
      let r = 0, n = 0;
      for (let j = Math.max(0, k - 7); j <= Math.min(stops - 1, k + 7); j++, n++) { r += track[j].r; for (let i = 0; i < 3; i++) c[i] += track[j].c[i]; }
      return { c: c.map((v) => v / n), r: r / n };
    });
    const focus = (time) => {
      if (stops === 1) return calm[0];
      const at = clamp(time / scene.duration, 0, 1) * (stops - 1), k = Math.min(stops - 2, Math.floor(at)), f = at - k;
      return { c: calm[k].c.map((v, i) => v * (1 - f) + calm[k + 1].c[i] * f), r: calm[k].r * (1 - f) + calm[k + 1].r * f };
    };

    // A scene is seen from the side its figures move along, from where the
    // photo was taken (-X); a model from the front quarter, as ever.
    let side = Math.hypot(...travel) * unit > 0.05 ? Math.atan2(travel[1], travel[0]) : Math.PI / 2;
    if (Math.sin(side) < -1e-6) side += Math.PI;
    const home = playable ? { yaw: side + 0.3, pitch: 0.2, zoom: 1, panX: 0, panY: 0 } : { yaw: 0.6, pitch: 0.22, zoom: 1, panX: 0, panY: 0 };
    const cam = { ...home };
    const turn = playable ? 0 : 0.35; // rad/s until the first touch
    let spin = turn;
    let velYaw = 0, velPitch = 0, visible = true, frame = 0, last = 0, lost = false;
    let playing = playable, clock = 0;
    const stage = () => canvas.closest('.stage');
    const free = () => !!stage()?.classList.contains('fs');
    const rest = 0.9; // the last picture stays for a moment before the film starts again

    function draw() {
      const rect = canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = clamp(Math.round((rect.width || 600) * dpr), 1, 2600), h = clamp(Math.round((rect.height || 400) * dpr), 1, 2600);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      const time = playable ? Math.min(scene.duration, clock % (scene.duration + rest)) : 0;
      if (playable) pose(time);
      const { c: center, r: radius } = focus(time);
      const aspect = w / h, f = 1 / Math.tan((34 * Math.PI) / 360);
      // Far enough to hold the whole model at any angle, also in a narrow portrait frame.
      const half = Math.atan(Math.min(1, aspect) / f);
      const dist = (cam.zoom * 1.06 * radius) / Math.sin(half), near = Math.max(0.03, dist - 2.5 * reach), far = dist + 2.5 * reach;
      const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cx = Math.cos(cam.pitch), sx = Math.sin(cam.pitch);
      const model = new Float32Array([unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, unit, 0, -center[0] * unit, -center[1] * unit, -center[2] * unit, 1]);
      const ry = new Float32Array([cy, 0, -sy, 0, 0, 1, 0, 0, sy, 0, cy, 0, 0, 0, 0, 1]);
      const rx = new Float32Array([1, 0, 0, 0, 0, cx, sx, 0, 0, -sx, cx, 0, 0, 0, 0, 1]);
      const move = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, cam.panX, cam.panY, -dist, 1]);
      gl.uniformMatrix4fv(uVm, false, mul(move, mul(rx, mul(ry, model))));
      gl.uniformMatrix4fv(uPr, false, new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0]));
      // Where each figure stands, for its shadow on the ground: as long and as
      // wide as the figure, fainter while it is in the air.
      const shadows = [], shapes = [];
      if (floor.length)
        for (const item of actors.slice(0, 8)) {
          const m = item.at(), { min, max } = item.mesh;
          const mx = (min[0] + max[0]) / 2, mz = (min[2] + max[2]) / 2;
          const along = Math.max(1e-5, max[0] - min[0]), across = Math.max(1e-5, max[2] - min[2]);
          // Half the size of the shadow in the figure's own directions; a thin figure still has one.
          const hx = 0.62 * Math.max(along, 0.4 * across), hz = 0.62 * Math.max(across, 0.4 * along);
          const ex = m[0] * m[0] + m[2] * m[2] || 1, ez = m[8] * m[8] + m[10] * m[10] || 1;
          const tall = Math.max(1e-5, (max[1] - min[1]) * Math.hypot(m[4], m[5], m[6]));
          shadows.push(m[0] * mx + m[8] * mz + m[12], m[2] * mx + m[10] * mz + m[14], 0.36 / (1 + (4 * Math.max(0, m[13] - whole.lo[1])) / tall), 0);
          shapes.push(m[0] / ex / hx, m[2] / ex / hx, m[8] / ez / hz, m[10] / ez / hz);
        }
      for (const item of items) {
        gl.uniformMatrix4fv(uWm, false, item.node.world || identity);
        const skin = item.mesh.skin;
        gl.uniform1i(uSkinned, skin ? 1 : 0);
        if (skin) {
          // Where every bone is now, against where it was when the figure was bound to it.
          const bones = new Float32Array(16 * skin.joints.length);
          skin.joints.forEach((joint, n) => bones.set(mul(scene.nodes[joint].world || identity, skin.inverse.subarray(16 * n, 16 * n + 16)), 16 * n));
          gl.uniformMatrix4fv(uBones, false, bones);
        }
        gl.uniform1i(uTex, item.texture ? 1 : 0);
        gl.uniform3fv(uTint, item.mesh.tint);
        const shaded = shadows.length && !item.moves;
        gl.uniform1i(uBlobs, shaded ? shadows.length / 4 : 0);
        if (shaded) { gl.uniform4fv(uBlob, new Float32Array(shadows)); gl.uniform4fv(uAxes, new Float32Array(shapes)); }
        if (item.texture) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, item.texture); }
        gl.bindVertexArray(item.vao);
        if (item.mesh.indices) gl.drawElements(gl.TRIANGLES, item.count, item.mesh.indices instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
        else gl.drawArrays(gl.TRIANGLES, 0, item.count);
      }
    }
    // Draws while something moves; otherwise the viewer is idle.
    function tick(now) {
      frame = 0;
      if (lost) return;
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
      last = now;
      const held = pointers.size > 0;
      if (playing) clock += dt;
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
      if (visible && (held || playing || spin || velYaw || velPitch)) wake();
      else last = 0;
    }
    function wake() { if (!frame && !lost) frame = requestAnimationFrame(tick); }
    const show = () => stage()?.classList.toggle('paused', playable && !playing);
    const reset = () => { Object.assign(cam, home); velYaw = velPitch = 0; spin = turn; if (playable) { clock = 0; playing = true; show(); } wake(); };
    // Play or pause; at the end of the film "play" starts it again.
    const toggle = () => {
      if (!playable) return;
      playing = !playing;
      show();
      wake();
    };

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
      stage()?.classList.add('touched');
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
    canvas._viewer = { reset, toggle };
    stage()?.classList.add('ready');
    stage()?.classList.toggle('film', playable);
    show();
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
    toggle(canvas) { canvas._viewer?.toggle(); },
    attach(canvas, url, now) {
      canvas.dataset.url = url;
      if (now || !watcher) boot(canvas);
      else watcher.observe(canvas);
    },
    // A canvas that never made it into the page.
    drop(canvas) { watcher?.unobserve(canvas); },
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
