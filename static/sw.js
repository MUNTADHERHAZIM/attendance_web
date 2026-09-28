// =====================================================================
// Service Worker — نظام الحضور الذكي
// Cache-first strategy for static assets, Network-first for API calls
// =====================================================================

const CACHE_VERSION = "v2";
const CACHE_NAME = `smart-attendance-${CACHE_VERSION}`;
const API_CACHE_NAME = `smart-attendance-api-${CACHE_VERSION}`;

// Static assets to pre-cache on install
const STATIC_ASSETS = [
    "/login/",
    "/static/images/logo_192.png",
    "/static/images/logo_512.png",
];

// Patterns that should NEVER be cached
const NEVER_CACHE = [
    "/api/",
    "/attendance/api/",
    "/admin/",
];

// ─── Install: Pre-cache static assets ────────────────────────────────
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn("[SW] Failed to pre-cache some assets:", err);
            });
        }).then(() => self.skipWaiting())
    );
});

// ─── Activate: Clean up old caches ───────────────────────────────────
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME && key !== API_CACHE_NAME)
                    .map((key) => caches.delete(key))
            );
        }).then(() => self.clients.claim())
    );
});

// ─── Fetch: Smart routing strategy ───────────────────────────────────
self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);

    // Skip non-GET requests and cross-origin
    if (event.request.method !== "GET" || url.origin !== self.location.origin) {
        return;
    }

    // Skip never-cache paths (API, admin)
    const isNeverCache = NEVER_CACHE.some((p) => url.pathname.startsWith(p));
    if (isNeverCache) {
        return; // Let browser handle normally
    }

    // Static assets → Cache First
    if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/media/")) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                if (cached) return cached;
                return fetch(event.request).then((response) => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
                    }
                    return response;
                });
            })
        );
        return;
    }

    // HTML pages → Network First, fallback to cache
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                if (response.ok) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
                }
                return response;
            })
            .catch(() => {
                return caches.match(event.request).then((cached) => {
                    if (cached) return cached;
                    // Offline fallback for page navigations
                    if (event.request.headers.get("accept")?.includes("text/html")) {
                        return caches.match("/login/");
                    }
                });
            })
    );
});
