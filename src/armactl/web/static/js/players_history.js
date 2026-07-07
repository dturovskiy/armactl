(() => {
  const toggles = document.querySelectorAll("[data-player-history-toggle]");

  function setExpanded(button, expanded) {
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function rowsForEvent(eventId) {
    return Array.from(document.querySelectorAll("[data-player-history-row]")).filter(
      (row) => row.dataset.playerHistoryEvent === eventId,
    );
  }

  function buttonsForEvent(eventId) {
    return Array.from(document.querySelectorAll("[data-player-history-toggle]")).filter(
      (eventButton) => eventButton.dataset.playerHistoryEvent === eventId,
    );
  }

  toggles.forEach((button) => {
    const targetId = button.dataset.playerHistoryTarget;
    const eventId = button.dataset.playerHistoryEvent;
    if (!targetId || !eventId) {
      return;
    }

    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }

    button.addEventListener("click", () => {
      const shouldOpen = target.hidden;

      rowsForEvent(eventId).forEach((row) => {
        row.hidden = true;
      });
      buttonsForEvent(eventId).forEach((eventButton) => {
        setExpanded(eventButton, false);
      });

      if (shouldOpen) {
        target.hidden = false;
        setExpanded(button, true);
      }
    });
  });
})();
