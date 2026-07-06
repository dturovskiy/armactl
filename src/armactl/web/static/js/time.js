(() => {
  const formatterOptions = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  };

  function formatLocalTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat([], formatterOptions).format(date);
    } catch (_error) {
      return "";
    }
  }

  function setLocalTime(node, value) {
    if (!node) return;
    const timestamp = value || node.getAttribute("datetime") || "";
    if (!timestamp) return;

    let timeNode = node;
    if (node.tagName !== "TIME") {
      timeNode = node.querySelector("time[data-local-time]");
      if (!timeNode) {
        timeNode = document.createElement("time");
        timeNode.dataset.localTime = "";
        node.replaceChildren(timeNode);
      }
    }

    timeNode.setAttribute("datetime", timestamp);
    timeNode.setAttribute("title", timestamp);
    const formatted = formatLocalTime(timestamp);
    timeNode.textContent = formatted || timestamp;
  }

  function formatLocalTimes(root = document) {
    root.querySelectorAll("time[data-local-time]").forEach((node) => {
      setLocalTime(node);
    });
  }

  window.armactlFormatLocalTime = formatLocalTime;
  window.armactlSetLocalTime = setLocalTime;
  window.armactlFormatLocalTimes = formatLocalTimes;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => formatLocalTimes());
  } else {
    formatLocalTimes();
  }
})();
