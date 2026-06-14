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
  const metricHistory = new Map();
  const maxHistoryLength = 24;

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

  function clampPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return null;
    }
    return Math.min(Math.max(number, 0), 100);
  }

  function metricText(metric) {
    if (metric && typeof metric.text === "string" && metric.text.trim()) {
      return metric.text;
    }
    return "unknown";
  }

  function updateMetricText(metricId, metric) {
    document.querySelectorAll("[data-dashboard-metric-value]").forEach((node) => {
      if (node.dataset.dashboardMetricValue === metricId) {
        node.textContent = metricText(metric);
      }
    });
  }

  function updateMetricFill(metricId, percent) {
    const width = percent === null ? 0 : percent;
    document.querySelectorAll("[data-dashboard-metric-fill]").forEach((node) => {
      if (node.dataset.dashboardMetricFill === metricId) {
        node.style.width = String(width) + "%";
      }
    });
  }

  function updateMetricContainers(metricId, metric, percent) {
    const unavailable = !metric || metric.available !== true || percent === null;
    document.querySelectorAll("[data-dashboard-meter]").forEach((node) => {
      if (node.dataset.dashboardMeter !== metricId) {
        return;
      }
      node.dataset.metricUnavailable = unavailable ? "true" : "false";
      const bar = node.querySelector(".metric-bar");
      if (bar) {
        bar.setAttribute("aria-valuenow", String(percent === null ? 0 : percent));
      }
    });
  }

  function updateSparkline(metricId, percent) {
    document.querySelectorAll("[data-dashboard-sparkline]").forEach((svg) => {
      if (svg.dataset.dashboardSparkline !== metricId || percent === null) {
        return;
      }
      const history = metricHistory.get(metricId) || [];
      history.push(percent);
      if (history.length > maxHistoryLength) {
        history.shift();
      }
      metricHistory.set(metricId, history);

      const points = history
        .map((value, index) => {
          const x = history.length === 1 ? 100 : (index / (history.length - 1)) * 100;
          const y = 34 - (value / 100) * 32;
          return x.toFixed(2) + "," + y.toFixed(2);
        })
        .join(" ");
      const polyline = svg.querySelector("polyline");
      if (polyline) {
        polyline.setAttribute("points", points);
      }
    });
  }

  function updateMeters(metrics) {
    if (!metrics || typeof metrics !== "object") {
      return;
    }
    Object.entries(metrics).forEach(([metricId, metric]) => {
      const percent = clampPercent(metric && metric.percent);
      updateMetricText(metricId, metric);
      updateMetricFill(metricId, percent);
      updateMetricContainers(metricId, metric, percent);
      updateSparkline(metricId, percent);
    });
  }

  function applyStatus(data) {
    if (!data || data.ok !== true || !data.fields) {
      throw new Error("Dashboard status payload is invalid.");
    }
    Object.entries(data.fields).forEach(([field, value]) => setFieldValue(field, value));
    updateLifecycleClass(data.lifecycle);
    root.dataset.dashboardLifecycle = data.lifecycle || "unknown";
    updateMeters(data.metrics);
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
