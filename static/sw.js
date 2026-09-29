// =====================================================================
// Service Worker — نظام الحضور الذكي | Smart Attendance PWA
// Ultra-resilient Offline Caching, Stale-While-Revalidate & Instant Sync
// =====================================================================

const SW_VERSION = "v3.2.0";
const PRECACHE_NAME = `smart-att-precache-${SW_VERSION}`;
const RUNTIME_CACHE = `smart-att-runtime-${SW_VERSION}`;
const STATIC_CACHE = `smart-att-static-${SW_VERSION}`;

// Critical Shell Assets to pre-cache immediately upon install
const PRECACHE_URLS = [
    "/offline/",
    "/login/",
    "/manifest.json",
    "/favicon.ico",
    "/static/images/pwa/icon-192x192.png",
    "/static/images/pwa/icon-512x512.png",
    "/static/images/pwa/icon-maskable-192x192.png",
    "/static/images/pwa/apple-touch-icon.png",
    "/static/images/pwa/favicon-32x32.png",
    "/static/images/logo_192.png",
    "/static/images/logo_512.png",
];

// Patterns that MUST ALWAYS bypass cache (network only, no cache fallback)
const NEVER_CACHE_URLS = [
    "/admin/",
    "/api/",
    "/attendance/api/",
    "/logout/",
    "/password-reset/",
    "/ws/",
];

// ─── 1. Install Event ───────────────────────────────────────────────
self.addEventListener("install", (event) => {
    console.log(`[PWA SW] Installing Smart Attendance Service Worker (${SW_VERSION})...`);
    event.waitUntil(
        caches.open(PRECACHE_NAME).then((cache) => {
            return Promise.allSettled(
                PRECACHE_URLS.map((url) =>
                    fetch(url, { cache: "no-cache" })
                        .then((res) => {
                            if (res.ok) return cache.put(url, res);
                            console.warn(`[PWA SW] Failed to cache ${url}: HTTP ${res.status}`);
                        })
                        .catch((err) => console.warn(`[PWA SW] Precache fetch error for ${url}:`, err))
                )
            );
        }).then(() => {
            console.log("[PWA SW] Pre-caching complete. Activating immediately.");
            return self.skipWaiting();
        })
    );
});

// ─── 2. Activate Event (Cache Maintenance & Claiming) ───────────────
self.addEventListener("activate", (event) => {
    console.log(`[PWA SW] Activating Smart Attendance Service Worker (${SW_VERSION})...`);
    const expectedCaches = [PRECACHE_NAME, RUNTIME_CACHE, STATIC_CACHE];

    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (!expectedCaches.includes(cacheName)) {
                        console.log(`[PWA SW] Purging obsolete cache: ${cacheName}`);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => {
            console.log("[PWA SW] Claiming clients for instant control.");
            return self.clients.claim();
        })
    );
});

// ─── 3. Fetch Event Routing Strategies ──────────────────────────────
self.addEventListener("fetch", (event) => {
    const request = event.request;
    const url = new URL(request.url);

    // Skip non-GET requests entirely
    if (request.method !== "GET") {
        return;
    }

    // Bypass Chrome Extension and other non-http(s) schemes
    if (!url.protocol.startsWith("http")) {
        return;
    }

    // Check if URL is in NEVER_CACHE list
    const isExcluded = NEVER_CACHE_URLS.some((prefix) => url.pathname.startsWith(prefix));
    if (isExcluded) {
        return; // Normal network fetch
    }

    // Strategy A: Static Assets (Local /static/, /media/, CDNs for Fonts & Tailwind & Alpine)
    const isStaticAsset =
        url.pathname.startsWith("/static/") ||
        url.pathname.startsWith("/media/") ||
        url.hostname.includes("fonts.googleapis.com") ||
        url.hostname.includes("fonts.gstatic.com") ||
        url.hostname.includes("cdn.tailwindcss.com") ||
        url.hostname.includes("cdn.jsdelivr.net") ||
        url.hostname.includes("unpkg.com");

    if (isStaticAsset) {
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                if (cachedResponse) {
                    // Stale-While-Revalidate: serve cached & update cache in background
                    fetch(request)
                        .then((networkResponse) => {
                            if (networkResponse && networkResponse.status === 200) {
                                caches.open(STATIC_CACHE).then((cache) => cache.put(request, networkResponse));
                            }
                        })
                        .catch(() => {/* Ignore background network errors for cached assets */});
                    return cachedResponse;
                }

                // If not cached, fetch from network and cache
                return fetch(request).then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseToCache = networkResponse.clone();
                        caches.open(STATIC_CACHE).then((cache) => cache.put(request, responseToCache));
                    }
                    return networkResponse;
                }).catch(() => {
                    // Fallback for missing images
                    if (request.destination === "image") {
                        return caches.match("/static/images/pwa/icon-192x192.png");
                    }
                });
            })
        );
        return;
    }

    // Strategy B: HTML Navigation / Pages (Network First -> Cache Fallback -> Offline Page)
    if (request.mode === "navigate" || (request.headers.get("accept") && request.headers.get("accept").includes("text/html"))) {
        event.respondWith(
            fetch(request)
                .then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseToCache = networkResponse.clone();
                        caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, responseToCache));
                    }
                    return networkResponse;
                })
                .catch(async () => {
                    console.log(`[PWA SW] Network failed for ${url.pathname}. Attempting offline cache.`);
                    // Try to find cached version of this exact URL
                    const cachedPage = await caches.match(request);
                    if (cachedPage) {
                        return cachedPage;
                    }

                    // Try to return the dedicated offline page
                    const offlinePage = await caches.match("/offline/");
                    if (offlinePage) {
                        return offlinePage;
                    }

                    // Fallback to login page if pre-cached
                    const loginPage = await caches.match("/login/");
                    if (loginPage) {
                        return loginPage;
                    }

                    return new Response(
                        "<h1>غير متصل بالإنترنت</h1><p>يرجى التحقق من اتصال الشبكة وإعادة المحاولة.</p>",
                        { headers: { "Content-Type": "text/html; charset=utf-8" } }
                    );
                })
        );
        return;
    }

    // Strategy C: Generic Request Fallback (Network First with Runtime Cache)
    event.respondWith(
        fetch(request)
            .then((networkResponse) => {
                if (networkResponse && networkResponse.status === 200) {
                    const responseToCache = networkResponse.clone();
                    caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, responseToCache));
                }
                return networkResponse;
            })
            .catch(() => caches.match(request))
    );
});

// ─── 4. Client Communication & Instant Updates ──────────────────────
self.addEventListener("message", (event) => {
    if (event.data && event.data.type === "SKIP_WAITING") {
        console.log("[PWA SW] Received SKIP_WAITING signal.");
        self.skipWaiting();
    }
});

// ─── 5. Push Notifications (Native PWA Notifications) ───────────────
self.addEventListener("push", (event) => {
    if (!event.data) return;
    try {
        const data = event.data.json();
        const options = {
            body: data.body || "إشعار جديد من نظام الحضور الذكي",
            icon: data.icon || "/static/images/pwa/icon-192x192.png",
            badge: "/static/images/pwa/badge-72x72.png",
            dir: "rtl",
            lang: "ar",
            vibrate: [100, 50, 100],
            data: {
                url: data.url || "/"
            }
        };
        event.waitUntil(self.registration.showNotification(data.title || "نظام الحضور الذكي", options));
    } catch (e) {
        console.error("[PWA SW] Push handling error:", e);
    }
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const urlToOpen = (event.notification.data && event.notification.data.url) ? event.notification.data.url : "/";
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
            for (let client of windowClients) {
                if (client.url === urlToOpen && "focus" in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(urlToOpen);
            }
        })
    );
});
