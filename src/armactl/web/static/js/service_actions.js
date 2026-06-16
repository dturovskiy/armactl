(() => {
  const forms = document.querySelectorAll("[data-service-action-form]");
  if (!forms.length) {
    return;
  }

  let overlay = null;

  function buildOverlay() {
    if (overlay) {
      return overlay;
    }

    overlay = document.createElement("div");
    overlay.className = "service-action-overlay";
    overlay.setAttribute("role", "status");
    overlay.setAttribute("aria-live", "assertive");

    const panel = document.createElement("div");
    panel.className = "service-action-overlay-panel";

    const spinner = document.createElement("span");
    spinner.className = "service-action-spinner";
    spinner.setAttribute("aria-hidden", "true");

    const message = document.createElement("p");
    message.dataset.serviceActionOverlayMessage = "";

    panel.append(spinner, message);
    overlay.append(panel);
    document.body.append(overlay);
    return overlay;
  }

  function showOverlay(message) {
    const activeOverlay = buildOverlay();
    const label = activeOverlay.querySelector("[data-service-action-overlay-message]");
    if (label) {
      label.textContent = message || "Running service action...";
    }
    activeOverlay.classList.add("is-visible");
  }

  function lockForm(form) {
    form.dataset.serviceActionSubmitting = "true";
    form.classList.add("service-action-submitting");
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
    form.querySelectorAll("input, select, textarea").forEach((control) => {
      control.setAttribute("aria-disabled", "true");
    });
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-service-action-form]")) {
      return;
    }

    if (form.dataset.serviceActionSubmitting === "true") {
      event.preventDefault();
      return;
    }

    if (typeof form.reportValidity === "function" && !form.reportValidity()) {
      return;
    }
    if (typeof form.checkValidity === "function" && !form.checkValidity()) {
      return;
    }

    showOverlay(form.dataset.serviceActionLabel || "Running service action...");
    lockForm(form);
  });
})();