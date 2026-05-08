// search.js — Client-side search filtering

(function () {
  const input = document.getElementById("searchInput");
  const clearBtn = document.getElementById("searchClear");
  const statusEl = document.getElementById("searchStatus");
  const noResults = document.getElementById("noResults");
  const clearSearchBtn = document.getElementById("clearSearchBtn");

  let searchIndex = null;
  let onFilterCallback = null;

  window.initSearch = function (indexData, filterFn) {
    searchIndex = indexData;
    onFilterCallback = filterFn;
  };

  function doSearch() {
    const query = input.value.trim().toLowerCase();
    if (!query) {
      clearBtn.hidden = true;
      statusEl.textContent = "";
      noResults.hidden = true;
      if (onFilterCallback) onFilterCallback(null);
      return;
    }

    clearBtn.hidden = false;

    if (!searchIndex || !searchIndex.length) {
      statusEl.textContent = "No index loaded";
      noResults.hidden = true;
      return;
    }

    const tokens = query.split(/\s+/).filter(Boolean);
    const matched = searchIndex.filter(function (item) {
      return tokens.every(function (token) {
        if (item.text && item.text.toLowerCase().includes(token)) return true;
        if (item.date && item.date.includes(token)) return true;
        if (item.tags && item.tags.some(function (t) { return t.toLowerCase().includes(token); })) return true;
        if (item.dynamicId && item.dynamicId.includes(token)) return true;
        return false;
      });
    });

    const ids = new Set(matched.map(function (m) { return m.dynamicId; }));
    statusEl.textContent = matched.length + " / " + searchIndex.length + " 条";
    noResults.hidden = matched.length > 0;

    if (onFilterCallback) onFilterCallback(ids);
  }

  input.addEventListener("input", doSearch);

  clearBtn.addEventListener("click", function () {
    input.value = "";
    doSearch();
    input.focus();
  });

  clearSearchBtn.addEventListener("click", function () {
    input.value = "";
    doSearch();
  });
})();
