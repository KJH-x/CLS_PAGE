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

  function scoreMatch(item, token) {
    let score = 0;
    if (item.text && item.text.toLowerCase().includes(token)) score += 3;
    if (item.tags && item.tags.some(function (t) { return t.toLowerCase().includes(token); })) score += 2;
    if (item.fullText && item.fullText.toLowerCase().includes(token)) score += 1;
    if (item.date && item.date.includes(token)) score += 0.5;
    if (item.dynamicId && item.dynamicId.includes(token)) score += 0.5;
    return score;
  }

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
      statusEl.textContent = "搜索索引未加载";
      noResults.hidden = true;
      return;
    }

    const tokens = query.split(/\s+/).filter(Boolean);
    const scored = searchIndex.map(function (item) {
      let s = 0;
      tokens.forEach(function (tok) { s += scoreMatch(item, tok); });
      return { item: item, score: s };
    });
    const matched = scored
      .filter(function (e) { return e.score > 0; })
      .sort(function (a, b) { return b.score - a.score; })
      .map(function (e) { return e.item; });

    const ids = new Set(matched.map(function (m) { return m.dynamicId; }));

    let statusText = matched.length + " / " + searchIndex.length + " 条";
    const accountCounts = {};
    let anyAccount = false;
    matched.forEach(function (m) {
      if (m._account) {
        anyAccount = true;
        accountCounts[m._account] = (accountCounts[m._account] || 0) + 1;
      }
    });
    if (anyAccount) {
      const parts = Object.keys(accountCounts).map(function (a) { return a + " " + accountCounts[a]; });
      statusText += "（" + parts.join(" · ") + "）";
    }
    if (matched.length === 0) {
      const hasFullText = searchIndex.some(function (m) {
        return !!(m.fullText && String(m.fullText).length);
      });
      if (!hasFullText) statusText += " · 当前索引不含正文";
    }

    statusEl.textContent = statusText;
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
