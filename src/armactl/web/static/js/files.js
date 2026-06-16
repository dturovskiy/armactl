(function () {
  const browser = document.querySelector("[data-file-browser]");
  const previewTarget = document.querySelector("[data-file-preview-target]");

  if (!browser || !previewTarget || !window.DOMParser || !window.fetch) {
    return;
  }

  function previewUrl(url) {
    const next = new URL(url, window.location.href);
    next.hash = "";
    return next.toString();
  }

  function setPreviewBusy() {
    const loadingText = previewTarget.dataset.loadingText || "Loading preview...";
    previewTarget.setAttribute("aria-busy", "true");
    previewTarget.replaceChildren(previewMessage(loadingText, "muted"));
  }

  function setPreviewError() {
    const errorText = previewTarget.dataset.errorText || "Preview could not be loaded.";
    previewTarget.replaceChildren(previewMessage(errorText, "panel notice-panel file-state"));
  }

  function previewMessage(text, className) {
    const section = document.createElement("section");
    const message = document.createElement("p");
    section.className = "file-preview-block";
    message.className = className;
    message.textContent = text;
    section.appendChild(message);
    return section;
  }

  async function loadPreview(link) {
    setPreviewBusy();

    const response = await fetch(previewUrl(link.href), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    });
    if (!response.ok) {
      throw new Error("preview request failed");
    }

    const html = await response.text();
    const documentFragment = new DOMParser().parseFromString(html, "text/html");
    const nextPreview = documentFragment.querySelector("[data-file-preview-target]");
    if (!nextPreview) {
      throw new Error("preview target missing");
    }

    previewTarget.innerHTML = nextPreview.innerHTML;
    previewTarget.removeAttribute("aria-busy");
    window.history.replaceState(null, "", link.href);
    previewTarget.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  browser.addEventListener("click", function (event) {
    const link = event.target.closest("[data-file-preview-link]");
    if (!link || event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }

    event.preventDefault();
    loadPreview(link).catch(function () {
      previewTarget.removeAttribute("aria-busy");
      setPreviewError();
    });
  });
})();
