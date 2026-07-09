(() => {
  const tokenEndpoint = "/auth/csrf-token";
  const tokenSelector = 'input[name="csrf_token"]';
  const submitterCloneAttribute = "data-csrf-submit-clone";

  const isSameOrigin = (url) => {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch {
      return false;
    }
  };

  const shouldRefreshForm = (form) => {
    if ((form.method || "").toLowerCase() !== "post") {
      return false;
    }
    if (form.dataset.csrfRefresh === "off") {
      return false;
    }
    if (form.dataset.preferenceForm) {
      return false;
    }
    const action = form.getAttribute("action") || window.location.href;
    if (!isSameOrigin(action)) {
      return false;
    }
    if (new URL(action, window.location.href).pathname === "/login") {
      return false;
    }
    return Boolean(form.querySelector(tokenSelector));
  };

  const refreshToken = async () => {
    const response = await fetch(tokenEndpoint, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
    });
    if (!response.ok) {
      throw new Error("CSRF refresh failed");
    }
    const payload = await response.json();
    if (!payload || typeof payload.csrf_token !== "string" || payload.csrf_token.length === 0) {
      throw new Error("CSRF refresh returned no token");
    }
    return payload.csrf_token;
  };

  const updateFormTokens = (token) => {
    document.querySelectorAll(tokenSelector).forEach((input) => {
      input.value = token;
    });
  };

  const removeSubmitterClones = (form) => {
    form.querySelectorAll(`[${submitterCloneAttribute}]`).forEach((input) => {
      input.remove();
    });
  };

  const preserveSubmitter = (form, submitter) => {
    removeSubmitterClones(form);
    if (!(submitter instanceof HTMLElement)) {
      return;
    }
    const name = submitter.getAttribute("name");
    if (!name) {
      return;
    }
    const clone = document.createElement("input");
    clone.type = "hidden";
    clone.name = name;
    clone.value = submitter.getAttribute("value") || "";
    clone.setAttribute(submitterCloneAttribute, "true");
    form.appendChild(clone);
  };

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !shouldRefreshForm(form)) {
      return;
    }
    if (form.dataset.csrfSubmitting === "true") {
      return;
    }

    event.preventDefault();
    try {
      updateFormTokens(await refreshToken());
    } catch {
      // Keep CSRF fail-closed: submit the original token and let the server reject it.
    }
    preserveSubmitter(form, event.submitter);
    form.dataset.csrfSubmitting = "true";
    HTMLFormElement.prototype.submit.call(form);
  });
})();
