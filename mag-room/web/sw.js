// MAG room service worker. The server stamps VERSION and serves this file with
// Cache-Control: no-store, so a new build is always fetched.
// HTML is network-first. Versioned static files are cache-first. API, SSE and
// room data are never cached. A failed navigation gets a tiny offline page,
// never a stored copy of the room.
const VERSION = '%%MAG_VERSION%%';
const CACHE = 'mag-static-' + VERSION;
const OFFLINE = '<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MAG</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#050505;color:#fff;font:16px/1.5 sans-serif}main{max-width:28rem;padding:24px}h1{font-size:20px;font-weight:600;margin:0 0 8px}p{margin:0;color:#c4c4cb}</style><main><h1>MAG ist offline</h1><p>Keine Verbindung. Es werden keine alten Raumdaten angezeigt.</p></main></html>';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (!event.data)
    return;
  if (event.data.type === 'SKIP_WAITING')
    self.skipWaiting();
  if (event.data.type === 'VERSION' && event.source)
    event.source.postMessage({version: VERSION});
});

function versionedStatic(url) {
  if (!url.searchParams.has('v'))
    return false;
  return url.pathname.startsWith('/static/') ||
    url.pathname === '/favicon.svg' ||
    url.pathname === '/favicon.ico' ||
    url.pathname === '/apple-touch-icon.png';
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  if (hit)
    return hit;
  const res = await fetch(request);
  if (res && res.ok && res.type === 'basic')
    await cache.put(request, res.clone());
  return res;
}

async function networkShell(request) {
  try {
    return await fetch(request, {cache: 'no-store'});
  } catch (e) {
    if (request.mode === 'navigate') {
      return new Response(OFFLINE, {
        status: 503,
        headers: {
          'Content-Type': 'text/html; charset=utf-8',
          'Cache-Control': 'no-store'
        }
      });
    }
    throw e;
  }
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin)
    return;
  if (event.request.method !== 'GET')
    return;
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/events'))
    return;
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(networkShell(event.request));
    return;
  }
  if (versionedStatic(url))
    event.respondWith(cacheFirst(event.request));
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {};
  }
  event.waitUntil((async () => {
    if (data.kind === 'run') {
      const open = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
      if (open.some((client) => client.visibilityState === 'visible'))
        return;
    }
    const target = (typeof data.url === 'string' && data.url.charAt(0) === '/') ? data.url : '/';
    await self.registration.showNotification(data.title || 'MAG', {
      body: data.body || '',
      tag: data.tag || 'mag',
      icon: '/static/icon-192.png?v=' + VERSION,
      data: {url: target, kind: data.kind || ''}
    });
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const client of open) {
      if ('focus' in client) {
        await client.focus();
        try {
          await client.navigate(target);
        } catch (e) {}
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
