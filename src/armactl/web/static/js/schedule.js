(() => {
  const forms = document.querySelectorAll("[data-schedule-time-form]");

  const pad = (value) => String(value).padStart(2, "0");

  const nextTime = (rows) => {
    if (rows.length === 0) {
      return "06:00";
    }
    const lastInput = rows[rows.length - 1].querySelector('input[type="time"]');
    const current = lastInput && lastInput.value ? lastInput.value : "06:00";
    const [hour, minute] = current.split(":").map((part) => Number.parseInt(part, 10));
    if (Number.isNaN(hour) || Number.isNaN(minute)) {
      return "06:00";
    }
    return `${pad((hour + 6) % 24)}:${pad(minute)}`;
  };

  forms.forEach((form) => {
    const list = form.querySelector("[data-schedule-time-list]");
    const template = form.querySelector("[data-schedule-time-template]");
    const addButton = form.querySelector("[data-add-schedule-time]");
    const emptyNote = form.querySelector("[data-empty-schedule-time]");
    if (!list || !template || !addButton) {
      return;
    }

    const maxTimes = Number.parseInt(list.dataset.maxTimes || "3", 10);
    const rows = () => Array.from(list.querySelectorAll("[data-schedule-time-row]"));

    const refresh = () => {
      const currentRows = rows();
      if (emptyNote) {
        emptyNote.hidden = currentRows.length > 0;
      }
      addButton.disabled = currentRows.length >= maxTimes;
    };

    const addRow = (value = "") => {
      const currentRows = rows();
      if (currentRows.length >= maxTimes) {
        return;
      }
      const fragment = template.content.cloneNode(true);
      const row = fragment.querySelector("[data-schedule-time-row]");
      const input = fragment.querySelector('input[type="time"]');
      if (input) {
        input.value = value || nextTime(currentRows);
      }
      list.appendChild(fragment);
      refresh();
      const addedInput = row && row.querySelector('input[type="time"]');
      if (addedInput) {
        addedInput.focus();
      }
    };

    addButton.addEventListener("click", () => addRow());

    list.addEventListener("click", (event) => {
      const target = event.target.closest("[data-remove-schedule-time]");
      if (!target) {
        return;
      }
      const row = target.closest("[data-schedule-time-row]");
      if (row) {
        row.remove();
        refresh();
      }
    });

    refresh();
  });
})();
