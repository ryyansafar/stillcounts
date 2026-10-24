// Caches the app shell so the ledger opens and works with no network at all.
// Strategy: network first (so updates arrive when there is a signal), cache as the fallback after a short timeout.
var CACHE = "stillcounts-v4";
var SHELL = ["/", "/static/crdt.js", "/static/app.js", "/static/style.css", "/static/manifest.webmanifest",
             "/static/learn.html", "/static/learn.js", "/static/learn.css"];
var NETWORK_TIMEOUT_MS = 2500;

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function fromNetwork(request) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () { reject(new Error("timeout")); }, NETWORK_TIMEOUT_MS);
    fetch(request).then(function (res) {
      clearTimeout(timer);
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(request, copy); });
      resolve(res);
    }, function (err) { clearTimeout(timer); reject(err); });
  });
}

self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.indexOf("/api/") === 0) return;
  e.respondWith(fromNetwork(e.request).catch(function () {
    return caches.match(e.request).then(function (hit) { return hit || new Response("offline", { status: 503 }); });
  }));
});
