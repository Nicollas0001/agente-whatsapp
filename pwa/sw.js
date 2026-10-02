// Service worker do app: abre na hora (mesmo com o servidor dormindo ou sem internet)
// e mostra as notificações da secretária.
const CACHE = 'secretaria-v1';
const BASE = ['./', 'manifest.webmanifest', 'icone-192.png', 'icone-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(nomes => Promise.all(nomes.filter(n => n !== CACHE).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

// Mostra o que está guardado e atualiza por trás; a versão nova vale na próxima abertura.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith('/app')) return;
  const pedido = e.request.mode === 'navigate' ? new Request('./') : e.request;
  e.respondWith(caches.open(CACHE).then(async cache => {
    const guardado = await cache.match(pedido);
    const daRede = fetch(pedido).then(r => { if (r.ok) cache.put(pedido, r.clone()); return r; });
    if (guardado) { e.waitUntil(daRede.catch(() => {})); return guardado; }
    return daRede;
  }));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { corpo: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.titulo || 'Secretária', {
    body: d.corpo || '', icon: 'icone-192.png', badge: 'icone-192.png',
    tag: d.tag || 'secretaria', renotify: true, data: { url: d.url || './#secretaria' }
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const destino = new URL(e.notification.data.url, self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(janelas => {
    for (const j of janelas) {
      if (j.url.startsWith(self.registration.scope)) { j.navigate(destino).catch(() => {}); return j.focus(); }
    }
    return self.clients.openWindow(destino);
  }));
});
