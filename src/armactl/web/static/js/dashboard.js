(() => {
  const root = document.querySelector("[data-dashboard-root]");
  if (!root) {
    return;
  }

  const endpoint = root.dataset.dashboardEndpoint || "/dashboard/status.json";
  const parsedInterval = Number.parseInt(root.dataset.dashboardIntervalMs || "7000", 10);
  const intervalMs = Number.isFinite(parsedInterval)
    ? Math.min(Math.max(parsedInterval, 5000), 10000)
    : 7000;
  const liveStatus = document.querySelector("[data-dashboard-live-status]");

  function setLiveStatus(stale) {
    if (!liveStatus) {
      return;
    }
    liveStatus.dataset.stale = stale ? "true" : "false";
    liveStatus.textContent = stale
      ? liveStatus.dataset.staleLabel || "Dashboard data may be stale."
      : liveStatus.dataset.freshLabel || "Live refresh active";
  }

  function setFieldValue(field, value) {
    if (!field || value === undefined || value === null) {
      return;
    }
    document
      .querySelectorAll(`[data-dashboard-field="${field}"]`)
      .forEach((node) => {
        node.textContent = String(value);
      });
  }

  function updateLifecycleClass(lifecycle) {
    document.querySelectorAll("[data-dashboard-lifecycle-class]").forEach((node) => {
      node.classList.forEach((className) => {
        if (className.startsWith("state-")) {
          node.classList.remove(className);
        }
      });
      node.classList.add(`state-${lifecycle || "unknown"}`);
    });
  }

  function applyStatus(data) {
    if (!data || data.ok !== true || !data.fields) {
      throw new Error("Dashboard status payload is invalid.");
    }
    Object.entries(data.fields).forEach(([field, value]) => setFieldValue(field, value));
    updateLifecycleClass(data.lifecycle);
    root.dataset.dashboardLifecycle = data.lifecycle || "unknown";
    setLiveStatus(false);
  }

  async function refreshDashboard() {
    try {
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Dashboard status request failed: ${response.status}`);
      }
      applyStatus(await response.json());
    } catch (_error) {
      setLiveStatus(true);
    }
  }

  window.setTimeout(refreshDashboard, 1000);
  window.setInterval(refreshDashboard, intervalMs);
})();
