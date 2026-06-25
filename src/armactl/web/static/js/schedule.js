(() => {
  const forms = document.querySelectorAll("[data-schedule-time-form]");
  const localDisplays = document.querySelectorAll("[data-schedule-local-display]");

  const pad = (value) => String(value).padStart(2, "0");

  const browserTimeZone = () => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (_error) {
      return "UTC";
    }
  };

  const todayUtcDate = () => new Date().toISOString().slice(0, 10);

  const localTimeFromUtc = (utcTime, timeZone, referenceDate) => {
    if (!utcTime || !timeZone) {
      return utcTime || "";
    }
    const date = new Date((referenceDate || todayUtcDate()) + "T" + utcTime + ":00Z");
    if (Number.isNaN(date.getTime())) {
      return utcTime;
    }
    try {
      const parts = new Intl.DateTimeFormat([], {
        hour: "2-digit",
        hourCycle: "h23",
        minute: "2-digit",
        timeZone,
      }).formatToParts(date);
      const hour = parts.find((part) => part.type === "hour")?.value || "00";
      const minute = parts.find((part) => part.type === "minute")?.value || "00";
      return (hour === "24" ? "00" : hour) + ":" + minute;
    } catch (_error) {
      return utcTime;
    }
  };

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
    return pad((hour + 6) % 24) + ":" + pad(minute);
  };

  const timeZone = browserTimeZone();

  localDisplays.forEach((display) => {
    const utcTimes = (display.dataset.utcTimes || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (utcTimes.length === 0) {
      return;
    }
    const referenceDate = display.dataset.scheduleReferenceDate || todayUtcDate();
    const localTimes = utcTimes.map((utcTime) => localTimeFromUtc(utcTime, timeZone, referenceDate));
    display.textContent = localTimes.join(", ") + " " + timeZone;
    display.dataset.scheduleTimezone = timeZone;
  });

  forms.forEach((form) => {
    const list = form.querySelector("[data-schedule-time-list]");
    const template = form.querySelector("[data-schedule-time-template]");
    const addButton = form.querySelector("[data-add-schedule-time]");
    const emptyNote = form.querySelector("[data-empty-schedule-time]");
    const timezoneInput = form.querySelector("[data-schedule-timezone-input]");
    const referenceDateInput = form.querySelector("[data-schedule-reference-date-input]");
    const timezoneLabel = form.querySelector("[data-schedule-timezone-label]");
    if (!list || !template || !addButton) {
      return;
    }

    const referenceDate = form.dataset.scheduleReferenceDate || todayUtcDate();
    if (timezoneInput) {
      timezoneInput.value = timeZone;
    }
    if (referenceDateInput && !referenceDateInput.value) {
      referenceDateInput.value = referenceDate;
    }
    if (timezoneLabel) {
      timezoneLabel.textContent = timeZone;
    }

    list.querySelectorAll('input[type="time"][data-utc-time]').forEach((input) => {
      input.value = localTimeFromUtc(input.dataset.utcTime, timeZone, referenceDate);
    });

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

    form.addEventListener("submit", () => {
      if (timezoneInput) {
        timezoneInput.value = timeZone;
      }
      if (referenceDateInput && !referenceDateInput.value) {
        referenceDateInput.value = referenceDate;
      }
    });

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
