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
    const flashMessages = Array.from(
      document.querySelectorAll("[data-config-flash-message]"),
    );
    if (!form || !note) {
      return;
    }
    const hideFlashMessages = () => {
      flashMessages.forEach((message) => { message.hidden = true; });
    };

    const fields = Array.from(
      form.querySelectorAll("input[name], textarea[name], select[name]"),
    ).filter((field) => field.name !== "csrf_token");

    const update = () => {
      const dirty = fields.some(fieldIsDirty);
      note.hidden = !dirty;
      if (dirty) {
        hideFlashMessages();
      }
    };

    for (const field of fields) {
      field.addEventListener("input", update);
      field.addEventListener("change", update);
    }
    update();
    if (flashMessages.length > 0) {
      window.setTimeout(hideFlashMessages, 7000);
    }
  }

  function setupRawConfigReset() {
    const resetButton = document.querySelector("[data-config-raw-reset]");
    const editor = document.querySelector("[data-config-raw-editor]");
    if (!resetButton || !editor) {
      return;
    }

    const loadedConfig = editor.getAttribute("data-loaded-config") ?? editor.defaultValue;
    const form = resetButton.form;
    const confirmation = form ? form.querySelector('input[name="confirm"]') : null;

    resetButton.addEventListener("click", () => {
      editor.value = loadedConfig;
      editor.defaultValue = loadedConfig;
      editor.dispatchEvent(new Event("input", { bubbles: true }));

      if (confirmation) {
        confirmation.checked = false;
        confirmation.defaultChecked = false;
        confirmation.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  }

  function setupConfigPage() {
    setupConfigDirtyNotice();
    setupRawConfigReset();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupConfigPage);
  } else {
    setupConfigPage();
  }
})();
