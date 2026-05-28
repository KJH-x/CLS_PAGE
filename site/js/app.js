// app.js — Main application: fetch data, render gallery-first timeline

(function () {
  "use strict";

  var R2_BASE = window.ARCHIVE_CONFIG.R2_PUBLIC_URL;
  var PREVIEW_COUNT = 6;

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
  var appData = null;
  var appData = null;
  var searchData = null;
  var currentCategory = "";
  var routeTarget = null;

  function setCategory(cat) {
    currentCategory = cat;
    document.querySelectorAll(".filter-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.cat === cat);
    });
    render();
  }

  function initFilterBar() {
    document.querySelectorAll(".filter-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setCategory(this.dataset.cat);
      });
    });
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  function formatDate(dateStr) {
    if (!dateStr) return "";
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function getActivitySlug(text) {
    var m = text.match(/[｜|](.+?)〓/);
    return m ? m[1].trim() : "";
  }

  // ------------------------------------------------------------------
  // Routing — open a specific dynamic via URL
  // ------------------------------------------------------------------
  var routeTarget = null; // dynamic to open after render

  function checkRoute() {
    if (!appData || !appData.dynamics) return;

    var targetId = null;
    var targetActivity = null;

    var pathMatch = window.location.pathname.match(/\/to\/(.+?)\/?$/);
    if (pathMatch) {
      targetActivity = decodeURIComponent(pathMatch[1]);
    }

    var hashMatch = window.location.hash.match(/^#id-(.+)$/);
    if (hashMatch) {
      targetId = hashMatch[1];
    }

    if (!targetId && !targetActivity) return;

    for (var i = 0; i < appData.dynamics.length; i++) {
      var d = appData.dynamics[i];
      if (targetId && d.id === targetId) { routeTarget = d; break; }
      if (targetActivity && getActivitySlug(d.text) === targetActivity) { routeTarget = d; break; }
    }

    if (routeTarget) {
      // Show all categories so the target isn't hidden
      currentCategory = "";
      document.querySelectorAll(".filter-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.cat === "");
      });
    }
  }

  function navigateToDynamic(dyn) {
    // Rewrite URL bar to clean base URL
    var cleanUrl = window.location.origin + "/";
    if (window.location.pathname !== "/" || window.location.hash) {
      history.replaceState(null, "", cleanUrl);
    }

    requestAnimationFrame(function () {
      var card = document.querySelector('.dynamic-card[data-id="' + dyn.id + '"]');
      if (card) {
        var imgs = card.querySelectorAll('img[loading="lazy"]');
        imgs.forEach(function (img) { img.loading = "eager"; });
        card.scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(function () {
          if (dyn.images && dyn.images.length > 0) {
            window.openViewer(dyn.images, 0);
          }
        }, 600);
      }
    });
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

  function hideLoading() { loadingState.hidden = true; }

  function showError(msg) {
    hideLoading();
    errorState.hidden = false;
    errorMsg.textContent = msg || "无法加载归档数据";
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
    if (!dyn.images) dyn.images = [];
    dyn.images.forEach(function (img) {
      if (!img.storedWidth) img.storedWidth = img.originalWidth || 0;
      if (!img.storedHeight) img.storedHeight = img.originalHeight || 0;
      if (!img.displayWidthScale) img.displayWidthScale = 1;
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
      checkRoute();          // detect route, may override currentCategory
      render();              // render with current filter
      if (routeTarget) {
        navigateToDynamic(routeTarget);
      }
    })
    .catch(function (err) {
      console.error("Fetch error:", err);
      showError("无法加载归档数据：" + err.message);
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

    var visibleDynamics = dynamics;
    if (searchFilterIds) {
      visibleDynamics = dynamics.filter(function (d) { return searchFilterIds.has(d.id); });
      noResults.hidden = visibleDynamics.length > 0;
    } else {
      noResults.hidden = true;
    }

    if (currentCategory) {
      visibleDynamics = visibleDynamics.filter(function (d) {
        return (d.category || "") === currentCategory;
      });
    }

    timeline.innerHTML = "";

    visibleDynamics.forEach(function (dyn) {
      var card = createCard(dyn);
      timeline.appendChild(card);
    });

    updateFooter();
  }

  function updateFooter() {
    if (appData) {
      var timeStr = appData.generatedAt ? new Date(appData.generatedAt).toLocaleString("zh-CN") : "";
      var countStr = String(appData.totalDynamics || appData.dynamics.length || 0);
      footerTime.textContent = timeStr;
      footerCount.textContent = countStr;
      var hc = document.getElementById("headerCount");
      var ht = document.getElementById("headerTime");
      if (hc) hc.textContent = countStr;
      if (ht) ht.textContent = timeStr;
    }
  }

  // ------------------------------------------------------------------
  // Card creation
  // ------------------------------------------------------------------

  function createCard(dyn) {
    var card = document.createElement("article");
    card.className = "dynamic-card";
    card.dataset.id = dyn.id;

    // ---- Header ----
    var header = document.createElement("div");
    header.className = "card-header";

    var dateEl = document.createElement("time");
    dateEl.className = "card-date";
    dateEl.textContent = formatDate(dyn.date);

    var titleEl = document.createElement("h2");
    titleEl.className = "card-title";
    titleEl.textContent = dyn.text || "(无标题)";
    titleEl.title = dyn.fullText || dyn.text || "";

    header.appendChild(dateEl);
    header.appendChild(titleEl);

    // Meta row
    var metaRow = document.createElement("div");
    metaRow.className = "card-meta";

    // Tags: show max 3
    if (dyn.tags && dyn.tags.length > 0) {
      var shown = dyn.tags.slice(0, 3);
      shown.forEach(function (t) {
        var tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "#" + t;
        metaRow.appendChild(tag);
      });
      if (dyn.tags.length > 3) {
        var more = document.createElement("span");
        more.className = "tag";
        more.textContent = "+" + (dyn.tags.length - 3);
        metaRow.appendChild(more);
      }
    }

    var countEl = document.createElement("span");
    countEl.className = "image-count-badge";
    countEl.textContent = dyn.imageCount + " 张";
    metaRow.appendChild(countEl);

    var linkEl = document.createElement("a");
    linkEl.className = "bilibili-link";
    linkEl.href = dyn.bilibiliUrl;
    linkEl.target = "_blank";
    linkEl.rel = "noopener noreferrer";
    linkEl.textContent = "原动态";
    metaRow.appendChild(linkEl);

    header.appendChild(metaRow);

    // ---- Preview grid (always shown, first 6 images) ----
    var imageContainer = document.createElement("div");
    imageContainer.className = "card-images";
    var imagesId = "images-" + dyn.id;
    imageContainer.id = imagesId;

    if (dyn.images && dyn.images.length > 0) {
      var previewGrid = document.createElement("div");
      previewGrid.className = "image-grid";
      var previewCount = Math.min(dyn.images.length, PREVIEW_COUNT);
      for (var i = 0; i < previewCount; i++) {
        var tile = createTile(dyn.images[i], dyn, i);
        previewGrid.appendChild(tile);
      }
      imageContainer.appendChild(previewGrid);

      // Remaining images (hidden behind view-all)
      if (dyn.images.length > PREVIEW_COUNT) {
        var restGrid = document.createElement("div");
        restGrid.className = "image-grid";
        restGrid.hidden = true;
        for (var j = PREVIEW_COUNT; j < dyn.images.length; j++) {
          restGrid.appendChild(createTile(dyn.images[j], dyn, j));
        }
        imageContainer.appendChild(restGrid);
      }
    }

    // ---- View all button ----
    var viewAllBtn = null;
    if (dyn.imageCount > PREVIEW_COUNT) {
      viewAllBtn = document.createElement("button");
      viewAllBtn.className = "view-all-btn";
      viewAllBtn.innerHTML = '<span class="arrow">&#9660;</span> 查看全部 ' + dyn.imageCount + " 张";
      viewAllBtn.setAttribute("aria-expanded", "false");
      viewAllBtn.setAttribute("aria-controls", imagesId);
    }

    // ---- Assemble ----
    card.appendChild(header);
    card.appendChild(imageContainer);
    if (viewAllBtn) card.appendChild(viewAllBtn);

    // ---- View all toggle ----
    if (viewAllBtn) {
      viewAllBtn.addEventListener("click", function () {
        var restGrid = imageContainer.querySelectorAll(".image-grid")[1];
        if (!restGrid) return;
        var expanded = !restGrid.hidden;
        if (expanded) {
          restGrid.hidden = true;
          card.classList.remove("expanded");
          viewAllBtn.innerHTML = '<span class="arrow">&#9660;</span> 查看全部 ' + dyn.imageCount + " 张";
          viewAllBtn.setAttribute("aria-expanded", "false");
        } else {
          restGrid.hidden = false;
          card.classList.add("expanded");
          viewAllBtn.innerHTML = '<span class="arrow">&#9660;</span> 收起';
          viewAllBtn.setAttribute("aria-expanded", "true");
        }
      });
    }

    return card;
  }

  // ------------------------------------------------------------------
  // Tile creation (fixed load-order bug)
  // ------------------------------------------------------------------

  // Tile height — 3x taller than typical thumbnail
  var TILE_HEIGHT = 640;

  function getTileHeight() {
    // smaller on mobile
    if (window.innerWidth < 480) return 400;
    if (window.innerWidth < 768) return 500;
    return TILE_HEIGHT;
  }

  function createTile(meta, dyn, idx) {
    var tileH = getTileHeight();
    var origW = meta.originalWidth || (meta.storedWidth * (meta.displayWidthScale || 1));
    var origH = meta.originalHeight || meta.storedHeight;
    var aspect = origH > 0 ? origW / origH : 1;
    var tileW = Math.round(tileH * aspect);

    var tile = document.createElement("div");
    tile.className = "image-tile";
    tile.style.width = tileW + "px";
    tile.style.height = tileH + "px";

    var img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = (dyn.text || "存档图片") + " #" + (meta.index + 1);
    img.style.width = tileW + "px";
    img.style.height = tileH + "px";

    // Bind events BEFORE setting src
    img.addEventListener("load", function () {
      img.classList.add("loaded");
    });

    img.addEventListener("error", function () {
      img.remove();
      var ph = document.createElement("div");
      ph.className = "tile-placeholder";
      ph.textContent = "\u56FE\u7247\u52A0\u8F7D\u5931\u8D25";
      tile.appendChild(ph);
    });

    img.src = R2_BASE + "/" + (meta.thumbnailKey || meta.r2Key || "");

    if (img.complete) {
      img.classList.add("loaded");
    }

    tile.appendChild(img);
    tile.addEventListener("click", function () {
      console.log("[app] tile clicked, dyn.id:", dyn.id, "idx:", idx, "dyn.images count:", (dyn.images || []).length);
      console.log("[app] meta.index (api):", meta.index, "meta.r2Key:", meta.r2Key);
      console.log("[app] R2_BASE:", R2_BASE);
      window.openViewer(dyn.images || [], idx);
    });
    return tile;
  }

  // ------------------------------------------------------------------
  // Search
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
    var input = document.getElementById("searchInput");
    if (input && input.value.trim()) {
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  console.log("[app] init, ARCHIVE_CONFIG:", window.ARCHIVE_CONFIG);
  console.log("[app] R2_BASE:", R2_BASE);
  console.log("[app] PREVIEW_COUNT:", PREVIEW_COUNT, "TILE_HEIGHT:", TILE_HEIGHT);

  initFilterBar();
  setCategory("上新");

  fetchData();

})();
