(() => {
  const root = document.querySelector("[data-jobs-refresh-root]");
  if (!root || !window.DOMParser || !window.fetch) {
    return;
  }

  const parsedInterval = Number.parseInt(root.dataset.jobsRefreshIntervalMs || "5000", 10);
  const intervalMs = Number.isFinite(parsedInterval)
    ? Math.min(Math.max(parsedInterval, 3000), 15000)
    : 5000;
  let timer = null;
  let inFlight = false;

  function hasActiveJobs(container) {
    return Boolean(
      container.querySelector(".job-card-queued, .job-card-running, .job-status-queued, .job-status-running")
    );
  }

  function replaceSection(documentFragment, id) {
    const current = document.getElementById(id);
    const next = documentFragment.getElementById(id);
    if (!current || !next) {
      return;
    }
    current.replaceWith(next);
  }

  function stopRefresh() {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
    root.dataset.jobsRefreshActive = "false";
    root.removeAttribute("aria-busy");
  }

  async function refreshJobs() {
    if (inFlight) {
      return;
    }
    inFlight = true;
    root.dataset.jobsRefreshActive = "true";
    root.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(window.location.href, {
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "text/html",
          "X-Requested-With": "fetch",
        },
      });
      if (!response.ok) {
        throw new Error("jobs refresh failed");
      }
      const html = await response.text();
      const documentFragment = new DOMParser().parseFromString(html, "text/html");
      replaceSection(documentFragment, "job-store-integrity");
      replaceSection(documentFragment, "pending-work");
      replaceSection(documentFragment, "background-jobs");
      if (!hasActiveJobs(document)) {
        stopRefresh();
      }
    } catch (_error) {
      root.dataset.jobsRefreshStale = "true";
    } finally {
      inFlight = false;
    }
  }

  if (hasActiveJobs(document)) {
    timer = window.setInterval(refreshJobs, intervalMs);
    window.setTimeout(refreshJobs, Math.min(intervalMs, 1000));
  }
})();