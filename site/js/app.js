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
  var pendingTarget = null;     // /to/ 未决目标（供 loadRoute 异步解析）
  var resolveToken = 0;         // 递增以取消过期的跨账号解析
  var routeIndexCache = {};     // "account:page" → 已归一化 index 数据（会话内缓存）
  var searchInput = document.getElementById("searchInput");
  var allSiteToggle = document.getElementById("allSiteToggle");
  var fromEl = null;            // date-range controls, built by initFilterBar
  var toEl = null;
  var sortEl = null;
  var urlSyncTimer = null;
  // R8 infinite-scroll batch state
  var RENDER_BATCH = 30;
  var batchState = null;
  var batchObserver = null;
  var batchSentinel = null;

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

  // /to/{account}/{slug}/  (new)  and  /to/{slug}/  (legacy, README.md:20)
  function parseToTarget(pathname) {
    var seg = pathname.match(/^\/to\/([^/]+)\/([^/]+)\/?$/);
    if (seg) {
      var acc = seg[1];
      if (CFG.ACCOUNTS[acc]) {                       // 第一段必须是已知账号，否则按旧格式继续
        try { return { account: acc, slug: decodeURIComponent(seg[2]) }; }
        catch (err) { return null; }
      }
    }
    var legacy = pathname.match(/^\/to\/([^/]+)\/?$/);
    if (legacy) {
      try { return { slug: decodeURIComponent(legacy[1]) }; }
      catch (err) { return null; }
    }
    return null;
  }

  function routeTargetFromLocation() {
    var to = parseToTarget(window.location.pathname);
    if (to) return { slug: to.slug, account: to.account || null };
    var hashMatch = window.location.hash.match(/^#id-(.+)$/);
    if (hashMatch) return { id: hashMatch[1] };      // 与现状一致：id 不回显 decode（纯数字）
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
    resolveToken++;                                  // 取消在途的跨账号解析
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
    if (!route) {
      var to = parseToTarget(window.location.pathname);
      if (to) {
        var start = to.account
          ? { account: to.account, page: "main" }
          : (currentRoute || { account: "ak", page: "main" });
        currentRoute = start;
        rememberRoute(start);
        updateShell();
        loadRoute(start);
        return;
      }
      redirectToLast();
      return;
    }
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
    syncUrlState(false);
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

    // Date range + sort controls (R1)
    var extras = document.createElement("div");
    extras.className = "filter-extras";
    fromEl = document.createElement("input");
    fromEl.type = "date";
    fromEl.id = "filterFrom";
    fromEl.setAttribute("aria-label", "起始日期");
    fromEl.addEventListener("change", function () {
      render(activeSearchIds);
      syncUrlState(false);
    });
    toEl = document.createElement("input");
    toEl.type = "date";
    toEl.id = "filterTo";
    toEl.setAttribute("aria-label", "结束日期");
    toEl.addEventListener("change", function () {
      render(activeSearchIds);
      syncUrlState(false);
    });
    sortEl = document.createElement("select");
    sortEl.id = "filterSort";
    sortEl.setAttribute("aria-label", "排序");
    [["date-desc", "最新在前"], ["date-asc", "最早在前"], ["images-desc", "图片最多"]].forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o[0];
      opt.textContent = o[1];
      sortEl.appendChild(opt);
    });
    sortEl.value = "date-desc";
    sortEl.addEventListener("change", function () {
      render(activeSearchIds);
      syncUrlState(false);
    });
    extras.appendChild(fromEl);
    extras.appendChild(toEl);
    extras.appendChild(sortEl);
    bar.appendChild(extras);
  }

  function loadRoute(route) {
    showLoading();
    currentCategory = "";
    activeSearchIds = null;
    var urlState = parseUrlState();
    if (searchInput) {
      searchInput.value = urlState.q || "";
      var clearBtn = document.getElementById("searchClear");
      if (clearBtn) clearBtn.hidden = !urlState.q;
      var statusEl = document.getElementById("searchStatus");
      if (statusEl) statusEl.textContent = "";
    }
    var noResultsEl = document.getElementById("noResults");
    if (noResultsEl) {
      var np = noResultsEl.querySelector("p");
      if (np) np.textContent = "没有匹配的动态";
      var cb = document.getElementById("clearSearchBtn");
      if (cb) cb.hidden = false;
    }

    timeline.classList.add("fade-out");

    var searchUrl = R2_BASE + searchFile(route);

    Promise.all([
      fetchIndexFor(route),
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
      applyUrlState();
      checkRoute();
      render();
      if (routeTarget) {
        navigateToDynamic(routeTarget);
      }
      if (pendingTarget) {
        resolvePendingTarget();
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

  // --- Slug helpers (client mirror of collect.server_slug) ---

  function normalizeSlug(text) {
    var line = "";
    var lines = String(text || "").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var cleaned = lines[i].replace(/#[^#]+#/g, "").trim();
      if (cleaned) { line = cleaned; break; }
    }
    line = line.replace(/\s+/g, " ").trim();
    line = line.replace(/[^\w\u4e00-\u9fa5-]/g, "-"); // JS \w 仅 ASCII，等价 collect 的显式字符类
    line = line.replace(/-+/g, "-").replace(/^-|-$/g, "");
    return line;
  }

  function buildSlug(dyn) {
    if (dyn && dyn.slug) return dyn.slug;                 // 服务端权威值，先取（旧索引缺字段时走推导）
    var text = (dyn && (dyn.text || dyn.fullText)) || "";
    var m = text.match(/[｜|]([^〓▼]+)〓/);
    if (m && m[1].trim()) return m[1].trim();            // ak 〓…〓
    var m2 = text.match(/▼(.+?)▼/);
    if (m2 && m2[1].trim()) return m2[1].trim();         // ef ▼…▼
    var norm = normalizeSlug(text);
    if (norm) return norm;                                // 归一化回退
    return String((dyn && dyn.id) || "");
  }

  function shortCode(id) {
    // /to/{code}/ 短链：bilibili 雪花 ID 的 base36 末 8 位（≈ ID mod 36^8，
    // 双射子集空间 2.8e12；63 条实测零碰撞，生日碰撞概率 ~7e-10）。
    try { return BigInt(id).toString(36).slice(-8); }
    catch (err) { return String(id || ""); }
  }

  function buildDynamicLink(dyn) {
    return window.location.origin + "/to/" + shortCode((dyn && dyn.id) || "") + "/";
  }

  function copyTextFallback(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function copyText(text, onResult) {
    function finish(ok) {
      if (!ok) {
        try { window.prompt("复制失败，请手动复制链接", text); } catch (e) { /* ignore */ }
      }
      if (onResult) onResult(!!ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { finish(true); },
        function () { finish(copyTextFallback(text)); }
      );
      return;
    }
    finish(copyTextFallback(text));
  }

  // 暴露给 viewer.js 信息条复用
  window.buildDynamicLink = buildDynamicLink;
  window.copyText = copyText;

  // ------------------------------------------------------------------
  // Routing — open a specific dynamic via URL
  // ------------------------------------------------------------------
  function matchTarget(dyn, target) {
    if (target.id) return dyn.id === target.id;
    if (target.slug) {
      if (dyn.slug && dyn.slug === target.slug) return true;  // 服务端权威值直接比对
      if (buildSlug(dyn) === target.slug) return true;        // 旧索引回退：客户端推导
      if (shortCode(dyn.id) === target.slug) return true;     // /to/{短码}/
      return dyn.id === target.slug;                          // 兜底：完整动态 ID
    }
    return false;
  }

  function resolveInDynamics(dynamics, target, account) {
    for (var i = 0; i < dynamics.length; i++) {
      var d = dynamics[i];
      if (matchTarget(d, target)) { d._account = account; return d; }
    }
    return null;
  }

  // 解析候选队列：显式账号优先 → 同账号 figures → 另一账号兜底
  function toResolutionQueue(target) {
    var others = Object.keys(CFG.ACCOUNTS).filter(function (a) { return a !== currentRoute.account; });
    var queue = [];
    var pushed = {};
    function push(a, p) {
      var k = a + ":" + p;
      if (pushed[k]) return;
      if (a === currentRoute.account && p === currentRoute.page) return; // 已同步查过
      pushed[k] = true;
      queue.push({ account: a, page: p });
    }
    if (target.account && target.account !== currentRoute.account) {
      push(target.account, "main");      // 显式账号优先：与 URL 意图一致
      push(target.account, "figures");
    }
    push(currentRoute.account, "figures");                 // 同账号 figures
    others.forEach(function (a) { push(a, "main"); push(a, "figures"); }); // 另一账号兜底
    return queue;
  }

  function checkRoute() {
    routeTarget = null;          // 修复 routeTarget 陈旧残留
    pendingTarget = null;
    if (!appData || !appData.dynamics) return;

    var target = routeTargetFromLocation();
    if (!target) return;

    // 当前路由 appData 同步解析
    routeTarget = resolveInDynamics(appData.dynamics, target, currentRoute.account);
    if (routeTarget) {
      currentCategory = "";
      document.querySelectorAll(".filter-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.cat === "");
      });
      return;
    }

    // #id- 保持现状：仅当前路由解析；/to/ 才走跨账号/跨页异步队列
    if (target.slug) {
      pendingTarget = target;
    }
  }

  function fetchIndexFor(route) {
    var key = route.account + ":" + route.page;
    if (routeIndexCache[key]) return Promise.resolve(routeIndexCache[key]);
    var url = R2_BASE + indexFile(route);
    return fetch(url)
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        if (!data || !data.dynamics) throw new Error("Invalid index format");
        data.dynamics = data.dynamics.map(normalizeDynamic);
        routeIndexCache[key] = data;
        return data;
      });
  }

  function resolvePendingTarget() {
    var target = pendingTarget;
    if (!target) return;
    pendingTarget = null;
    var token = ++resolveToken;
    var queue = toResolutionQueue(target);
    var idx = 0;
    var aborted = false;
    function step() {
      if (aborted || token !== resolveToken) return;   // 期间发生了新导航 → 放弃
      if (idx >= queue.length) { showRouteMiss(); return; }
      var cand = queue[idx++];
      fetchIndexFor(cand).then(function (data) {
        if (aborted || token !== resolveToken) return;
        var dyn = resolveInDynamics(data.dynamics, target, cand.account);
        if (dyn) { openResolvedTarget(dyn, cand); }
        else { step(); }
      }).catch(function () { step(); });
    }
    step();
  }

  function openResolvedTarget(dyn, cand) {
    // 命中的是另一路由：切过去（/to/ URL 保持），loadRoute 的 checkRoute 会同步二次命中
    if (!currentRoute || currentRoute.account !== cand.account || currentRoute.page !== cand.page) {
      currentRoute = { account: cand.account, page: cand.page };
      rememberRoute(currentRoute);
      updateShell();
      loadRoute(currentRoute);
      return;
    }
    routeTarget = dyn;
    navigateToDynamic(dyn);
  }

  function showRouteMiss() {
    var el = document.getElementById("noResults");
    if (!el) return;
    el.hidden = false;
    var p = el.querySelector("p");
    if (p) p.textContent = "未找到该动态（链接可能已失效）";
    var btn = document.getElementById("clearSearchBtn");
    if (btn) btn.hidden = true;
  }

  function navigateToDynamic(dyn) {
    // 保留 /to/… 可分享 URL：不再改写回干净路由。不做 pushState → 不会触发 popstate 双加载。
    if (!dyn._account) dyn._account = currentRoute.account;

    requestAnimationFrame(function () {
      var card = document.querySelector('.dynamic-card[data-id="' + dyn.id + '"]');
      if (card) {
        var imgs = card.querySelectorAll('img[loading="lazy"]');
        imgs.forEach(function (img) { img.loading = "eager"; });
        card.scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(function () {
          if (dyn.images && dyn.images.length > 0) {
            window.openViewer(dyn.images, 0, dyn);
          }
        }, 600);
      } else if (dyn._account && dyn._account !== currentRoute.account) {
        // 理论上跨账号命中后已切路由、卡片应在；此处仅防御性记录
        console.warn("Dynamic not rendered on current route:", dyn.id);
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

    var from = fromEl ? fromEl.value : "";
    var to = toEl ? toEl.value : "";
    if (from || to) {
      visibleDynamics = visibleDynamics.filter(function (d) {
        if (from && (!d.date || d.date < from)) return false;
        if (to && (!d.date || d.date > to)) return false;
        return true;
      });
    }

    var sort = sortEl ? sortEl.value : "date-desc";
    visibleDynamics = visibleDynamics.slice();
    if (sort === "date-asc") {
      visibleDynamics.sort(function (a, b) {
        var byDate = (a.date || "").localeCompare(b.date || "");
        if (byDate !== 0) return byDate;
        return (b.timestamp || 0) - (a.timestamp || 0);
      });
    } else if (sort === "images-desc") {
      visibleDynamics.sort(function (a, b) {
        var byImgs = (b.imageCount || 0) - (a.imageCount || 0);
        if (byImgs !== 0) return byImgs;
        return (b.timestamp || 0) - (a.timestamp || 0);
      });
    } else {
      visibleDynamics.sort(function (a, b) { return (b.timestamp || 0) - (a.timestamp || 0); });
    }

    var isFiltering = !!(searchFilterIds || currentCategory || from || to);
    noResults.hidden = !isFiltering || visibleDynamics.length > 0;

    // R8: batch render (~30 per chunk) + IntersectionObserver infinite scroll
    timeline.innerHTML = "";
    removeBatchObserver();
    batchState = { list: visibleDynamics, rendered: 0 };
    renderNextBatch();
    updateFooter();
  }

  function renderNextBatch() {
    if (!batchState) return;
    var list = batchState.list;
    var count = Math.min(RENDER_BATCH, list.length - batchState.rendered);
    for (var i = 0; i < count; i++) {
      timeline.appendChild(createCard(list[batchState.rendered + i]));
    }
    batchState.rendered += count;
    updateFooter();
    if (batchState.rendered < list.length) {
      observeBatchSentinel();
    } else {
      removeBatchObserver();
    }
  }

  function observeBatchSentinel() {
    removeBatchObserver();
    batchSentinel = document.createElement("div");
    batchSentinel.className = "infinite-sentinel";
    batchSentinel.setAttribute("aria-hidden", "true");
    timeline.appendChild(batchSentinel);
    if (!batchObserver && "IntersectionObserver" in window) {
      batchObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) renderNextBatch();
        });
      }, { rootMargin: "400px 0px" });
    }
    if (batchObserver) batchObserver.observe(batchSentinel);
  }

  function removeBatchObserver() {
    if (batchObserver) { batchObserver.disconnect(); }
    batchObserver = null;
    if (batchSentinel && batchSentinel.parentNode) batchSentinel.parentNode.removeChild(batchSentinel);
    batchSentinel = null;
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

    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "copy-link";
    copyBtn.textContent = "复制链接";
    copyBtn.setAttribute("aria-label", "复制本条动态的站内链接");
    copyBtn.addEventListener("click", function () {
      var url = buildDynamicLink(dyn);
      if (!url) return;
      copyText(url, function () {
        copyBtn.textContent = "已复制 ✓";
        clearTimeout(copyBtn._t);
        copyBtn._t = setTimeout(function () { copyBtn.textContent = "复制链接"; }, 1500);
      });
    });
    metaRow.appendChild(copyBtn);

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
    tile.tabIndex = 0;
    tile.setAttribute("role", "button");
    tile.setAttribute("aria-label", (dyn.text || "存档图片") + " 第" + (idx + 1) + "张，打开查看");
    function openFromTile() {
      window.openViewer(dyn.images || [], idx, dyn);
    }
    tile.addEventListener("click", openFromTile);
    tile.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openFromTile();
      }
    });
    return tile;
  }

  // ------------------------------------------------------------------
  // Search
  // ------------------------------------------------------------------

  // ------------------------------------------------------------------
  // Search
  // ------------------------------------------------------------------

  function onSearchResult(filterIds) {
    if (!appData) return;
    activeSearchIds = filterIds;
    if (filterIds === null) {
      render(null);
      document.getElementById("noResults").hidden = true;
      document.getElementById("searchStatus").textContent = "";
    } else {
      render(filterIds);
    }
  }

  function currentTaggedSearch() {
    var data = searchData || [];
    return data.map(function (it) {
      var c = Object.assign({}, it);
      c._account = accountLabel(currentRoute.account);
      return c;
    });
  }

  function fetchRemoteSearch(account) {
    var url = R2_BASE + searchFile({ account: account, page: currentRoute.page });
    return fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (data) {
        if (!data) return [];
        return data.map(function (it) {
          var c = Object.assign({}, it);
          c._account = accountLabel(account);
          return c;
        });
      });
  }

  function setupSearch() {
    if (allSiteToggle && allSiteToggle.checked) {
      var others = Object.keys(CFG.ACCOUNTS).filter(function (a) { return a !== currentRoute.account; });
      Promise.all(others.map(fetchRemoteSearch)).then(function (parts) {
        var merged = currentTaggedSearch();
        parts.forEach(function (p) { merged = merged.concat(p); });
        window.initSearch(merged, onSearchResult);
        if (searchInput && searchInput.value.trim()) {
          searchInput.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
    } else {
      window.initSearch(searchData || [], onSearchResult);
      if (searchInput && searchInput.value.trim()) {
        searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  }

  // ------------------------------------------------------------------
  // URL state (R1): ?q=&cat=&from=&to=&sort=
  // ------------------------------------------------------------------

  function parseUrlState() {
    var out = { q: "", cat: "", from: "", to: "", sort: "" };
    try {
      var p = new URLSearchParams(window.location.search);
      if (p.has("q") && p.get("q")) out.q = p.get("q");
      if (p.has("cat") && p.get("cat")) out.cat = p.get("cat");
      var fromV = p.get("from") || "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(fromV)) out.from = fromV;
      var toV = p.get("to") || "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(toV)) out.to = toV;
      var s = p.get("sort") || "";
      if (s === "date-asc" || s === "date-desc" || s === "images-desc") out.sort = s;
    } catch (e) { /* ignore malformed query */ }
    return out;
  }

  function syncUrlState(replace) {
    if (!currentRoute) return;
    var params = new URLSearchParams();
    var q = searchInput ? searchInput.value.trim() : "";
    if (q) params.set("q", q);
    if (currentCategory) params.set("cat", currentCategory);
    if (fromEl && fromEl.value) params.set("from", fromEl.value);
    if (toEl && toEl.value) params.set("to", toEl.value);
    if (sortEl && sortEl.value && sortEl.value !== "date-desc") params.set("sort", sortEl.value);
    var qs = params.toString();
    var url = routePath(currentRoute) + (qs ? "?" + qs : "");
    var state = { route: routePath(currentRoute) };
    try {
      if (replace) history.replaceState(state, "", url);
      else history.pushState(state, "", url);
    } catch (e) { /* ignore quota errors */ }
  }

  function applyUrlState() {
    var st = parseUrlState();
    if (searchInput && st.q) {
      searchInput.value = st.q;
      var clearBtn = document.getElementById("searchClear");
      if (clearBtn) clearBtn.hidden = false;
    }
    if (st.cat && document.querySelector('.filter-btn[data-cat="' + st.cat + '"]')) {
      currentCategory = st.cat;
      document.querySelectorAll(".filter-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.cat === st.cat);
      });
    }
    if (fromEl) fromEl.value = st.from;
    if (toEl) toEl.value = st.to;
    if (sortEl) sortEl.value = st.sort || "date-desc";
    if (searchInput && searchInput.value.trim()) {
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      clearTimeout(urlSyncTimer);
      urlSyncTimer = setTimeout(function () { syncUrlState(true); }, 300);
    });
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        clearTimeout(urlSyncTimer);
        syncUrlState(false);
      }
    });
  }

  if (allSiteToggle) {
    allSiteToggle.addEventListener("change", function () {
      setupSearch();
      syncUrlState(false);
    });
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
  var toTarget = !initial && parseToTarget(window.location.pathname);
  if (initial) {
    currentRoute = initial;
    rememberRoute(initial);
    updateShell();
    loadRoute(initial);
  } else if (toTarget) {
    // /to/{account}/{slug}/ 或 /to/{slug}/：不 redirect，保留可分享 URL
    var start;
    if (toTarget.account) {
      start = { account: toTarget.account, page: "main" };
    } else {
      var last = null;
      try { last = localStorage.getItem(LS_LAST); } catch (e) { /* ignore */ }
      start = last ? (parseRoute(last) || { account: "ak", page: "main" }) : { account: "ak", page: "main" };
    }
    currentRoute = start;
    rememberRoute(start);
    updateShell();
    loadRoute(start);
  } else {
    // "/" or unknown → redirect to last route (default /ak/)
    redirectToLast();
  }

})();
