const CACHE_NAME = 'swaraj-grocery-v2';
const STATIC_ASSETS = [
  '/',
  '/static/manifest.json'
];

self.addEventListener('install', function (event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(STATIC_ASSETS).catch(function () {});
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) {
          return key !== CACHE_NAME;
        }).map(function (key) {
          return caches.delete(key);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

function offlineFallbackResponse() {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Swaraj Grocery - Offline</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: #f1f5f9;
      color: #0f172a;
      display: flex;
      min-height: 100vh;
      align-items: center;
      justify-content: center;
      padding: 20px;
      text-align: center;
    }
    .card {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 24px;
      padding: 32px 24px;
      max-width: 380px;
      width: 100%;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
    }
    .badge {
      display: inline-block;
      background: #ecfdf5;
      color: #047857;
      font-weight: 800;
      font-size: 11px;
      padding: 4px 12px;
      border-radius: 999px;
      margin-bottom: 12px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 22px;
      font-weight: 900;
    }
    p {
      margin: 0 0 20px;
      font-size: 13px;
      color: #475569;
      line-height: 1.5;
    }
    button {
      background: #059669;
      color: #ffffff;
      border: 0;
      border-radius: 14px;
      padding: 12px 20px;
      font-size: 13px;
      font-weight: 800;
      cursor: pointer;
      width: 100%;
    }
    button:hover {
      background: #047857;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">SWARAJ GROCERY • KATOL</div>
    <h1>No Internet Connection</h1>
    <p>Check your mobile data or Wi-Fi connection to browse fresh groceries, pack variants, and place your order.</p>
    <button type="button" onclick="window.location.reload()">🔄 Try Again</button>
  </div>
</body>
</html>`;

  return new Response(html, {
    status: 503,
    statusText: 'Service Unavailable',
    headers: { 'Content-Type': 'text/html; charset=UTF-8' }
  });
}

self.addEventListener('fetch', function (event) {
  const req = event.request;

  // Never intercept non-GET requests (like checkout/login POST forms)
  if (req.method !== 'GET') {
    return;
  }

  const url = new URL(req.url);

  // Page navigations: Network-first with friendly offline fallback
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(function () {
        return caches.match(req).then(function (cached) {
          return cached || offlineFallbackResponse();
        });
      })
    );
    return;
  }

  // Same-origin static assets: Stale-while-revalidate
  if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then(function (cached) {
        const networkFetch = fetch(req).then(function (res) {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(req, copy);
            });
          }
          return res;
        }).catch(function () {
          return cached;
        });
        return cached || networkFetch;
      })
    );
  }
});