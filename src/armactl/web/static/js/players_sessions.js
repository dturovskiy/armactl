(() => {
  const toggles = document.querySelectorAll("[data-player-session-toggle]");

  function setExpanded(button, expanded) {
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  toggles.forEach((button) => {
    const targetId = button.dataset.playerSessionTarget;
    if (!targetId) {
      return;
    }

    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }

    button.addEventListener("click", () => {
      const shouldOpen = target.hidden;
      target.hidden = !shouldOpen;
      setExpanded(button, shouldOpen);
    });
  });
})();
