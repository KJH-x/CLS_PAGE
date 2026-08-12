// app.js — Main application: SPA routing, fetch data, render gallery-first timeline
//
// Routes (account × page), fully isolated:
//   /ak/         朝陇山 main
//   /ak-figures/ 朝陇山 手办
//   /ef/         山团团 main
//   /ef-figures/ 山团团 手办
//
// Navigation is pushState-based (no reload). Content cross-fades; only images load.
// Remembers the last opened route (archive.lastRoute) and per-route scroll offsets
// (archive.scroll.<route>). "/" redirects to the last route (default /ak/).

(function () {
  "use strict";

  var CFG = window.ARCHIVE_CONFIG;
  var R2_BASE = CFG.R2_PUBLIC_URL;
  var PREVIEW_COUNT = 6;
  var FADE_MS = 200;

  var LS_LAST = "archive.lastRoute";
  var LS_SCROLL = "archive.scroll.";

  // ---- DOM refs ----
  var timeline = document.getElementById("timeline");
  var loadingState = document.getElementById("loadingState");
  var errorState = document.getElementById("errorState");
  var errorMsg = document.getElementById("errorMessage");
  var errorRetry = document.getElementById("errorRetry");
  var emptyState = document.getElementById("emptyState");
  var noResults = document.getElementById("noResults");
  var footerTime = document.getElementById("footerTime");
  var footerCount = document.getElementById("footerCount");
  var headerCount = document.getElementById("headerCount");
  var headerTime = document.getElementById("headerTime");
  var siteTitle = document.getElementById("siteTitle");
  var accountNav = document.getElementById("accountNav");
  var pageNav = document.getElementById("pageNav");

  // ---- State ----
  var appData = null;
  var searchData = null;
  var currentCategory = "";
  var activeSearchIds = null;
  var currentRoute = null;      // {account, page}
  var routeTarget = null;
  var searchInput = document.getElementById("searchInput");

  var ROUTE_PATHS = {
    "ak": { main: "/ak/", figures: "/ak-figures/" },
    "ef": { main: "/ef/", figures: "/ef-figures/" },
  };

  // ------------------------------------------------------------------
  // Routing
  // ------------------------------------------------------------------

  function parseRoute(pathname) {
    if (pathname.indexOf("/ak-figures") === 0) return { account: "ak", page: "figures" };
    if (pathname.indexOf("/ak") === 0) return { account: "ak", page: "main" };
    if (pathname.indexOf("/ef-figures") === 0) return { account: "ef", page: "figures" };
    if (pathname.indexOf("/ef") === 0) return { account: "ef", page: "main" };
    return null;
  }

  function routePath(route) {
    return ROUTE_PATHS[route.account][route.page];
  }

  function accountLabel(account) {
    return CFG.ACCOUNTS[account] ? CFG.ACCOUNTS[account].label : account;
  }

  function indexFile(route) {
    var acc = CFG.ACCOUNTS[route.account];
    return route.page === "figures" ? acc.figures : acc.index;
  }

  function searchFile(route) {
    var acc = CFG.ACCOUNTS[route.account];
    return route.page === "figures" ? acc.figuresSearch : acc.search;
  }

  // ------------------------------------------------------------------
  // Navigation
  // ------------------------------------------------------------------

  function rememberRoute(route) {
    try { localStorage.setItem(LS_LAST, routePath(route)); } catch (e) { /* ignore */ }
  }

  function rememberScroll() {
    if (!currentRoute) return;
    try { localStorage.setItem(LS_SCROLL + routePath(currentRoute), String(window.scrollY || 0)); } catch (e) { /* ignore */ }
  }

  function restoreScroll() {
    if (!currentRoute) return;
    var saved = null;
    try { saved = parseInt(localStorage.getItem(LS_SCROLL + routePath(currentRoute)) || "0", 10); } catch (e) { /* ignore */ }
    if (saved && saved > 0) {
      // wait for images to start laying out so the position is meaningful
      setTimeout(function () { window.scrollTo(0, saved); }, 50);
    } else {
      window.scrollTo(0, 0);
    }
  }

  function buildSeg(container, items, activeIndex, onSelect) {
    container.innerHTML = '<span class="seg-thumb"></span>';
    items.forEach(function (item, i) {
      var b = document.createElement("button");
      b.className = "nav-btn" + (i === activeIndex ? " active" : "");
      b.textContent = item.label;
      b.addEventListener("click", function () { onSelect(i, item); });
      container.appendChild(b);
    });
    container.setAttribute("data-on", String(activeIndex));
  }

  function renderNav() {
    var accList = Object.keys(CFG.ACCOUNTS).map(function (acc) {
      return { key: acc, label: CFG.ACCOUNTS[acc].label };
    });
    var accIdx = accList.findIndex(function (a) { return a.key === currentRoute.account; });
    buildSeg(accountNav, accList, accIdx, function (i) {
      navigateTo({ account: accList[i].key, page: currentRoute.page });
    });
    var pages = [{ key: "main", label: "全部" }, { key: "figures", label: "手办" }];
    var pageIdx = pages.findIndex(function (p) { return p.key === currentRoute.page; });
    buildSeg(pageNav, pages, pageIdx, function (i) {
      navigateTo({ account: currentRoute.account, page: pages[i].key });
    });
  }

  function updateShell() {
    var acc = CFG.ACCOUNTS[currentRoute.account];
    var title = acc.label + (currentRoute.page === "figures" ? "手办预售归档" : "图片归档");
    siteTitle.textContent = title;
    document.title = title + " - 明日方舟周边图片归档";
    document.body.setAttribute("data-account", currentRoute.account);
    renderNav();
  }

  function navigateTo(route, opts) {
    opts = opts || {};
    if (currentRoute &&
        currentRoute.account === route.account &&
        currentRoute.page === route.page &&
        !opts.force) {
      return;
    }
    rememberScroll();
    rememberRoute(route);
    currentRoute = route;
    var path = routePath(route);
    if (window.location.pathname !== path) {
      history.pushState({ route: path }, "", path);
    }
    updateShell();
    loadRoute(route);
  }

  window.addEventListener("popstate", function () {
    var route = parseRoute(window.location.pathname);
    if (!route) { redirectToLast(); return; }
    currentRoute = route;
    rememberRoute(route);
    updateShell();
    loadRoute(route);
  });

  function redirectToLast() {
    var last = null;
    try { last = localStorage.getItem(LS_LAST); } catch (e) { /* ignore */ }
    var route = last ? parseRoute(last) : null;
    if (!route) route = { account: "ak", page: "main" };
    currentRoute = route;
    var path = routePath(route);
    try { localStorage.setItem(LS_LAST, path); } catch (e) { /* ignore */ }
    history.replaceState({ route: path }, "", path);
    updateShell();
    loadRoute(route);
  }

  // ------------------------------------------------------------------
  // Data load with fade
  // ------------------------------------------------------------------

  function setCategory(cat) {
    currentCategory = cat;
    document.querySelectorAll(".filter-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.cat === cat);
    });
    render(activeSearchIds);
  }

  function initFilterBar() {
    if (!appData || !appData.dynamics) return;
    var bar = document.getElementById("filterBar");
    bar.innerHTML = "";
    if (currentRoute.page === "figures") {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    var cats = {};
    appData.dynamics.forEach(function (d) {
      var c = d.category || "";
      if (c) cats[c] = (cats[c] || 0) + 1;
    });
    var btn = document.createElement("button");
    btn.className = "filter-btn active";
    btn.dataset.cat = "";
    btn.textContent = "全部";
    btn.addEventListener("click", function () { setCategory(""); });
    bar.appendChild(btn);
    Object.keys(cats).sort().forEach(function (cat) {
      var b = document.createElement("button");
      b.className = "filter-btn";
      b.dataset.cat = cat;
      b.textContent = cat;
      b.addEventListener("click", function () { setCategory(cat); });
      bar.appendChild(b);
    });
  }

  function loadRoute(route) {
    showLoading();
    currentCategory = "";
    activeSearchIds = null;
    if (searchInput) {
      searchInput.value = "";
      var clearBtn = document.getElementById("searchClear");
      if (clearBtn) clearBtn.hidden = true;
      var statusEl = document.getElementById("searchStatus");
      if (statusEl) statusEl.textContent = "";
    }

    timeline.classList.add("fade-out");

    var indexUrl = R2_BASE + indexFile(route);
    var searchUrl = R2_BASE + searchFile(route);

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
      initFilterBar();
      checkRoute();
      render();
      if (routeTarget) {
        navigateToDynamic(routeTarget);
      }
      setTimeout(fadeIn, FADE_MS);
      restoreScroll();
      if (routeTarget) {
        setTimeout(function () { navigateToDynamic(routeTarget); }, FADE_MS + 60);
      }
      setTimeout(function () {
        updateFsThumb();
        showFsBar();
      }, FADE_MS + 80);
      layoutFsBar();
    })
    .catch(function (err) {
      console.error("Fetch error:", err);
      showError("无法加载归档数据：" + err.message);
      timeline.classList.remove("fade-out");
    });
  }

  function fadeIn() {
    timeline.classList.remove("fade-out");
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
  function checkRoute() {
    if (!appData || !appData.dynamics) return;

    var targetId = null;
    var targetActivity = null;

    var pathMatch = window.location.pathname.match(/\/to\/(.+?)\/?$/);
    if (pathMatch) {
      try {
        targetActivity = decodeURIComponent(pathMatch[1]);
      } catch (err) {
        console.warn("Ignoring malformed archive route", err);
      }
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
      currentCategory = "";
      document.querySelectorAll(".filter-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.cat === "");
      });
    }
  }

  function navigateToDynamic(dyn) {
    var cleanPath = routePath(currentRoute);
    var cleanUrl = window.location.origin + cleanPath;
    if (window.location.pathname !== cleanPath || window.location.hash) {
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

  errorRetry.addEventListener("click", function () { loadRoute(currentRoute); });

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
    }

    if (currentCategory) {
      visibleDynamics = visibleDynamics.filter(function (d) {
        return (d.category || "") === currentCategory;
      });
    }

    noResults.hidden = !(searchFilterIds || currentCategory) || visibleDynamics.length > 0;

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
      if (headerCount) headerCount.textContent = countStr;
      if (headerTime) headerTime.textContent = timeStr;
    }
  }

  // ------------------------------------------------------------------
  // Card creation
  // ------------------------------------------------------------------

  function createCard(dyn) {
    var card = document.createElement("article");
    card.className = "dynamic-card";
    card.dataset.id = dyn.id;

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

    var metaRow = document.createElement("div");
    metaRow.className = "card-meta";

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

    var viewAllBtn = null;
    if (dyn.imageCount > PREVIEW_COUNT) {
      viewAllBtn = document.createElement("button");
      viewAllBtn.className = "view-all-btn";
      viewAllBtn.innerHTML = '<span class="arrow">&#9660;</span> 查看全部 ' + dyn.imageCount + " 张";
      viewAllBtn.setAttribute("aria-expanded", "false");
      viewAllBtn.setAttribute("aria-controls", imagesId);
    }

    card.appendChild(header);
    card.appendChild(imageContainer);
    if (viewAllBtn) card.appendChild(viewAllBtn);

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
  // Tile creation
  // ------------------------------------------------------------------

  var TILE_HEIGHT = 640;

  function getTileHeight() {
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

    // Grid preview: use the 1/8 small thumb; full original only loads in the viewer.
    img.src = R2_BASE + "/" + (meta.smallThumbKey || meta.thumbnailKey || meta.r2Key || "");

    if (img.complete) {
      img.classList.add("loaded");
    }

    tile.appendChild(img);
    tile.addEventListener("click", function () {
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
      activeSearchIds = filterIds;
      if (filterIds === null) {
        render(null);
        document.getElementById("noResults").hidden = true;
        document.getElementById("searchStatus").textContent = "";
      } else {
        render(filterIds);
      }
    });
    if (searchInput && searchInput.value.trim()) {
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // ------------------------------------------------------------------
  // Floating scrollbar (thumb only, fade in/out on scroll & hover)
  // ------------------------------------------------------------------

  var fsBar = document.getElementById("floatingScrollbar");
  var fsThumb = document.getElementById("fsThumb");
  var fsHideTimer = null;
  var FS_HIDE_DELAY = 600;

  function layoutFsBar() {
    if (!fsBar) return;
    var header = document.querySelector(".site-header");
    var top = header ? header.offsetHeight : 0;
    fsBar.style.top = top + "px";
  }

  function updateFsThumb() {
    if (!fsBar || !fsThumb) return;
    var doc = document.documentElement;
    var scrollH = Math.max(doc.scrollHeight, document.body.scrollHeight);
    var viewH = window.innerHeight;
    var scrollTop = window.pageYOffset || doc.scrollTop || 0;
    var trackH = fsBar.clientHeight;
    var maxScroll = scrollH - viewH;
    if (maxScroll <= 0) {
      fsThumb.style.height = "0px";
      fsThumb.style.top = "0px";
      return;
    }
    var th = Math.max(24, trackH * viewH / scrollH);
    fsThumb.style.height = th + "px";
    var maxTop = trackH - th;
    fsThumb.style.top = (scrollTop / maxScroll) * maxTop + "px";
  }

  function showFsBar() {
    if (!fsBar) return;
    fsBar.classList.add("visible");
    clearTimeout(fsHideTimer);
    fsHideTimer = setTimeout(function () { fsBar.classList.remove("visible"); }, FS_HIDE_DELAY);
  }

  function showFsBarKeep() {
    if (!fsBar) return;
    fsBar.classList.add("visible");
    clearTimeout(fsHideTimer);
  }

  window.addEventListener("scroll", function () {
    updateFsThumb();
    showFsBar();
  }, { passive: true });

  window.addEventListener("resize", function () {
    layoutFsBar();
    updateFsThumb();
  });

  if (fsBar) {
    fsBar.addEventListener("mouseenter", showFsBarKeep);
    fsBar.addEventListener("mouseleave", function () {
      clearTimeout(fsHideTimer);
      fsHideTimer = setTimeout(function () { fsBar.classList.remove("visible"); }, FS_HIDE_DELAY);
    });
    fsThumb.addEventListener("mousedown", function (e) {
      e.preventDefault();
      var doc = document.documentElement;
      var startY = e.clientY;
      var startTop = window.pageYOffset || doc.scrollTop || 0;
      var maxScroll = Math.max(doc.scrollHeight, document.body.scrollHeight) - window.innerHeight;
      var trackH = fsBar.clientHeight;
      var th = fsThumb.clientHeight;
      var maxTop = trackH - th;

      function onMove(ev) {
        var ratio = (ev.clientY - startY) / (maxTop || 1);
        window.scrollTo(0, startTop + ratio * maxScroll);
      }
      function onUp() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.userSelect = "";
      }
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  window.addEventListener("beforeunload", function () {
    rememberScroll();
    rememberRoute(currentRoute);
  });

  layoutFsBar();
  updateFsThumb();

  var initial = parseRoute(window.location.pathname);
  if (initial) {
    currentRoute = initial;
    rememberRoute(initial);
    updateShell();
    loadRoute(initial);
  } else {
    // "/" or unknown → redirect to last route (default /ak/)
    redirectToLast();
  }

})();
