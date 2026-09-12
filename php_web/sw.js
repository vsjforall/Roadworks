// App shell cached; API responses always go to network first so a user is
// never shown a stale winner as if it were current.
const SHELL = 'ki-shell-v1';
const FILES = ['index.html', 'app.js', 'manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.pathname.includes('api.php')) return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
