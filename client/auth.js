// The join token. The board's QR code carries it (/?t=...); a phone keeps it and sends it on every request to /api/.
// It is a speed bump against someone else on the venue WiFi, not a login: it travels in clear over plain http.
(function () {
  "use strict";
  var KEY = "sc_token", token = null;
  try { token = localStorage.getItem(KEY); } catch (e) { token = null; }
  try {
    var q = new URLSearchParams(location.search), given = q.get("t");
    if (given) {
      token = given;
      try { localStorage.setItem(KEY, given); } catch (e) { /* the token then lasts for this page only */ }
      q.delete("t");                                     // keep it out of the address bar, history and screenshots
      var rest = q.toString();
      history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash);
    }
  } catch (e) { /* no URLSearchParams or history: leave the page as it is */ }
  window.StillCountsToken = function () { return token; };
  var real = window.fetch;
  if (typeof real !== "function") return;
  window.fetch = function (url, init) {
    if (token && typeof url === "string" && url.indexOf("/api/") === 0) {
      init = Object.assign({}, init);
      init.headers = Object.assign({}, init.headers, { "X-Join-Token": token });
    }
    return real.call(window, url, init);
  };
})();
