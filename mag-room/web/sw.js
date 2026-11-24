// MAG service worker: versioned assets from cache, the page itself always
// from the network (never stale room data), and push notifications.
const VERSION = '{{V}}';
const CACHE = 'mag-' + VERSION;
const SHARE = 'mag-share';

// Something shared to MAG from the system's share menu (manifest share_target):
// the browser posts it here as a navigation. Nothing goes to the room from the
// service worker: the files and words are kept in Cache Storage for one page,
// which sends them the way the composer does (/api/upload with its limits and
// the owner's token, then /api/message). The shared files never touch the
// network before that page has them.
async function takeShare(request) {
  const id = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  let form = null;
  try { form = await request.formData(); } catch {}
  if (!form) return Response.redirect(location.origin + '/?share=lost', 303);
  const str = (k) => { const v = form.get(k); return typeof v === 'string' ? v.trim() : ''; };
  const files = form.getAll('files').filter((f) => f && typeof f === 'object' && 'size' in f);
  const cache = await caches.open(SHARE);
  const meta = { title: str('title'), text: str('text'), url: str('url'), files: [] };
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const key = '/share-inbox/' + id + '/' + i;
    await cache.put(key, new Response(f, { headers: { 'Content-Type': f.type || 'application/octet-stream' } }));
    meta.files.push({ key, name: f.name || 'datei', type: f.type || '' });
  }
  await cache.put('/share-inbox/' + id, new Response(JSON.stringify(meta), { headers: { 'Content-Type': 'application/json' } }));
  return Response.redirect(location.origin + '/?share=' + id, 303);
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // Only the page's own earlier caches: the voices (Cache Storage 'mag-tts-…', fetched once by
    // the reading worker) is not the page's and must survive a new release.
    // Nor is a share that has not been picked up yet ('mag-share').
    for (const key of await caches.keys()) if (key.startsWith('mag-') && !key.startsWith('mag-tts') && key !== SHARE && key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'POST' && url.origin === location.origin && url.pathname === '/share') {
    event.respondWith(takeShare(event.request));
    return;
  }
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  const versioned = (url.pathname.startsWith('/app/') || url.pathname.startsWith('/static/')) && url.searchParams.get('v') === VERSION;
  if (versioned) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const res = await fetch(event.request);
      if (res.ok) cache.put(event.request, res.clone());
      return res;
    })());
    return;
  }
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => new Response(
      '<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>MAG</title>' +
      '<body style="font:16px system-ui;background:#0c0d10;color:#ececf0;display:grid;place-items:center;height:100vh;margin:0">' +
      '<p>MAG ist gerade nicht erreichbar.</p>', { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
  }
});

self.addEventListener('push', (event) => {
  let note = {};
  try { note = event.data.json(); } catch {}
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // An answer you are already looking at needs no notification, nor does a question that waits in front of you.
    if ((note.kind === 'run' || note.kind === 'job' || note.kind === 'approval') &&
        windows.some((w) => w.visibilityState === 'visible')) return;
    await self.registration.showNotification(note.title || 'MAG', {
      body: note.body || '', tag: note.tag || 'mag', icon: '/static/icon-192.png?v=' + VERSION, data: { url: note.url || '/' } });
  })());
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const url = event.notification.data?.url || '/';
    const id = new URL(url, self.location.origin).searchParams.get('m');
    if (windows.length) {
      const page = windows.find((w) => w.visibilityState === 'visible') || windows[0];
      await page.focus();
      if (id) page.postMessage({ show: id });
      return;
    }
    return self.clients.openWindow(url);
  })());
});
