// Service Worker for Kangourou des Mathématiques PWA
// Cache version — bump to force update
var CACHE_VERSION = 'v1';
var CACHE_NAME = 'kangourou-' + CACHE_VERSION;

// Install: cache the app shell (HTML + manifest)
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll([
        './',
        './index.html',
        './manifest.json'
      ]);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

// Activate: clean up old caches
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(name) {
          return name.startsWith('kangourou-') && name !== CACHE_NAME;
        }).map(function(name) {
          return caches.delete(name);
        })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// Fetch: cache-first for images, network-first for HTML
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Images: cache-first (they never change)
  if (event.request.url.match(/\.png$/)) {
    event.respondWith(
      caches.match(event.request).then(function(cached) {
        if (cached) return cached;
        return fetch(event.request).then(function(response) {
          if (response.ok) {
            var clone = response.clone();
            caches.open(CACHE_NAME).then(function(cache) {
              cache.put(event.request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // HTML/other: network-first (so updates are picked up)
  event.respondWith(
    fetch(event.request).then(function(response) {
      if (response.ok) {
        var clone = response.clone();
        caches.open(CACHE_NAME).then(function(cache) {
          cache.put(event.request, clone);
        });
      }
      return response;
    }).catch(function() {
      return caches.match(event.request);
    })
  );
});

// Listen for messages from the page
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'PRELOAD_IMAGES') {
    var urls = event.data.urls;
    var preloadPromise = caches.open(CACHE_NAME).then(function(cache) {
      var processed = 0;
      var cached = 0;
      var cursor = 0;
      var total = urls.length;

      function grabNext() {
        var i = cursor++;
        if (i >= total) return Promise.resolve();
        return cache.match(urls[i]).then(function(existing) {
          if (existing) {
            cached++;
            return;
          }
          return fetch(urls[i]).then(function(response) {
            if (response.ok) {
              return cache.put(urls[i], response).then(function() {
                cached++;
              });
            }
          });
        }).catch(function() {
          // skip failures
        }).then(function() {
          processed++;
          report();
          return grabNext();
        });
      }

      function report() {
        self.clients.matchAll().then(function(clients) {
          clients.forEach(function(client) {
            client.postMessage({ type: 'PRELOAD_PROGRESS', processed: processed, cached: cached, total: total });
          });
        });
      }

      // Run 4 parallel download workers
      var workers = [];
      for (var s = 0; s < 4; s++) workers.push(grabNext());
      return Promise.all(workers).then(function() {
        report();
      });
    });
    event.waitUntil(preloadPromise);
  }
});
