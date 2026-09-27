/* RemnaDeck service worker.
   __VER__ сервер подставляет сам — это хеш всей статики. Поменялся любой файл → поменялся
   sw.js → браузер ставит новый воркер, старый кеш удаляется, приложение перезагружается.
   API и WebSocket не трогаем никогда — данные всегда живые, а без сети приложение хотя бы откроется. */
const VER = "__VER__";
const CACHE = "remnadeck-" + VER;
const SHELL = ["/", `/static/style.css?v=${VER}`, `/static/app.js?v=${VER}`, `/static/manage.js?v=${VER}`,
               "/static/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return;
  if (url.pathname === "/status" || url.pathname.startsWith("/static/status.")) return;  // публичная страница — мимо оболочки

  // страница: сначала сеть (свежую кладём в кеш), без сети — оболочка из кеша
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request, { cache: "no-cache" })
      .then((r) => { if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put("/", copy)); } return r; })
      .catch(() => caches.match("/")));
    return;
  }
  // статика с ?v=<хеш> неизменна — из кеша; остальное (иконки, манифест, vendor) — сначала сеть
  const versioned = url.searchParams.get("v") === VER;
  e.respondWith(caches.open(CACHE).then(async (c) => {
    const hit = await c.match(e.request);
    if (versioned && hit) return hit;
    try {
      const r = await fetch(e.request, { cache: "no-cache" });
      if (r.ok) c.put(e.request, r.clone());
      return r;
    } catch (err) {
      if (hit) return hit;
      throw err;
    }
  }));
});
