(() => {
  const STORAGE_KEY = "shortDramaTheme";
  const THEMES = new Set(["default", "sunset"]);

  function normalizeTheme(value) {
    return THEMES.has(value) ? value : "default";
  }

  function applyTheme(theme) {
    const nextTheme = normalizeTheme(theme);
    if (nextTheme === "default") {
      document.documentElement.removeAttribute("data-app-theme");
    } else {
      document.documentElement.setAttribute("data-app-theme", nextTheme);
    }

    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      const active = normalizeTheme(button.dataset.themeChoice) === nextTheme;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  applyTheme(localStorage.getItem(STORAGE_KEY));

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(localStorage.getItem(STORAGE_KEY));
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      button.addEventListener("click", () => {
        const theme = normalizeTheme(button.dataset.themeChoice);
        localStorage.setItem(STORAGE_KEY, theme);
        applyTheme(theme);
      });
    });
  });
})();
