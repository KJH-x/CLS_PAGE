// app.js — Main application: fetch data, render timeline, handle interactions

(function () {
  "use strict";

  var R2_BASE = window.ARCHIVE_CONFIG.R2_PUBLIC_URL;

  // DOM refs
  var timeline = document.getElementById("timeline");
  var loadingState = document.getElementById("loadingState");
  var errorState = document.getElementById("errorState");
  var errorMsg = document.getElementById("errorMessage");
  var errorRetry = document.getElementById("errorRetry");
  var emptyState = document.getElementById("emptyState");
  var noResults = document.getElementById("noResults");
  var footerTime = document.getElementById("footerTime");
  var footerCount = document.getElementById("footerCount");

  // State
  var appData = null;       // Full index.json
  var searchData = null;    // search-index.json
  var allCards = [];        // Array of { id, element }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  function escapeHtml(str) {
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function formatDate(dateStr) {
    if (!dateStr) return "";
    // dateStr format: "2024-05-01" or ISO timestamp
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  // ------------------------------------------------------------------
  // States
  // ------------------------------------------------------------------

  function showLoading() {
    timeline.innerHTML = "";
    loadingState.hidden = false;
    errorState.hidden = true;
    emptyState.hidden = true;
    noResults.hidden = true;
  }

  function hideLoading() {
    loadingState.hidden = true;
  }

  function showError(msg) {
    hideLoading();
    errorState.hidden = false;
    errorMsg.textContent = msg || "Failed to load archive data.";
    timeline.innerHTML = "";
  }

  function showEmpty() {
    hideLoading();
    emptyState.hidden = false;
  }

  // ------------------------------------------------------------------
  // Data fetch
  // ------------------------------------------------------------------

  function normalizeDynamic(dyn) {
    // Ensure consistent struct; the date field may be "2024-05-01" or ISO.
    // images array may have missing fields → fill defaults.
    if (!dyn.images) dyn.images = [];
    dyn.images.forEach(function (img) {
      if (!img.storedWidth) img.storedWidth = Math.round((img.originalWidth || 0) / 3);
      if (!img.storedHeight) img.storedHeight = img.originalHeight || 0;
      if (!img.displayWidthScale) img.displayWidthScale = 3;
    });
    return dyn;
  }

  function fetchData() {
    showLoading();

    var indexUrl = R2_BASE + "/site/index.json";
    var searchUrl = R2_BASE + "/site/search-index.json";

    Promise.all([
      fetch(indexUrl).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }),
      fetch(searchUrl).then(function (r) { return r.ok ? r.json() : Promise.resolve(null); }).catch(function () { return null; })
    ])
    .then(function (results) {
      appData = results[0];
      searchData = results[1];
      if (!appData || !appData.dynamics) throw new Error("Invalid index format");

      appData.dynamics = appData.dynamics.map(normalizeDynamic);
      hideLoading();
      setupSearch();
      render();
    })
    .catch(function (err) {
      console.error("Fetch error:", err);
      showError("Could not load archive: " + err.message);
    });
  }

  errorRetry.addEventListener("click", fetchData);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  function render(searchFilterIds) {
    if (!appData || !appData.dynamics || appData.dynamics.length === 0) {
      showEmpty();
      updateFooter();
      return;
    }

    emptyState.hidden = true;
    var dynamics = appData.dynamics;

    // Filter by search if active
    var visibleDynamics = dynamics;
    if (searchFilterIds) {
      visibleDynamics = dynamics.filter(function (d) { return searchFilterIds.has(d.id); });
      noResults.hidden = visibleDynamics.length > 0;
    } else {
      noResults.hidden = true;
    }

    timeline.innerHTML = "";
    allCards = [];

    visibleDynamics.forEach(function (dyn) {
      var card = createCard(dyn);
      timeline.appendChild(card);
      allCards.push({ id: dyn.id, element: card });
    });

    updateFooter();
  }

  function updateFooter() {
    if (appData) {
      footerTime.textContent = appData.generatedAt ? new Date(appData.generatedAt).toLocaleString() : "";
      footerCount.textContent = String(appData.totalDynamics || appData.dynamics.length || 0);
    }
  }

  // ------------------------------------------------------------------
  // Card creation
  // ------------------------------------------------------------------

  function createCard(dyn) {
    var card = document.createElement("article");
    card.className = "dynamic-card";
    card.dataset.id = dyn.id;
    card.setAttribute("role", "listitem");

    // ---- Header ----
    var header = document.createElement("div");
    header.className = "card-header";

    var dateEl = document.createElement("time");
    dateEl.className = "card-date";
    dateEl.textContent = formatDate(dyn.date);

    var titleEl = document.createElement("span");
    titleEl.className = "card-title";
    titleEl.textContent = dyn.text || "(no text)";
    titleEl.title = dyn.fullText || dyn.text || "";

    header.appendChild(dateEl);
    header.appendChild(titleEl);

    // Tags
    if (dyn.tags && dyn.tags.length > 0) {
      var metaEl = document.createElement("span");
      metaEl.className = "card-meta";
      dyn.tags.forEach(function (t) {
        var tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "#" + t;
        metaEl.appendChild(tag);
      });
      header.appendChild(metaEl);
    }

    // Image count
    var countEl = document.createElement("span");
    countEl.className = "image-count-badge";
    countEl.textContent = dyn.imageCount + " pics";
    header.appendChild(countEl);

    // Bilibili link
    var linkEl = document.createElement("a");
    linkEl.className = "bilibili-link";
    linkEl.href = dyn.bilibiliUrl;
    linkEl.target = "_blank";
    linkEl.rel = "noopener noreferrer";
    linkEl.textContent = "Bilibili";
    header.appendChild(linkEl);

    // ---- Expand button ----
    var expandBtn = document.createElement("button");
    expandBtn.className = "expand-btn";
    expandBtn.innerHTML = '<span class="arrow">&#9660;</span> ' + dyn.imageCount + " images";

    // ---- Image container ----
    var imageContainer = document.createElement("div");
    imageContainer.className = "card-images";
    imageContainer.hidden = true;

    // ---- Assemble ----
    card.appendChild(header);
    card.appendChild(expandBtn);
    card.appendChild(imageContainer);

    // ---- Expand / collapse ----
    expandBtn.addEventListener("click", function () {
      var isExpanded = !imageContainer.hidden;

      if (isExpanded) {
        imageContainer.hidden = true;
        card.classList.remove("expanded");
        expandBtn.innerHTML = '<span class="arrow">&#9660;</span> ' + dyn.imageCount + " images";
      } else {
        if (!imageContainer.dataset.loaded) {
          buildImageTrack(imageContainer, dyn);
          imageContainer.dataset.loaded = "1";
        }
        imageContainer.hidden = false;
        card.classList.add("expanded");
        expandBtn.innerHTML = '<span class="arrow">&#9660;</span> Collapse';
      }
    });

    return card;
  }

  // ------------------------------------------------------------------
  // Image track
  // ------------------------------------------------------------------

  function buildImageTrack(container, dyn) {
    var images = dyn.images || [];
    if (images.length === 0) return;

    var track = document.createElement("div");
    track.className = "image-track";

    var tileHeight = getTileHeight();

    images.forEach(function (meta, idx) {
      var tile = createTile(meta, tileHeight, function () {
        // find all images in current gallery context
        var allImages = dyn.images || [];
        window.openViewer(allImages, idx);
      });
      track.appendChild(tile);
    });

    container.appendChild(track);
  }

  function getTileHeight() {
    var style = getComputedStyle(document.documentElement);
    var val = style.getPropertyValue("--tile-height").trim();
    if (val && val.endsWith("px")) {
      return parseFloat(val);
    }
    return 320;
  }

  function createTile(meta, tileHeight, onClick) {
    var origW = meta.originalWidth || (meta.storedWidth * (meta.displayWidthScale || 3));
    var origH = meta.originalHeight || meta.storedHeight;
    var aspect = origH > 0 ? origW / origH : 1;
    var tileWidth = Math.round(tileHeight * aspect);

    var tile = document.createElement("div");
    tile.className = "image-tile";
    tile.style.height = tileHeight + "px";
    tile.style.width = tileWidth + "px";

    var img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = R2_BASE + "/" + (meta.r2Key || "");
    img.style.width = tileWidth + "px";
    img.style.height = tileHeight + "px";

    img.addEventListener("load", function () {
      img.classList.add("loaded");
    });
    img.addEventListener("error", function () {
      img.remove();
      var ph = document.createElement("div");
      ph.className = "tile-placeholder";
      ph.textContent = "🖼";
      tile.appendChild(ph);
    });

    tile.appendChild(img);
    tile.addEventListener("click", onClick);
    return tile;
  }

  // ------------------------------------------------------------------
  // Search integration (called after data loads)
  // ------------------------------------------------------------------

  function setupSearch() {
    window.initSearch(searchData || [], function (filterIds) {
      if (!appData) return;
      if (filterIds === null) {
        render(null);
        document.getElementById("noResults").hidden = true;
        document.getElementById("searchStatus").textContent = "";
      } else {
        render(filterIds);
      }
    });
    // If user already typed before data loaded, re-trigger search
    var input = document.getElementById("searchInput");
    if (input && input.value.trim()) {
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  fetchData();

})();
