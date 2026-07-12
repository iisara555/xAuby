(() => {
  const sourceUrl = "https://github.com/iisara555/xAuby";

  const updateLinks = () => {
    let changed = false;

    document.querySelectorAll(`a[href^="${sourceUrl}"]`).forEach((link) => {
      const label = link.textContent?.trim() ?? "";

      if (label.includes("Inspect")) {
        link.href = "#research";
        link.textContent = "Read the research";
      } else if (label.includes("View source")) {
        link.href = "#system";
        link.textContent = "Explore the system →";
      } else if (label.includes("WebUI")) {
        link.href = "#dashboard";
        link.textContent = "Explore the dashboard";
      } else {
        link.href = "#start";
        link.textContent = "Get started";
      }

      changed = true;
    });

    return changed;
  };

  const observer = new MutationObserver(() => {
    if (updateLinks()) observer.disconnect();
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
  updateLinks();
  window.setTimeout(() => observer.disconnect(), 10000);
})();
