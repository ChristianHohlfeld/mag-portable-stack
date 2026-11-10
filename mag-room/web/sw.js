// MAG service worker: versioned assets from cache, the page itself always
// from the network (never stale room data), and push notifications.
const VERSION = '{{V}}';
const CACHE = 'mag-' + VERSION;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith('mag-') && key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
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
    if ((note.kind === 'run' || note.kind === 'approval') && windows.some((w) => w.visibilityState === 'visible')) return;
    await self.registration.showNotification(note.title || 'MAG', {
      body: note.body || '', tag: note.tag || 'mag', icon: '/static/icon-192.png?v=' + VERSION, data: { url: note.url || '/' } });
  })());
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (windows.length) return windows[0].focus();
    return self.clients.openWindow(event.notification.data?.url || '/');
  })());
});
