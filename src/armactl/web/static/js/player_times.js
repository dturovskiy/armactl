(() => {
  const formatLocalTime = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat([], {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
    } catch (_error) {
      return "";
    }
  };

  document.querySelectorAll("time[data-local-time]").forEach((node) => {
    const value = node.getAttribute("datetime") || "";
    const formatted = formatLocalTime(value);
    if (formatted) {
      node.textContent = formatted;
    }
  });
})();
