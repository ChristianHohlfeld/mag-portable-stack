// The bots' VMs, live: the browser picture and a real terminal. In each VM
// mag-browser streams its Chrome and holds a Bash PTY over an authenticated
// UDP link to the host gateway (mag-browser-webrtc); the gateway hands both to
// this page as WebRTC data channels:
//   frames   JPEG pieces, unreliable (a late frame is a dropped frame)
//   control  pointer, wheel, keys and text for the page
//   console  the PTY: terminal_open/_input/_resize up, terminal_output down
// One connection per bot serves the browser view and the terminal. Where UDP
// cannot get through (some phone networks) the browser view falls back to
// pictures over HTTPS and says so; the terminal then offers single commands.
'use strict';
(function () {
  const { MAG } = window, { el } = MAG;
  const links = new Map();      // bot id -> Link
  const terminals = new Map();  // bot id -> terminal; its scrollback outlives the panel
  const utf8 = new TextEncoder();
  const SUBSCRIBE = '{"kind":"subscribe"}', SCREEN_TIMEOUT_MS = 16000;
  const gone = (pc) => !pc || ['failed', 'disconnected', 'closed'].includes(pc.connectionState);

  // The hidden keyboard field is focused from one place: a one-finger tap
  // that did not move. A tap on the field the page has already named, or a
  // tap while no field is known yet (the phone can open its keyboard only
  // from this call), focuses it. A tap outside that field closes it. A guest
  // answer that the spot is not a field closes it. Frames, timers, state
  // that is a field, a reconnect, the page becoming visible and a card drawn
  // again do not focus it.
  function onField(point, field) {
    if (!point || !Array.isArray(field) || field.length !== 4) return null;
    const x = +field[0], y = +field[1], w = +field[2], h = +field[3];
    if (!(w > 1) || !(h > 1)) return null;
    const pad = 16;
    return point.x >= x - pad && point.y >= y - pad && point.x <= x + w + pad && point.y <= y + h + pad;
  }
  function keysAction(ev) {
    if (!ev) return 'none';
    if (ev.kind === 'state') return ev.editable === false ? 'blur' : 'none';
    if (ev.kind !== 'tap') return 'none';
    if (ev.pointerType !== 'touch' || ev.moved || ev.fingers !== 1) return 'none';
    if (ev.onField === false) return 'blur';
    return 'focus';
  }
  // Two fingers: spacing sets the zoom from the gesture's first gap, and the
  // midpoint pans in the same step once the picture is larger than the view.
  // At 1x a drag that does not spread scrolls the page instead.
  function pinchStep(pinch, dist, mx, my) {
    const next = Math.max(1, Math.min(4, pinch.s * dist / (pinch.d || 1)));
    if (next <= 1.001) return { z: 1, tx: 0, ty: 0, scroll: true };
    const u = (pinch.mx - pinch.L - pinch.tx) / (pinch.s || 1);
    const v = (pinch.my - pinch.T - pinch.ty) / (pinch.s || 1);
    return { z: next, tx: mx - pinch.L - u * next, ty: my - pinch.T - v * next, scroll: false };
  }
  // One finger pans the picture only while it is magnified.
  function fingerDrag(zoom) { return zoom > 1.001; }
  // No control channel: the HTTPS picture starts after about two seconds.
  // An open channel with no picture yet only says so; it does not poll.
  function pictureFallback(controlOpen, sinceMs) {
    if (!controlOpen && sinceMs >= 2000) return 'https';
    if (controlOpen && sinceMs >= 5000) return 'wait';
    return 'none';
  }
  MAG.liveGesture = { onField, keysAction, pinchStep, fingerDrag, pictureFallback };

  class Link {
    constructor(bot) {
      Object.assign(this, { bot, vm: '', users: 0, gen: 0, pc: null, control: null, console: null,
        screen: null, term: null, // the views attached right now
        chunks: null, painting: false, lastSeq: -1, frameAt: 0, waitAt: -1, fallbackTimer: 0,
        http: null, httpBefore: false, posts: Promise.resolve(), scroll: 0,
        pending: [], hbAt: 0, udp: false, retryTimer: 0, retryDelay: 1000, lingerTimer: 0, clock: 0 });
    }
    sayScreen(text) { this.screen?.say(text); }
    sayTerm(text) { this.term?.say(text); }

    // A view starts using the link, or is done with it. The connection lingers
    // a moment so switching between browser and terminal does not rebuild it.
    use() {
      clearTimeout(this.lingerTimer);
      this.users++;
      this.clock ||= setInterval(() => this.tick(), 2000);
      if (gone(this.pc)) this.connect();
    }
    release() {
      this.users = Math.max(0, this.users - 1);
      if (this.users) return;
      this.lingerTimer = setTimeout(() => {
        if (this.users) return;
        clearInterval(this.clock);
        this.clock = 0;
        this.pending = [];
        this.disconnect();
      }, 1500);
    }
    // Try again, later each time. Where UDP has never got through from this
    // network and the picture arrives over HTTPS anyway, there is no hurry.
    scheduleReconnect() {
      if (this.retryTimer || document.hidden || !this.users) return;
      this.retryTimer = setTimeout(() => {
        this.retryTimer = 0;
        this.retryDelay = Math.min(this.retryDelay * 2, this.udp || this.term || !this.http ? 10000 : 60000);
        if (this.users && !document.hidden) this.connect();
      }, this.retryDelay);
    }

    // Every 2 s: keep the picture subscribed (the VM stops sending 8 s after
    // the last request), notice a picture that does not come or stalls, and a
    // console whose heartbeat stopped (the gateway sends one every 2 s; without
    // it the session is dead and input would go nowhere).
    tick() {
      const now = performance.now(), open = this.control?.readyState === 'open';
      if (this.screen && !document.hidden) {
        if (open) this.control.send(SUBSCRIBE);
        if (this.chunks && now - this.chunks.stamp > 550) this.chunks = null;
        // Open control channel: a late picture is the VM being busy, so do not
        // poll screenshots. Closed channel: HTTPS starts after about two
        // seconds, so the view does not stay black on "verbinde".
        const since = this.waitAt < 0 ? 0 : now - this.waitAt;
        const fb = pictureFallback(!!open, since);
        if (fb === 'https') this.startHttp(this.frameAt ? 'UDP-Bild stockt' : 'UDP nicht erreichbar');
        else if (fb === 'wait' && !this.frameAt) this.sayScreen('WebRTC/UDP · ' + this.vm + ' · warte auf Bild');
      }
      if (this.console?.readyState === 'open' && this.hbAt && now - this.hbAt > 6000 && !document.hidden) {
        this.hbAt = 0;
        this.sayTerm('Shell-Verbindung verloren · verbinde neu …');
        this.scheduleReconnect();
      }
    }

    // ---- the browser view ---------------------------------------------------
    // The view (re)appears: ask for pictures now. A picture already on screen
    // stays; HTTPS is only for a control channel that never opens.
    wantFrames() {
      // Once. Coming back to the page must not push the HTTPS picture back.
      if (this.waitAt < 0) this.waitAt = performance.now();
      if (this.control?.readyState === 'open') this.control.send(SUBSCRIBE);
    }
    watch(screen) {
      this.screen = screen;
      this.wantFrames();
      this.use();
      // Where UDP did not get through last time, HTTPS starts at once.
      if (this.httpBefore) this.startHttp();
    }
    unwatch(screen) {
      if (this.screen === screen) { this.screen = null; this.stopHttp(); }
      this.release();
    }
    // Input for the page. Without the control channel the same input goes
    // through the room's browser routes, one request after the other.
    send(data) {
      if (this.control?.readyState === 'open') { this.control.send(JSON.stringify(data)); return true; }
      if (!this.http) return false;
      const post = (action, body) => {
        this.posts = this.posts.then(() => MAG.api('POST', '/api/browser/' + this.bot + '/' + action, body())).catch((err) => this.sayScreen('HTTPS-Rückfall: ' + err.message));
      };
      if (data.kind === 'pointer' && (data.action === 'tap' || data.action === 'up')) post('pointer', () => ({ x: data.x, y: data.y, action: 'tap', button: data.button || 'left' }));
      else if (data.kind === 'text') post('type', () => ({ text: data.text }));
      else if (data.kind === 'key') post(data.key === 'Enter' ? 'type' : 'key', () => (data.key === 'Enter' ? { text: '', enter: true } : { key: data.key }));
      else if (data.kind === 'wheel' && data.dy) {
        // One request scrolls by everything that came in while the last one ran.
        if (!this.scroll) post('scroll', () => { const dy = Math.round(this.scroll); this.scroll = 0; return { dy }; });
        this.scroll += data.dy;
      }
      return true;
    }
    // A frame arrives as up to 32 pieces: seq (u32), index (u16), total (u16),
    // then JPEG bytes. Pieces of an older frame are dropped, never waited for.
    receiveFrame(bytes) {
      if (!this.screen || document.hidden) return;
      // A Blob is not a frame yet. new Uint8Array(blob) is empty, so every
      // picture would be dropped while the control channel still says connected.
      if (typeof Blob !== 'undefined' && bytes instanceof Blob) {
        bytes.arrayBuffer().then((buf) => this.receiveFrame(buf)).catch(() => {});
        return;
      }
      const b = new Uint8Array(bytes);
      if (b.length < 8) return;
      const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
      const seq = view.getUint32(0), index = view.getUint16(4), total = view.getUint16(6), now = performance.now();
      if (!total || total > 32 || index >= total || seq <= this.lastSeq) return;
      if (this.chunks && (now - this.chunks.stamp > 550 || seq > this.chunks.seq)) this.chunks = null;
      if (!this.chunks) this.chunks = { seq, parts: Array(total), count: 0, stamp: now };
      if (this.chunks.seq !== seq || this.chunks.parts.length !== total) return;
      if (!this.chunks.parts[index]) { this.chunks.parts[index] = b.slice(8); this.chunks.count++; }
      if (this.chunks.count !== total) return;
      const parts = this.chunks.parts;
      this.chunks = null;
      if (!this.painting) void this.displayFrame(parts, seq, this.gen);
    }
    async displayFrame(parts, seq, gen) {
      if (this.painting) return;
      this.painting = true;
      let bitmap = null;
      try {
        bitmap = await createImageBitmap(new Blob(parts, { type: 'image/jpeg' }));
        if (gen !== this.gen || seq < this.lastSeq || !this.screen) return;
        this.paint(bitmap);
        this.lastSeq = seq;
        this.frameAt = performance.now();
        this.udp = true;
        this.httpBefore = false;
        if (this.http) this.stopHttp();
        this.sayScreen('WebRTC/UDP · ' + this.vm + ' · live');
      } catch (err) { this.sayScreen('Bild: ' + err.message); }
      finally { bitmap?.close(); this.painting = false; }
    }
    paint(bitmap) {
      const canvas = this.screen.canvas;
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) { canvas.width = bitmap.width; canvas.height = bitmap.height; }
      canvas.getContext('2d', { alpha: false }).drawImage(bitmap, 0, 0);
      this.screen.shown();
    }
    // Phones on mobile data or a VPN sometimes offer no usable UDP path. Then
    // the room's JPEG route keeps the view alive, across reconnects, until UDP
    // frames arrive or the view closes.
    startHttp(why) {
      if (this.http || !this.screen) return;
      const session = { timer: 0, abort: null, failures: 0 };
      why ||= 'UDP nicht erreichbar';
      this.http = session;
      this.httpBefore = true;
      this.sayScreen(why + ' · prüfe Browserbild über HTTPS …');
      const fresh = () => this.frameAt && performance.now() - this.frameAt < 5000;
      const tick = async () => {
        if (this.http !== session || fresh() || !this.screen) { if (this.http === session) this.stopHttp(); return; }
        if (document.hidden) { session.timer = setTimeout(tick, 900); return; }
        const abort = new AbortController();
        session.abort = abort;
        const deadline = setTimeout(() => abort.abort(), SCREEN_TIMEOUT_MS);
        try {
          const res = await fetch('/api/screen/' + this.bot + '?format=jpeg', { cache: 'no-store', credentials: 'same-origin', signal: abort.signal });
          if (!res.ok) throw new Error(res.status >= 502 && res.status <= 504 ? 'VM-Browser noch nicht erreichbar (HTTP ' + res.status + ')' : 'Browserbild fehlgeschlagen (HTTP ' + res.status + ')');
          const bitmap = await createImageBitmap(await res.blob());
          try {
            if (this.http === session && this.screen) {
              this.paint(bitmap);
              session.failures = 0;
              this.sayScreen('Browser über HTTPS · ' + this.vm + ' · ' + why);
            }
          } finally { bitmap.close(); }
        } catch (err) {
          if (this.http === session) {
            // Keep a picture that is already there; say what failed.
            session.failures++;
            this.sayScreen((err.name === 'AbortError' ? 'Browserbild: Antwort dauert zu lange' : err.message) + ' · erneuter Versuch');
          }
        } finally {
          clearTimeout(deadline);
          if (session.abort === abort) session.abort = null;
          if (this.http === session) session.timer = setTimeout(tick, session.failures ? 3000 : 900);
        }
      };
      void tick();
    }
    stopHttp() {
      if (!this.http) return;
      clearTimeout(this.http.timer);
      this.http.abort?.abort();
      this.http = null;
    }

    // ---- the terminal (PTY) -------------------------------------------------
    sendConsole(kind, payload) {
      if (this.console?.readyState !== 'open') return false;
      // Heartbeat overdue: the session is probably dead; keep the input instead of losing it.
      if (kind === 'terminal_input' && this.hbAt && performance.now() - this.hbAt > 3000) return false;
      this.console.send(JSON.stringify({ kind, ...(payload || {}) }));
      return true;
    }
    attachTerminal(view) { this.term = view; this.openTerminal(); }
    detachTerminal(view) { if (this.term === view) this.term = null; }
    // A terminal view attached, or the channel just opened: start the shell in
    // the VM (a running one repaints), tell it the size and hand in what was
    // typed meanwhile, unless that is long ago.
    openTerminal() {
      if (!this.term || !this.sendConsole('terminal_open')) return;
      this.term.opened();
      const queued = this.pending.filter((p) => Date.now() - p.at < 20000);
      this.pending = [];
      for (const { data } of queued) this.sendConsole('terminal_input', { data });
      this.sayTerm('Shell · ' + (MAG.bot(this.bot)?.name || this.bot) + ' · verbunden');
    }
    typed(text) {
      const bytes = utf8.encode(text);
      for (let offset = 0; offset < bytes.length; offset += 300) {
        const data = btoa(String.fromCharCode(...bytes.subarray(offset, offset + 300)));
        if (this.sendConsole('terminal_input', { data })) continue;
        if (this.pending.length < 64) this.pending.push({ data, at: Date.now() });
        this.sayTerm('Shell verbindet neu … Eingabe wird nachgereicht');
        // Still connecting is not a reason to start over.
        if (this.console?.readyState === 'open' || gone(this.pc)) this.scheduleReconnect();
      }
    }
    receiveConsole(raw) {
      let message;
      try { message = JSON.parse(raw); } catch { return; }
      if (message?.kind === 'hb') {
        this.hbAt = performance.now();
        this.udp = true;
        if (message.vm_age > 5) this.sayTerm('VM antwortet nicht (' + Math.round(message.vm_age) + ' s) · Eingabe wird ggf. verworfen');
        else if (this.term && /VM antwortet nicht|verbinde neu/.test(this.term.text())) this.sayTerm('Shell verbunden');
        return;
      }
      const entry = terminals.get(this.bot);
      if (!entry || message?.kind !== 'terminal_output' || typeof message.data !== 'string' || message.data.length > 950) return;
      try { entry.term.write(Uint8Array.from(atob(message.data), (c) => c.charCodeAt(0))); }
      catch { this.sayTerm('Terminalausgabe nicht lesbar'); }
    }

    // ---- the connection -----------------------------------------------------
    disconnect() {
      ++this.gen;
      clearTimeout(this.retryTimer);
      clearTimeout(this.fallbackTimer);
      this.retryTimer = 0;
      this.fallbackTimer = 0;
      if (this.pc) { this.pc.close(); this.pc = null; }
      Object.assign(this, { control: null, console: null, chunks: null, painting: false, lastSeq: -1, hbAt: 0 });
      this.sayTerm('Shell getrennt · Sitzung bleibt in der VM erhalten');
    }
    async connect() {
      this.disconnect();
      const gen = this.gen, id = this.bot, bot = MAG.bot(id);
      if (!bot || !bot.vm) {
        this.sayScreen('Dieser Bot hat keine VM (kein Browser, keine Konsole).');
        this.sayTerm('Keine Konsole: dieser Bot hat keine VM.');
        return;
      }
      this.vm = bot.vm;
      // About two seconds, from this attempt, not from the next time the page
      // is shown. A WebRTC frame still replaces the HTTPS picture.
      const httpSoon = () => {
        this.fallbackTimer = setTimeout(() => {
          this.fallbackTimer = 0;
          if (gen !== this.gen || this.control?.readyState === 'open' || this.http || !this.screen) return;
          if (this.frameAt && performance.now() - this.frameAt < 2000) return;
          if (document.hidden) { httpSoon(); return; }
          this.startHttp('UDP nicht erreichbar');
        }, 2000);
      };
      httpSoon();
      if (!window.RTCPeerConnection) { this.sayTerm('Dieser Browser kann kein WebRTC.'); this.startHttp('kein WebRTC'); return; }
      const pc = new RTCPeerConnection({ iceServers: [] });
      this.pc = pc;
      if (!this.http) this.sayScreen('WebRTC/UDP · verbinde ' + bot.name + ' …');
      this.sayTerm('Verbinde VM-Terminal …');
      const control = pc.createDataChannel('control', { ordered: true });
      this.control = control;
      control.onopen = () => {
        if (gen !== this.gen) return;
        clearTimeout(this.fallbackTimer);
        this.fallbackTimer = 0;
        // The channel is up: stop screenshot polling so it does not take the
        // browser's only thread. The picture already drawn stays until a frame.
        if (this.http) this.stopHttp();
        if (this.screen && !document.hidden) control.send(SUBSCRIBE);
        if (!this.frameAt) this.sayScreen('WebRTC/UDP · verbunden');
      };
      pc.ondatachannel = (e) => {
        if (e.channel.label === 'console') {
          this.console = e.channel;
          e.channel.onopen = () => {
            if (gen !== this.gen) return;
            this.retryDelay = 1000;
            this.hbAt = 0;
            this.openTerminal();
          };
          e.channel.onmessage = (m) => { if (gen === this.gen) this.receiveConsole(m.data); };
          e.channel.onclose = () => { if (gen === this.gen) { this.sayTerm('Shell getrennt · verbinde neu …'); this.scheduleReconnect(); } };
          return;
        }
        if (e.channel.label !== 'frames') return;
        e.channel.binaryType = 'arraybuffer';
        e.channel.onmessage = (m) => { if (gen === this.gen) this.receiveFrame(m.data); };
      };
      pc.onconnectionstatechange = () => {
        if (gen !== this.gen || !gone(pc)) return;
        if (!this.http) this.sayScreen('WebRTC/UDP · ' + pc.connectionState + ' · verbinde neu …');
        this.sayTerm('Shell getrennt · prüfe Verbindung');
        this.scheduleReconnect();
      };
      try {
        await pc.setLocalDescription(await pc.createOffer());
        if (pc.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
          const timer = setTimeout(() => { pc.removeEventListener('icegatheringstatechange', done); reject(new Error('ICE timeout')); }, 10000);
          function done() { if (pc.iceGatheringState === 'complete') { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', done); resolve(); } }
          pc.addEventListener('icegatheringstatechange', done);
        });
        if (gen !== this.gen) return;
        const res = await MAG.fetch('/api/browser-live/offer?bot=' + encodeURIComponent(id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pc.localDescription) });
        if (!res.ok) {
          // No JSON error means nginx answered for a room that is down or restarting.
          const message = (await res.json().catch(() => ({}))).error || (res.status >= 500 ? 'Raum nicht erreichbar' : 'Verbindung abgelehnt (' + res.status + ')');
          // A bot without a VM is not a transient failure: do not retry forever.
          throw Object.assign(new Error(message), { permanent: [400, 403, 404].includes(res.status) });
        }
        if (gen !== this.gen) return;
        await pc.setRemoteDescription(await res.json());
      } catch (err) {
        if (gen !== this.gen) return;
        if (!this.http) this.sayScreen('Browser: ' + err.message);
        this.sayTerm('Terminal: ' + err.message);
        pc.close();
        if (!err.permanent) { this.startHttp(err.message); this.scheduleReconnect(); }
      }
    }
  }
  const linkOf = (bot) => links.get(bot) || links.set(bot, new Link(bot)).get(bot);
  // A hidden page asks for no pictures, so the VM stops sending them; the
  // console stays connected where the browser lets it. Phones freeze a hidden
  // page: pick the session up again when it shows.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    for (const link of links.values()) {
      if (!link.users) continue;
      if (gone(link.pc)) link.connect();
      else if (link.screen) link.wantFrames();
    }
  });

  const fullIcon = MAG.icons.full.replace('<svg', '<svg class="to-full"') + MAG.icons.x; // enter, and leave
  // Fullscreen like the 3D viewer: the native API where it exists, a fixed
  // overlay elsewhere; the back gesture closes it. Esc stays with the VM where
  // the browser can leave it there (holding Esc still leaves).
  function fullscreen(node, changed) {
    let leave = null;
    const close = () => { if (leave) history.state?.magvm ? history.back() : leave(); };
    const enter = () => {
      let native = false;
      const watch = () => {
        if (document.fullscreenElement === node) native = true;
        else if (native) close();
      };
      leave = () => {
        leave = null;
        node.classList.remove('vm-full');
        document.removeEventListener('fullscreenchange', watch);
        window.removeEventListener('popstate', back);
        if (document.fullscreenElement === node) document.exitFullscreen().catch(() => {});
        navigator.keyboard?.unlock?.();
        changed?.(false);
      };
      const back = () => leave?.();
      node.classList.add('vm-full');
      history.pushState({ magvm: true }, '');
      window.addEventListener('popstate', back);
      document.addEventListener('fullscreenchange', watch);
      node.requestFullscreen?.({ navigationUI: 'hide' }).then(() => navigator.keyboard?.lock?.(['Escape'])).catch(() => {});
      changed?.(true);
    };
    return { toggle: () => (leave ? close() : enter()), close };
  }

  // ---- the browser of a bot's VM -------------------------------------------
  // The bot and Chris share this one browser; nothing here leaves the VM.
  MAG.liveView = function (bot) {
    const link = linkOf(bot), name = MAG.bot(bot)?.name || bot;
    const canvas = el('canvas', { class: 'screen', width: 1440, height: 900, tabIndex: 0, hidden: true, 'aria-label': 'Browser von ' + name });
    const wait = el('div', { class: 'wait', text: 'Verbinde mit dem Browser von ' + name + ' …' });
    const status = el('div', { class: 'note' });
    const address = el('input', { type: 'text', inputMode: 'url', enterKeyHint: 'go', placeholder: 'Adresse', 'aria-label': 'Adresse', autocapitalize: 'off', autocomplete: 'off' });
    // The phone's keyboard types into the page: a tap on a field of the page
    // focuses this invisible field, and what is typed in it goes on to the
    // page as it is typed (a word the keyboard puts in or takes back too).
    const keys = el('input', { type: 'text', class: 'keys', enterKeyHint: 'go', autocapitalize: 'off', autocomplete: 'off', 'aria-label': 'Tastatur für die Seite' });
    for (const input of [address, keys]) input.setAttribute('spellcheck', 'false');
    keys.setAttribute('autocorrect', 'off');
    keys.readOnly = true;
    let stopped = false, knownField = null;

    // The address follows the page, also while the bot is the one browsing.
    // Where the page's field is, so a later tap can tell a field from the rest.
    // Nothing here focuses the keyboard.
    const remember = (state) => {
      if (!state || typeof state !== 'object') return;
      if (state.editable === true && Array.isArray(state.field) && state.field.length === 4 && +state.field[2] > 1 && +state.field[3] > 1)
        knownField = state.field.map(Number);
      else if (state.editable === false || state.editable === 'maybe') knownField = null;
    };
    const shown = (state) => {
      if (!state || typeof state !== 'object' || stopped) return;
      remember(state);
      if (state.url && document.activeElement !== address) address.value = state.url;
      back.disabled = state.can_go_back === false;
      forward.disabled = state.can_go_forward === false;
    };
    let syncing = false;
    const sync = () => {
      if (document.hidden || stopped || syncing) return;
      syncing = true;
      MAG.api('POST', '/api/browser/' + bot + '/state', {}).then(shown, () => {}).finally(() => { syncing = false; });
    };
    const act = (action, body) => MAG.api('POST', '/api/browser/' + bot + '/' + action, body || {})
      .then((state) => { shown(state); setTimeout(sync, 700); }, (err) => { status.textContent = err.message; });
    const nav = (label, title, action) => el('button', { type: 'button', class: 'pill', text: label, title, 'aria-label': title, onclick: () => act(action) });
    const back = nav('←', 'Zurück', 'back'), forward = nav('→', 'Vor', 'forward');
    const go = () => {
      let url = address.value.trim();
      if (!url) return;
      if (!/^[a-z][\w+.-]*:\/\//i.test(url)) url = (/^(localhost|[\d.]+)(:\d+)?(\/|$)/i.test(url) ? 'http://' : 'https://') + url;
      address.blur();
      act('navigate', { url });
    };
    // What the keyboard changed since the last time, as keys for the page:
    // a Backspace for every character taken back, then the new text.
    let typed = '';
    const passOn = () => {
      const now = keys.value;
      let same = 0;
      while (same < typed.length && same < now.length && typed[same] === now[same]) same++;
      for (let i = typed.length; i > same; i--) link.send({ kind: 'key', key: 'Backspace' });
      if (now.length > same) link.send({ kind: 'text', text: now.slice(same) });
      typed = now;
    };
    const clearKeys = () => { keys.value = ''; typed = ''; };
    keys.addEventListener('input', passOn);
    keys.addEventListener('compositionend', () => setTimeout(() => { if (typed.length > 200) clearKeys(); }, 0));
    keys.addEventListener('keydown', (e) => {
      if (e.isComposing) return;
      const named = { Enter: 1, Tab: 1, Escape: 1, ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1 };
      // Backspace on an empty field changes nothing the field could see: the page gets it.
      if (named[e.key] || (e.key === 'Backspace' && !keys.value)) {
        e.preventDefault();
        link.send({ kind: 'key', key: e.key, shift: e.shiftKey });
        if (e.key === 'Enter' || e.key === 'Tab') clearKeys();
      }
    });
    keys.addEventListener('blur', () => { clearKeys(); if (!allowKeysFocus) keys.readOnly = true; });
    // focus() runs only while a tap (or the keyboard key) allows it. Any other
    // focus, including one the browser restores when the card is drawn again,
    // is closed before the keyboard can come up, and it is not asked for again.
    let allowKeysFocus = false, refusingKeys = false;
    const focusKeys = () => {
      if (document.activeElement === keys && !keys.readOnly) return;
      clearKeys();
      keys.readOnly = false;
      allowKeysFocus = true;
      try { keys.focus({ preventScroll: true }); }
      finally { allowKeysFocus = false; }
    };
    keys.addEventListener('focus', () => {
      if (allowKeysFocus || refusingKeys) return;
      refusingKeys = true;
      keys.blur();
      refusingKeys = false;
    });
    // The address bar is typed into only when it itself is tapped.
    let addressTap = false;
    address.addEventListener('pointerdown', () => { addressTap = true; });
    address.addEventListener('focus', () => {
      if (addressTap) { addressTap = false; return; }
      address.blur();
    });
    const keyboardFor = (state) => {
      shown(state);
      const field = state && state.editable;
      if (field) {
        const next = state.secret ? 'password' : 'text';
        if (keys.type !== next && document.activeElement === keys) {
          // Changing the type can blur and focus again. That is not a new tap,
          // and it must not be refused or the password keyboard would close.
          allowKeysFocus = true;
          try { keys.type = next; }
          finally { allowKeysFocus = false; }
        }
        // The field across the view (at most 4x), its left edge at the left, near the
        // top, so the phone keyboard does not cover it. Same zoom a pinch uses.
        if (lastTap && zoom.z < 2) {          // lastTap: a finger's, never a mouse's
          const box = Array.isArray(state.field) && state.field.length === 4 ? state.field : null;
          const vw = canvas.clientWidth || 1, vh = canvas.clientHeight || 1;
          if (box && box[2] > 8) {
            const z = Math.max(1.5, Math.min(4, (vw * 0.92) / ((box[2] / 1440) * vw)));
            zoom.z = z;
            zoom.tx = vw * 0.04 - (box[0] / 1440) * vw * z;
            zoom.ty = vh * 0.3 - ((box[1] + box[3] / 2) / 900) * vh * z;
            applyZoom();
          } else {
            zoomAt(Math.max(2, Math.min(4, 1440 / 420)), lastTap.x, lastTap.y);
            const r = canvas.parentNode.getBoundingClientRect();
            zoom.ty += r.top + r.height * 0.3 - lastTap.y;
            applyZoom();
          }
        }
      } else if (keysAction({ kind: 'state', editable: field }) === 'blur' && document.activeElement === keys) keys.blur();
    };
    // The tap reaches the page over the realtime link, the question over HTTPS:
    // asked twice, so a slow tap is not taken for one beside a field. This
    // keeps the zoom or closes the keyboard. It never focuses the field.
    const afterTap = () => {
      const ask = () => MAG.api('POST', '/api/browser/' + bot + '/state', {});
      setTimeout(() => ask().then((state) => (state && state.editable ? keyboardFor(state)
        : new Promise((ok) => setTimeout(ok, 450)).then(ask).then(keyboardFor))).catch(() => {}), 200);
    };

    // Where on the 1440 x 900 page a pointer is, whatever size the picture has here.
    const place = () => {
      const r = canvas.getBoundingClientRect(), w = canvas.width || 1440, h = canvas.height || 900, scale = Math.min(r.width / w, r.height / h);
      return { left: r.left + (r.width - w * scale) / 2, top: r.top + (r.height - h * scale) / 2, scale, w, h };
    };
    const spot = (e) => {
      const g = place(), x = (e.clientX - g.left) / g.scale, y = (e.clientY - g.top) / g.scale;
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x >= g.w || y >= g.h) return null;
      return { x: Math.min(1439, Math.max(0, (x * 1440) / g.w)), y: Math.min(899, Math.max(0, (y * 900) / g.h)) };
    };
    // One finger taps the point under it. A drag does nothing at 100%; while
    // magnified it only pans the picture. Two fingers pinch and pan together.
    // At 100% a two-finger drag that does not spread is the scroll of the page.
    // The percent button fits it.
    const zoom = { z: 1, tx: 0, ty: 0 };
    let lastTap = null;
    const lens = el('button', { type: 'button', class: 'pill lens', text: '100%', title: 'Ganze Seite', 'aria-label': 'Ganze Seite', onclick: () => {
      zoom.z = 1; zoom.tx = zoom.ty = 0; applyZoom();
    } });
    const applyZoom = () => {
      const view = canvas.parentNode, vw = view?.clientWidth || 0, vh = view?.clientHeight || 0;
      zoom.z = Math.max(1, Math.min(4, zoom.z));
      zoom.tx = Math.min(0, Math.max(vw - vw * zoom.z, zoom.tx));
      zoom.ty = Math.min(0, Math.max(vh - vh * zoom.z, zoom.ty));
      if (zoom.z <= 1.001) { zoom.z = 1; zoom.tx = zoom.ty = 0; }
      canvas.style.transform = zoom.z > 1 ? 'translate(' + zoom.tx + 'px,' + zoom.ty + 'px) scale(' + zoom.z + ')' : '';
      lens.textContent = Math.round(zoom.z * 100) + '%';
    };
    // Keep the client point (cx, cy) on the same pixel of the page. This is the
    // old view's zoom: translate then scale about the canvas origin.
    const zoomAt = (next, cx, cy) => {
      next = Math.max(1, Math.min(4, next));
      const r = canvas.getBoundingClientRect();
      const ox = (cx ?? r.left + r.width / 2) - (r.left - zoom.tx);
      const oy = (cy ?? r.top + r.height / 2) - (r.top - zoom.ty);
      zoom.tx = ox - (ox - zoom.tx) * next / zoom.z;
      zoom.ty = oy - (oy - zoom.ty) * next / zoom.z;
      zoom.z = next;
      applyZoom();
    };
    const button = (e) => (e.button === 2 ? 'right' : e.button === 1 ? 'middle' : 'left');
    let held = false, movedAt = 0, finger = null, touchTap = false, gestureFocused = false, pinched = false;
    const pts = new Map();
    let pinch = null;
    const pointer = (e, action, extra) => {
      const at = spot(e);
      if (!at) return;
      if (action === 'move') { if (performance.now() - movedAt < 55) return; movedAt = performance.now(); }
      if (!link.send({ kind: 'pointer', ...at, action, ...(extra || {}) })) status.textContent = 'Kein Steuerkanal verbunden';
    };
    // Pinch before the click handler. The second finger never becomes a tap.
    canvas.addEventListener('pointerdown', (e) => {
      if (e.pointerType !== 'touch' || e.target !== canvas) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const r = canvas.getBoundingClientRect();
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        // Anchor for the whole gesture. Spacing and the midpoint are both
        // applied on every move: pinch and pan together, with no cutoff.
        pinch = {
          d: Math.hypot(a.x - b.x, a.y - b.y) || 1, s: zoom.z, tx: zoom.tx, ty: zoom.ty,
          mx, my, sentMx: mx, sentMy: my, scrollAt: 0,
          L: r.left - zoom.tx, T: r.top - zoom.ty,
        };
        finger = null;
        held = false;
        touchTap = false;
        pinched = true;
        for (const id of pts.keys()) { try { canvas.releasePointerCapture(id); } catch {} }
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);
    canvas.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (!pinch || pts.size < 2) return;
      const [a, b] = [...pts.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      // Spacing sets the zoom on every move, from the first gap. The midpoint
      // pans at the same time once the picture is larger than the view. At
      // 100% there is nothing to pan, so a drag that does not spread scrolls.
      const step = pinchStep(pinch, dist, mx, my);
      if (step.scroll) {
        zoom.z = 1; zoom.tx = 0; zoom.ty = 0;
        applyZoom();
        if (performance.now() - pinch.scrollAt >= 30) {
          const dx = mx - pinch.sentMx, dy = my - pinch.sentMy;
          if (dx || dy) {
            const scale = place().scale || 1;
            link.send({ kind: 'wheel', dx: -dx / scale, dy: -dy / scale });
            pinch.sentMx = mx;
            pinch.sentMy = my;
            pinch.scrollAt = performance.now();
          }
        }
      } else {
        zoom.z = step.z;
        zoom.tx = step.tx;
        zoom.ty = step.ty;
        applyZoom();
      }
      e.stopPropagation();
    }, true);
    const pinchEnd = (e) => {
      if (!pts.has(e.pointerId)) return;
      const was = pinch;
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
      if (was) { finger = null; held = false; e.stopPropagation(); }
    };
    canvas.addEventListener('pointerup', pinchEnd, true);
    canvas.addEventListener('pointercancel', pinchEnd, true);
    canvas.addEventListener('pointerdown', (e) => {
      if (e.button > 2) return;
      e.preventDefault();
      canvas.focus({ preventScroll: true });
      // A touch is captured only once it is a drag. Capturing the first finger
      // on contact eats the second one, and the pinch never starts.
      if (e.pointerType !== 'touch') { try { canvas.setPointerCapture(e.pointerId); } catch {} }
      // A finger taps to click. Its drag pans the picture only while magnified.
      // A mouse presses, moves and releases.
      if (e.pointerType === 'touch') {
        // The capture listener already started a pinch: this finger is not a tap.
        if (pinch || pts.size > 1) { touchTap = false; finger = null; return; }
        touchTap = true; gestureFocused = false; pinched = false;
        finger ||= { id: e.pointerId, x: e.clientX, y: e.clientY, dragged: false };
        return;
      }
      held = true;
      pointer(e, 'down', { button: button(e) });
    });
    canvas.addEventListener('pointermove', (e) => {
      if (pinch) return;
      if (!finger) return pointer(e, 'move');
      if (e.pointerId !== finger.id) return;
      const dx = e.clientX - finger.x, dy = e.clientY - finger.y;
      if (!finger.dragged && Math.hypot(dx, dy) < 9) return;
      finger.dragged = true;
      touchTap = false;
      try { canvas.setPointerCapture(finger.id); } catch {}
      finger.x = e.clientX;
      finger.y = e.clientY;
      if (!fingerDrag(zoom.z)) return;
      zoom.tx += dx;
      zoom.ty += dy;
      applyZoom();
    });
    const release = (e) => {
      if (pinch || pts.size) return;
      if (finger) {
        if (e.pointerId !== finger.id) return;
        const tap = !finger.dragged && e.type === 'pointerup';
        finger = null;
        if (tap) {
          lastTap = { x: e.clientX, y: e.clientY };
          pointer(e, 'tap', { button: 'left' });
          offerKeys(e);
          afterTap();
        }
        return;
      }
      if (!held) return;
      held = false;
      pointer(e, 'up', { button: button(e) });
    };
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('pointercancel', release);
    // One focus per tap, inside pointerup or touchend (the call a phone accepts).
    // A click comes later and is not a tap, so it does not focus the field.
    const offerKeys = (e) => {
      if (e.pointerType === 'mouse') return;
      if (e.touches && (e.touches.length || e.changedTouches.length !== 1)) return;
      // pointerup and touchend are one tap. Focus again only if the first call
      // did not take (a phone may ignore focus except on touchend).
      if (gestureFocused && (document.activeElement === keys || e.type === 'pointerup')) return;
      const page = spot(e);
      const decision = keysAction({ kind: 'tap', pointerType: 'touch', moved: false, fingers: 1, onField: page ? onField(page, knownField) : false });
      gestureFocused = true;
      if (decision === 'focus') focusKeys();
      else if (decision === 'blur' && document.activeElement === keys) keys.blur();
    };
    canvas.addEventListener('touchend', (e) => {
      if (pinched || pinch || pts.size) {
        touchTap = false;
        if (!e.touches || !e.touches.length) pinched = false;
        return;
      }
      if (touchTap) offerKeys(e);
      touchTap = false;
    });
    canvas.addEventListener('click', () => { touchTap = false; });
    canvas.addEventListener('pointercancel', () => { touchTap = false; });
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) { zoomAt(zoom.z * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY); return; }
      link.send({ kind: 'wheel', dy: e.deltaY, dx: e.deltaX });
    }, { passive: false });
    canvas.addEventListener('keydown', (e) => {
      const named = ['Enter', 'Escape', 'Backspace', 'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Delete', 'Home', 'End', 'PageUp', 'PageDown', 'Insert'];
      if (e.isComposing) return;
      const text = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
      if (!text && !named.includes(e.key) && e.key.length !== 1) return;
      // The key is the page's, not the sheet's (Esc) or this document's (Tab, space).
      e.preventDefault();
      e.stopPropagation();
      link.send(text ? { kind: 'text', text: e.key } : { kind: 'key', key: e.key, shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey });
    });

    const node = el('div', { class: 'live' });
    const full = fullscreen(node);
    node.append(
      el('form', { class: 'url', onsubmit: (e) => { e.preventDefault(); go(); } },
        back, forward, nav('↻', 'Neu laden', 'reload'), address,
        lens,
        el('button', { type: 'button', class: 'pill kbd', text: '⌨', title: 'Tastatur', 'aria-label': 'Tastatur', onclick: () => focusKeys() }),
        el('button', { type: 'button', class: 'pill', 'aria-label': 'Vollbild', title: 'Vollbild', html: fullIcon, onclick: () => full.toggle() })),
      el('div', { class: 'view' }, canvas, wait, keys),
      status);
    const screen = { canvas, say: (text) => { status.textContent = text; }, shown: () => { canvas.hidden = false; wait.hidden = true; } };
    link.watch(screen);
    sync();
    const timer = setInterval(sync, 3000);
    const onResize = () => { if (!stopped && zoom.z > 1) applyZoom(); };
    window.addEventListener('resize', onResize);
    window.visualViewport?.addEventListener('resize', onResize);
    return { node, stop() {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
      window.removeEventListener('resize', onResize);
      window.visualViewport?.removeEventListener('resize', onResize);
      full.close();
      link.unwatch(screen);
    } };
  };

  // ---- the terminal of a bot's VM: xterm.js on the PTY ----------------------
  let xterm = null;
  function loadXterm() {
    if (window.Terminal && window.FitAddon?.FitAddon) return Promise.resolve();
    xterm ||= new Promise((resolve, reject) => {
      const v = '?v=' + encodeURIComponent(MAG.assets);
      const load = (src) => new Promise((ok, bad) => document.head.append(el('script', { src: src + v, onload: ok, onerror: () => bad(new Error(src + ' fehlt')) })));
      document.head.append(el('link', { rel: 'stylesheet', href: '/static/xterm.css' + v }));
      load('/static/xterm.js').then(() => load('/static/xterm-addon-fit.js')).then(resolve, (err) => { xterm = null; reject(err); });
    });
    return xterm;
  }
  // The terminal wears the room's colours, in the dark and in the light.
  const light = matchMedia('(prefers-color-scheme: light)');
  function termTheme() {
    const css = getComputedStyle(document.documentElement), token = (name) => css.getPropertyValue(name).trim();
    const base = { background: token('--code'), foreground: token('--text'), cursor: token('--muted'), cursorAccent: token('--code') };
    return (document.documentElement.dataset.theme || (light.matches ? 'light' : 'dark')) === 'light'
      ? { ...base, selectionBackground: '#3554d133', black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00', blue: '#0969da', magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
          brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37', brightYellow: '#633c01', brightBlue: '#218bff', brightMagenta: '#a475f9', brightCyan: '#3192aa', brightWhite: '#8c959f' }
      : { ...base, selectionBackground: '#8fa7ff44', black: '#484f58', red: '#ff7b72', green: '#3fb950', yellow: '#d29922', blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#b1bac4',
          brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364', brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#d2a8ff', brightCyan: '#56d4dd', brightWhite: '#ffffff' };
  }
  const retheme = () => { for (const entry of terminals.values()) entry.term.options.theme = termTheme(); };
  new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  light.addEventListener('change', retheme);
  // One terminal per bot for the life of the page: its scrollback stays when
  // the panel closes, like the shell in the VM stays when the page does.
  function makeTerminal(bot) {
    const existing = terminals.get(bot);
    if (existing) return existing;
    const link = linkOf(bot), host = el('div', { class: 'terminal-host' });
    const term = new Terminal({ cursorBlink: true, scrollback: 2500, fontSize: 12, convertEol: false,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace', theme: termTheme() });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    const entry = { term, fit, host, opened: false, ctrl: false, onCtrl: null };
    // Ctrl+C with a selection copies; without one it interrupts the shell as usual.
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown' || !(ev.ctrlKey || ev.metaKey) || ev.altKey) return true;
      const key = ev.key.toLowerCase();
      if (key === 'c') {
        const selected = term.getSelection();
        if (selected) { navigator.clipboard?.writeText(selected).catch(() => {}); return false; }
        return true;
      }
      return key !== 'v'; // the browser's own paste event does that
    });
    term.onData((data) => {
      // Back to the live cursor before input goes out, if the reader had scrolled up.
      if (term.buffer.active.viewportY < term.buffer.active.baseY) term.scrollToBottom();
      // The Strg key of the key row holds for the next single character.
      if (entry.ctrl && data.length === 1) {
        const code = data.toUpperCase().charCodeAt(0);
        if (code >= 63 && code <= 95) data = String.fromCharCode(code === 63 ? 127 : code - 64);
        entry.ctrl = false;
        entry.onCtrl?.();
      }
      link.typed(data);
    });
    term.onResize(({ cols, rows }) => link.sendConsole('terminal_resize', { cols, rows }));
    terminals.set(bot, entry);
    if (terminals.size > 12) {
      const first = terminals.keys().next().value;
      if (first !== bot) { terminals.get(first).term.dispose(); terminals.delete(first); }
    }
    return entry;
  }

  MAG.terminal = function (bot) {
    const link = linkOf(bot), name = MAG.bot(bot)?.name || bot;
    const box = el('div', { class: 'pty', role: 'application', 'aria-label': 'Shell in der VM von ' + name });
    const status = el('div', { class: 'note', text: 'Terminal wird geladen …' });
    let stopped = false, held = true, entry = null, observer = null, frame = 0, plain = null, wheel = 0;
    // xterm's FitAddon turns the size of the box into columns and rows; the PTY follows.
    const fit = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!entry || !entry.opened || entry.host.parentElement !== box || box.offsetWidth < 60 || box.offsetHeight < 90) return;
        try { entry.fit.fit(); link.sendConsole('terminal_resize', { cols: entry.term.cols, rows: entry.term.rows }); }
        catch (err) { status.textContent = 'Terminalgröße: ' + err.message; }
      });
    };
    const view = { say: (text) => { status.textContent = text; }, text: () => status.textContent, opened: () => { offer.hidden = true; fit(); } };
    const done = () => { if (held) { held = false; link.detachTerminal(view); link.release(); } };

    // What a phone keyboard lacks. The row never takes the focus, so the
    // terminal keeps it and the phone its keyboard.
    const key = (label, data, title) => el('button', { type: 'button', text: label, title: title || label, 'aria-label': title || label,
      onclick: () => { if (!entry) return; entry.term.scrollToBottom(); link.typed(typeof data === 'function' ? data() : data); } });
    const arrow = (letter) => () => (entry.term.modes.applicationCursorKeysMode ? '\x1bO' : '\x1b[') + letter;
    const ctrl = el('button', { type: 'button', text: 'Strg', title: 'Strg für das nächste Zeichen', onclick: () => { if (entry) ctrl.classList.toggle('on', (entry.ctrl = !entry.ctrl)); } });
    const keep = (e) => e.preventDefault();
    const keys = el('div', { class: 'keys', onpointerdown: keep, onmousedown: keep },
      key('Esc', '\x1b'), key('Tab', '\t'), ctrl, key('↑', arrow('A'), 'Pfeil hoch'), key('↓', arrow('B'), 'Pfeil runter'), key('←', arrow('D'), 'Pfeil links'), key('→', arrow('C'), 'Pfeil rechts'));
    // No PTY after a while: offer the plain way instead of a dead screen.
    const offer = el('button', { type: 'button', class: 'pill', text: 'Einzelne Befehle', title: 'Ohne UDP-Weg: ein Befehl nach dem anderen über HTTPS', hidden: true, onclick: () => {
      done();
      full.close();
      plain = shell(bot);
      node.replaceChildren(plain.node, el('div', { class: 'foot' }, el('div', { class: 'note', text: 'Befehlsmodus über HTTPS, ohne PTY. Das echte Terminal braucht den UDP-Weg zur VM.' })));
    } });
    const node = el('div', { class: 'term-box' });
    // In fullscreen there is room for a size that reads well on a desk.
    const full = fullscreen(node, (on) => { if (entry) entry.term.options.fontSize = on && innerWidth >= 900 ? 14 : 12; fit(); });
    node.append(box, keys, el('div', { class: 'foot' }, status, offer,
      el('button', { type: 'button', class: 'pill', text: 'Leeren', onclick: () => entry?.term.clear() }),
      el('button', { type: 'button', class: 'pill', 'aria-label': 'Vollbild', title: 'Vollbild', html: fullIcon, onclick: () => { full.toggle(); entry?.term.focus(); } })));
    box.addEventListener('click', () => entry?.term.focus());
    // Esc belongs to the shell (vim), not to the sheet around the terminal.
    box.addEventListener('keydown', (e) => { if (e.key === 'Escape') e.stopPropagation(); });
    // So does the wheel: a program on the alternate screen or with mouse
    // reporting gets it from xterm; otherwise it scrolls the history, and
    // fractions of a line add up so a touchpad scrolls smoothly.
    box.addEventListener('wheel', (e) => {
      if (!entry || !entry.opened || !entry.host.contains(e.target)) return;
      e.preventDefault();
      if (entry.term.buffer.active.type === 'alternate' || entry.term.modes.mouseTrackingMode !== 'none') return;
      e.stopPropagation();
      wheel += e.deltaMode === 1 ? e.deltaY : e.deltaMode === 2 ? e.deltaY * entry.term.rows : e.deltaY / Math.max(12, entry.term.options.fontSize * 1.25);
      const lines = Math.trunc(wheel);
      if (lines) { entry.term.scrollLines(lines); wheel -= lines; }
    }, { capture: true, passive: false });

    link.use();
    const timer = setTimeout(() => { if (held && link.console?.readyState !== 'open') offer.hidden = false; }, 9000);
    loadXterm().then(() => {
      if (stopped || !held) return;
      entry = makeTerminal(bot);
      entry.ctrl = false;
      entry.onCtrl = () => ctrl.classList.remove('on');
      entry.term.options.fontSize = 12;
      box.replaceChildren(entry.host);
      if (!entry.opened) { entry.term.open(entry.host); entry.opened = true; }
      status.textContent = 'Verbinde VM-Terminal …';
      link.attachTerminal(view);
      fit();
      observer = new ResizeObserver(() => { if (box.getBoundingClientRect().width > 0) fit(); });
      observer.observe(box);
    }, (err) => { status.textContent = 'xterm.js konnte nicht geladen werden (' + err.message + ').'; offer.hidden = false; });
    return { node, stop() {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      observer?.disconnect();
      plain?.stop();
      full.close();
      if (entry) entry.onCtrl = null;
      done();
    } };
  };

  // Without UDP there is no PTY. Then this still works: one command at a time
  // as user bot over HTTPS, output as it comes, Stop ends it.
  function shell(bot) {
    let cwd = '', running = null, at = 0;
    const history = [];
    const short = (p) => (p || '~').replace(/^\/home\/bot(?=\/|$)/, '~');
    const out = el('pre', { class: 'term-out' });
    const where = el('span', { class: 'term-cwd', text: '~' });
    const input = el('input', { type: 'text', class: 'term-in', autocapitalize: 'off', autocomplete: 'off', spellcheck: false, enterKeyHint: 'send', placeholder: 'Befehl', 'aria-label': 'Befehl' });
    input.setAttribute('autocorrect', 'off');
    const go = el('button', { class: 'pill', text: 'Ausführen', onclick: () => (running ? running.abort() : run()) });
    const node = el('div', { class: 'term' }, out, el('div', { class: 'term-row' }, where, input, go));
    const show = (text, cls) => {
      out.append(cls ? el('span', { class: cls, text }) : text);
      if (out.textContent.length > 240000) out.textContent = out.textContent.slice(-160000);
      out.scrollTop = out.scrollHeight;
    };
    async function run() {
      const command = input.value;
      if (!command.trim() || running) return;
      history.push(command);
      at = history.length;
      input.value = '';
      show(short(cwd) + ' $ ' + command + '\n', 'cmd');
      running = new AbortController();
      go.textContent = 'Stop';
      go.classList.add('danger');
      input.disabled = true;
      try {
        const res = await MAG.fetch('/api/vm/' + bot + '/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command, cwd }), signal: running.signal });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Fehler ' + res.status);
        const reader = res.body.getReader(), decoder = new TextDecoder();
        let tail = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          let text = tail + decoder.decode(value, { stream: true });
          const mark = text.indexOf('\x1e'); // the closing record: exit code and directory
          tail = mark >= 0 ? text.slice(mark) : '';
          if (mark >= 0) text = text.slice(0, mark);
          if (text) show(text);
        }
        let info = {};
        try { info = JSON.parse(tail.slice(1)); } catch {}
        if (info.cwd) cwd = info.cwd;
        if (info.error) show(info.error + '\n', 'err');
        else if (info.cancelled) show('^C\n', 'err');
        else if (info.timeout) show('Zeitlimit erreicht\n', 'err');
        else if (info.exit) show('Exit ' + info.exit + '\n', 'err');
      } catch (err) {
        show((err.name === 'AbortError' ? '^C' : err.message) + '\n', 'err');
      }
      running = null;
      go.textContent = 'Ausführen';
      go.classList.remove('danger');
      input.disabled = false;
      where.textContent = short(cwd);
      if (matchMedia('(pointer: fine)').matches) input.focus();
    }
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); run(); }
      else if (e.key === 'ArrowUp' && at > 0) { e.preventDefault(); input.value = history[--at]; }
      else if (e.key === 'ArrowDown' && at < history.length) { e.preventDefault(); input.value = history[++at] || ''; }
    });
    return { node, stop() { running?.abort(); } };
  }


  // The bot's own files: look around, open, download, delete.
  MAG.files = function (bot) {
    const crumbs = el('div', { class: 'fx-path' }), list = el('div', { class: 'fx-list' }), view = el('div', { class: 'fx-view', hidden: true });
    const node = el('div', { class: 'fx' }, crumbs, list, view);
    const api = (what, path, extra) => '/api/vm/' + bot + '/' + what + '?path=' + encodeURIComponent(path) + (extra || '');
    async function open(path) {
      view.hidden = true;
      list.hidden = false;
      list.replaceChildren(el('div', { class: 'fx-note', text: 'Lädt …' }));
      try {
        const out = await MAG.api('GET', api('files', path || '.'));
        const rel = out.path === out.home ? [] : out.path.slice(out.home.length + 1).split('/');
        crumbs.replaceChildren(el('button', { text: '~', onclick: () => open(out.home) }),
          ...rel.flatMap((part, i) => [el('span', { text: '/' }), el('button', { text: part, onclick: () => open(out.home + '/' + rel.slice(0, i + 1).join('/')) })]));
        const rows = out.entries.sort((a, b) => b.dir - a.dir || a.name.localeCompare(b.name, 'de'));
        list.replaceChildren(...rows.map((entry) => row(entry, out.path)));
        if (!rows.length) list.append(el('div', { class: 'fx-note', text: 'Leer' }));
      } catch (err) {
        list.replaceChildren(el('div', { class: 'fx-note err', text: err.message }));
      }
    }
    function row(entry, dir) {
      const full = dir + '/' + entry.name, url = api('file', full);
      const more = el('button', { class: 'act', 'aria-label': 'Aktionen', html: MAG.icons.dots, onclick: (e) => {
        e.stopPropagation();
        MAG.menu(e.currentTarget, [
          entry.dir ? null : { icon: 'open', label: 'In neuem Tab öffnen', run: () => window.open(url, '_blank', 'noopener') },
          entry.dir ? null : { icon: 'down', label: 'Herunterladen', run: () => { const a = el('a', { href: url + '&download=1', download: entry.name }); document.body.append(a); a.click(); a.remove(); } },
          { icon: 'trash', label: 'Löschen', danger: true, run: async () => {
            if (!(await MAG.confirm(entry.name + ' löschen?', entry.dir ? 'Der Ordner und alles darin wird in der VM gelöscht.' : 'Die Datei wird in der VM gelöscht.', 'Löschen', true))) return;
            MAG.api('DELETE', api('file', full)).then(() => open(dir)).catch((x) => MAG.toast(x.message, true));
          } },
        ]);
      } });
      return el('div', { class: 'fx-row' + (entry.dir ? ' dir' : ''), onclick: () => (entry.dir ? open(full) : look(entry, dir, url)) },
        MAG.icon(entry.dir ? 'folder' : 'file'), el('span', { class: 'nm', text: entry.name }), el('small', { text: entry.dir ? '' : MAG.bytes(entry.size) }), more);
    }
    // Pictures open over the chat, small text files right here, the rest in a tab.
    async function look(entry, dir, url) {
      if (/\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(entry.name)) return MAG.lightbox(url, entry.name);
      if (entry.size > 400000) return window.open(url, '_blank', 'noopener');
      try {
        const text = await MAG.api('GET', url + '&text=1');
        view.replaceChildren(el('div', { class: 'bar' }, el('button', { class: 'pill', text: '← Zurück', onclick: () => open(dir) }), el('b', { text: entry.name })),
          el('pre', { text: typeof text === 'string' ? text : JSON.stringify(text, null, 2) }));
        list.hidden = true;
        view.hidden = false;
      } catch (err) { MAG.toast(err.message, true); }
    }
    open('');
    return { node, stop() {} };
  };
})();
