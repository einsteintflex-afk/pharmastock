/* PharmaStock service worker.
   Caches only the application shell (HTML, CSS, JS, icons) so the app
   starts quickly and shows a clear message when the server is unreachable.
   API data is never cached: it is private and must always be current. */

const VERSION = "pharmastock-shell-v1";

self.addEventListener("install", event => {
    event.waitUntil(caches.open(VERSION).then(cache => cache.addAll([
        "./", "index.html", "style.css", "manifest.json", "assets/logo.webp", "assets/mark-64.png", "assets/favicon-32.png",
    ])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
    event.waitUntil(caches.keys()
        .then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key))))
        .then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
    const url = new URL(event.request.url);
    const shell = event.request.method === "GET" && url.origin === self.location.origin
        && url.pathname.startsWith(new URL(self.registration.scope).pathname);
    if (!shell) return;  // API calls go straight to the network
    // Network first (always the latest release), cache as fallback when offline.
    event.respondWith(fetch(event.request)
        .then(response => {
            if (response.ok) {
                const copy = response.clone();
                caches.open(VERSION).then(cache => cache.put(event.request, copy));
            }
            return response;
        })
        .catch(() => caches.match(event.request).then(hit => hit || caches.match("index.html"))));
});
