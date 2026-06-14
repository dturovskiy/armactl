(() => {
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      window.location.reload();
    }
  });

  const headers = {
    Accept: "application/json",
    "X-Requested-With": "fetch",
  };

  const oppositeTheme = (theme) => (theme === "dark" ? "light" : "dark");

  const postPreference = async (form) => {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers,
      credentials: "same-origin",
      redirect: "manual",
    });
    if (!response.ok) {
      throw new Error("Preference update failed");
    }
    return response.json();
  };

  const updateThemeButton = (form, nextTheme) => {
    const input = form.querySelector("[data-theme-input]");
    const button = form.querySelector("[data-theme-button]");
    if (input) {
      input.value = nextTheme;
    }
    if (button) {
      const prefix = button.dataset.themeLabelPrefix || "Theme";
      const label = nextTheme === "dark" ? button.dataset.themeLabelDark : button.dataset.themeLabelLight;
      button.textContent = `${prefix}: ${label || nextTheme}`;
    }
  };

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    const preference = form.dataset.preferenceForm;
    if (preference === "theme") {
      event.preventDefault();
      const input = form.querySelector("[data-theme-input]");
      const requestedTheme = input ? input.value : oppositeTheme(document.documentElement.dataset.theme);
      const previousTheme = document.documentElement.dataset.theme || "light";
      const persist = postPreference(form);
      document.documentElement.dataset.theme = requestedTheme;
      updateThemeButton(form, oppositeTheme(requestedTheme));
      persist.catch(() => {
        document.documentElement.dataset.theme = previousTheme;
        updateThemeButton(form, requestedTheme);
      });
      return;
    }

    if (preference === "language") {
      event.preventDefault();
      postPreference(form)
        .then(() => {
          window.location.reload();
        })
        .catch(() => {
          form.submit();
        });
    }
  });
})();
