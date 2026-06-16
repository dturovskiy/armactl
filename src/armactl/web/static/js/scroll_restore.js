(function () {
  const storageKey = "armactl.web.scroll";
  const ttlMs = 15000;
  const preservedRoots = new Set([
    "admins",
    "bot",
    "config",
    "files",
    "jobs",
    "logs",
    "mods",
    "players",
    "schedule",
  ]);

  function rootName(pathname) {
    const first = pathname.split("/").filter(Boolean)[0] || "";
    return preservedRoots.has(first) ? first : "";
  }

  function sameToolPage(url) {
    if (url.origin !== window.location.origin) {
      return false;
    }
    const currentRoot = rootName(window.location.pathname);
    return currentRoot !== "" && currentRoot === rootName(url.pathname);
  }

  function saveScrollFor(url) {
    if (!sameToolPage(url)) {
      return;
    }
    try {
      window.sessionStorage.setItem(
        storageKey,
        JSON.stringify({
          path: url.pathname,
          x: window.scrollX,
          y: window.scrollY,
          createdAt: Date.now(),
        }),
      );
    } catch {
      // Ignore storage failures; navigation must keep working.
    }
  }

  function restoreScroll() {
    let saved;
    try {
      saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
      window.sessionStorage.removeItem(storageKey);
    } catch {
      return;
    }
    if (!saved || saved.path !== window.location.pathname) {
      return;
    }
    if (Date.now() - Number(saved.createdAt || 0) > ttlMs) {
      return;
    }
    window.requestAnimationFrame(() => {
      window.scrollTo(Number(saved.x || 0), Number(saved.y || 0));
    });
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("download")) {
      return;
    }
    const href = link.getAttribute("href") || "";
    if (href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) {
      return;
    }
    saveScrollFor(new URL(link.href, window.location.href));
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const method = String(form.method || "get").toLowerCase();
    if (method !== "get") {
      return;
    }
    saveScrollFor(new URL(form.action || window.location.href, window.location.href));
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restoreScroll, { once: true });
  } else {
    restoreScroll();
  }
})();
