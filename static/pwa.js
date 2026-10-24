(function () {
  "use strict";

  // Register the service worker (needs HTTPS, or localhost while developing).
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function (err) {
        console.warn("Service worker registration failed:", err);
      });
    });
  }

  // "Install" button: shown only when the browser says the app can be installed.
  // (iPhone/iPad Safari never shows this: use Share > Add to Home Screen there.)
  var installBtn = document.getElementById("install-btn");
  var deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    if (installBtn) installBtn.hidden = false;
  });

  if (installBtn) {
    installBtn.addEventListener("click", async function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      installBtn.hidden = true;
    });
  }

  window.addEventListener("appinstalled", function () {
    if (installBtn) installBtn.hidden = true;
  });
})();
