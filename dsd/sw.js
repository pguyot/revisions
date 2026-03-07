// Service Worker for DSD I Prüfungstraining PWA
// Cache version — bump to force update
var CACHE_VERSION = 'v2';
var CACHE_NAME = 'dsd-' + CACHE_VERSION;

// Install: cache the app shell
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll([
        './',
        './index.html',
        './manifest.json',
        './data.json',
        './audio/manifest.json'
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
          return name.startsWith('dsd-') && name !== CACHE_NAME;
        }).map(function(name) {
          return caches.delete(name);
        })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// Fetch: cache-first for audio/images, network-first for HTML/JSON
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Audio & images: cache-first (they never change)
  if (url.pathname.match(/\.(mp3|png)$/)) {
    event.respondWith(
      caches.match(event.request).then(function(cached) {
        if (cached) return cached;
        return fetch(event.request).then(function(response) {
          if (response.ok) {
            var clone = response.clone();
            event.waitUntil(
              caches.open(CACHE_NAME).then(function(cache) {
                return cache.put(event.request, clone);
              })
            );
          }
          return response;
        });
      })
    );
    return;
  }

  // HTML/JSON/other: network-first (so updates are picked up)
  event.respondWith(
    fetch(event.request).then(function(response) {
      if (response.ok) {
        var clone = response.clone();
        event.waitUntil(
          caches.open(CACHE_NAME).then(function(cache) {
            return cache.put(event.request, clone);
          })
        );
      }
      return response;
    }).catch(function() {
      return caches.match(event.request);
    })
  );
});

// Listen for messages from the page
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'PRELOAD_ASSETS') {
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
        return self.clients.matchAll().then(function(clients) {
          clients.forEach(function(client) {
            client.postMessage({ type: 'PRELOAD_PROGRESS', processed: processed, cached: cached, total: total });
          });
        });
      }

      // Run 4 parallel download workers
      var workers = [];
      for (var s = 0; s < 4; s++) workers.push(grabNext());
      return Promise.all(workers).then(function() {
        return report();
      });
    });
    event.waitUntil(preloadPromise);
  }
});
