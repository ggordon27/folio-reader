const SHELL_CACHE = "folio-shell-v4";
const EPUB_CACHE = "folio-epub-v2";
const SHELL_ASSETS = ["/", "/styles.css", "/app.js", "/icon.svg", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== EPUB_CACHE)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Cache-first for immutable EPUB resources & cover thumbnails (enables instant chapter turns & offline reading)
  if (url.pathname.startsWith("/epub/") || /^\/api\/books\/[^/]+\/cover$/.test(url.pathname)) {
    event.respondWith(
      caches.open(EPUB_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          if (cached) return cached;
          return fetch(req).then((resp) => {
            if (resp.ok) cache.put(req, resp.clone());
            return resp;
          });
        })
      )
    );
    return;
  }

  // Never intercept dynamic API calls
  if (url.pathname.startsWith("/api/")) return;

  // Network-first for static shell assets so updates appear immediately while keeping offline fallback
  event.respondWith(
    caches.open(SHELL_CACHE).then((cache) =>
      fetch(req)
        .then((resp) => {
          if (resp.ok) cache.put(req, resp.clone());
          return resp;
        })
        .catch(() => cache.match(req))
    )
  );
});
