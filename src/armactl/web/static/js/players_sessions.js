(() => {
  const toggles = document.querySelectorAll("[data-player-session-toggle]");
  const filterForm = document.querySelector("[data-player-session-filter-form]");

  function setExpanded(button, expanded) {
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  toggles.forEach((button) => {
    const targetId = button.dataset.playerSessionTarget;
    if (!targetId) {
      return;
    }

    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }

    button.addEventListener("click", () => {
      const shouldOpen = target.hidden;
      target.hidden = !shouldOpen;
      setExpanded(button, shouldOpen);
    });
  });

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function browserLocalInputValue(date) {
    return [
      date.getFullYear(),
      "-",
      pad(date.getMonth() + 1),
      "-",
      pad(date.getDate()),
      "T",
      pad(date.getHours()),
      ":",
      pad(date.getMinutes()),
      ":",
      pad(date.getSeconds()),
    ].join("");
  }

  function hasExplicitTimezone(value) {
    return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  }

  if (filterForm) {
    const localInputs = filterForm.querySelectorAll(
      "[data-player-session-local-input]",
    );

    localInputs.forEach((input) => {
      const targetId = input.dataset.utcTarget;
      const hiddenInput = targetId ? document.getElementById(targetId) : null;
      if (!hiddenInput || !hiddenInput.value || !hasExplicitTimezone(hiddenInput.value)) {
        return;
      }
      const date = new Date(hiddenInput.value);
      if (!Number.isNaN(date.getTime())) {
        input.value = browserLocalInputValue(date);
      }
    });

    filterForm.addEventListener("submit", () => {
      localInputs.forEach((input) => {
        const targetId = input.dataset.utcTarget;
        const hiddenInput = targetId ? document.getElementById(targetId) : null;
        if (!hiddenInput) {
          return;
        }
        if (!input.value) {
          hiddenInput.value = "";
          return;
        }
        const localDate = new Date(input.value);
        hiddenInput.value = Number.isNaN(localDate.getTime())
          ? ""
          : localDate.toISOString();
      });
    });
  }
})();
