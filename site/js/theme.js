// theme.js — Light / dark theme management

(function () {
  const KEY = "archive-theme";
  const html = document.documentElement;
  const toggle = document.getElementById("themeToggle");

  function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    html.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, theme);
  }

  function initTheme() {
    const stored = localStorage.getItem(KEY);
    const theme = stored || getSystemTheme();
    applyTheme(theme);
  }

  function toggleTheme() {
    const current = html.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
  }

  toggle.addEventListener("click", toggleTheme);

  // Listen for system theme changes (only if user hasn't manually set)
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    if (!localStorage.getItem(KEY)) {
      applyTheme(getSystemTheme());
    }
  });

  // Apply immediately to avoid FOUC
  const stored = localStorage.getItem(KEY);
  html.setAttribute("data-theme", stored || getSystemTheme());
})();
