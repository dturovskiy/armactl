(() => {
  function fieldIsDirty(field) {
    if (field.type === "checkbox" || field.type === "radio") {
      return field.checked !== field.defaultChecked;
    }
    return field.value !== field.defaultValue;
  }

  function setupConfigDirtyNotice() {
    const form = document.querySelector("[data-config-edit-form]");
    const note = document.querySelector("[data-config-dirty-note]");
    if (!form || !note) {
      return;
    }

    const fields = Array.from(
      form.querySelectorAll("input[name], textarea[name], select[name]"),
    ).filter((field) => field.name !== "csrf_token");

    const update = () => {
      const dirty = fields.some(fieldIsDirty);
      note.hidden = !dirty;
    };

    for (const field of fields) {
      field.addEventListener("input", update);
      field.addEventListener("change", update);
    }
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupConfigDirtyNotice);
  } else {
    setupConfigDirtyNotice();
  }
})();
