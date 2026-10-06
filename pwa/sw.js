// Service worker do app: só para as notificações da secretária.
// Não intercepta nenhum pedido: no Chrome do Android, abrir o app instalado por uma
// resposta do service worker fazia o navegador oferecer "baixar download.html".
// Sem handler de fetch, a página sempre vem direto do servidor.
const VERSAO = 'secretaria-v3';

self.addEventListener('install', () => self.skipWaiting());

// apaga as cópias guardadas pelas versões antigas (secretaria-v1, -v2)
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(nomes => Promise.all(nomes.map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
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
