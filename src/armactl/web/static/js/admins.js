(() => {
  function trimField(field) {
    const trimmed = field.value.trim();
    if (field.value !== trimmed) {
      field.value = trimmed;
    }
  }

  function setupAdminFormSanitizer() {
    const forms = document.querySelectorAll("[data-admin-edit-form]");
    forms.forEach((form) => {
      const fields = Array.from(
        form.querySelectorAll('input[name="admin_reference"], input[name="label"]'),
      );
      fields.forEach((field) => {
        field.addEventListener("paste", () => {
          window.setTimeout(() => trimField(field), 0);
        });
        field.addEventListener("change", () => trimField(field));
      });
      form.addEventListener("submit", () => {
        fields.forEach(trimField);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupAdminFormSanitizer);
  } else {
    setupAdminFormSanitizer();
  }
})();