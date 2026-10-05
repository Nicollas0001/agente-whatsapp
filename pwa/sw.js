// Service worker do app: abre mesmo com o servidor dormindo ou sem internet
// e mostra as notificações da secretária.
const CACHE = 'secretaria-v2';
const BASE = ['./', 'manifest.webmanifest', 'icone-192.png', 'icone-512.png'];
const ESPERA_REDE_MS = 3000;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(nomes => Promise.all(nomes.filter(n => n !== CACHE).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith('/app')) return;
  if (e.request.mode === 'navigate') { e.respondWith(abrirApp(e)); return; }
  // ícones e manifesto: mostra o guardado e atualiza por trás
  e.respondWith(caches.open(CACHE).then(async cache => {
    const guardado = await cache.match(e.request);
    const daRede = fetch(e.request).then(r => { if (r.ok) cache.put(e.request, r.clone()); return r; });
    if (guardado) { e.waitUntil(daRede.catch(() => {})); return guardado; }
    return daRede;
  }));
});

// A página do app: a versão nova do servidor, se chegar em até 3 s; senão (servidor
// acordando ou sem internet), a guardada. Só guarda resposta que é mesmo a página.
async function abrirApp(e) {
  const cache = await caches.open(CACHE);
  const daRede = fetch(e.request).then(r => {
    if (r.ok && (r.headers.get('content-type') || '').startsWith('text/html')) cache.put('./', r.clone());
    return r;
  });
  e.waitUntil(daRede.catch(() => {}));
  const guardada = await cache.match('./');
  if (!guardada) return daRede;
  const espera = new Promise(ok => setTimeout(() => ok(null), ESPERA_REDE_MS));
  try {
    const r = await Promise.race([daRede, espera]);
    return r && r.ok ? r : guardada;
  } catch { return guardada; }
}

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
