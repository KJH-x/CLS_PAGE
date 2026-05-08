// viewer.js — Full-screen lightbox: zoom, pan, thumbnails

(function () {
  var overlay = document.getElementById("lightbox");
  var content = document.getElementById("lbContent");
  var stage = document.getElementById("lbStage");
  var counter = document.getElementById("lbCounter");
  var closeBtn = document.getElementById("lbClose");
  var prevBtn = document.getElementById("lbPrev");
  var nextBtn = document.getElementById("lbNext");
  var thumbStrip = document.getElementById("lbThumbStrip");
  var tipEl = document.getElementById("lbTip");
  var zoomBadge = document.getElementById("lbZoomBadge");

  // --- State ---
  var images = [];
  var currentIdx = 0;

  // Zoom / pan
  var zoom = 1;
  var tx = 0, ty = 0;
  var baseW = 0, baseH = 0;    // image display size (the rendered px)
  var MIN_ZOOM = 0.5;
  var MAX_ZOOM = 5;
  var ZOOM_STEP = 0.25;

  // Drag
  var dragging = false;
  var dragMoved = false;       // moved > 4px?
  var dragSX = 0, dragSY = 0;
  var dragTX = 0, dragTY = 0;
  var DRAG_THRESHOLD = 4;

  // Touch
  var pinchBaseDist = 0;
  var pinchBaseZoom = 1;
  var swipeStartX = 0;
  var swipeStartY = 0;

  // Tip timer
  var tipTimer = null;

  // --- Helpers ---

  function cfg() {
    return (window.ARCHIVE_CONFIG && window.ARCHIVE_CONFIG.R2_PUBLIC_URL)
      ? window.ARCHIVE_CONFIG.R2_PUBLIC_URL.replace(/\/+$/, "")
      : "";
  }

  function isMobileReadingMode() {
    return window.innerWidth <= 768;
  }

  function currentMinZoom() {
    return isMobileReadingMode() ? 1 : MIN_ZOOM;
  }

  // --- Clamp ---

  function clampPan() {
    var vpW = content.clientWidth;
    var vpH = content.clientHeight;
    var imgW = baseW * zoom;
    var imgH = baseH * zoom;

    if (imgW <= vpW) {
      tx = (vpW - imgW) / 2;
    } else {
      tx = Math.min(0, Math.max(vpW - imgW, tx));
    }

    if (imgH <= vpH) {
      ty = (vpH - imgH) / 2;
    } else {
      ty = Math.min(0, Math.max(vpH - imgH, ty));
    }
  }

  function applyTransform() {
    if (zoom <= 1.001) {
      stage.style.transform = "";
      return;
    }
    stage.style.transform = "translate3d(" + Math.round(tx) + "px," + Math.round(ty) + "px,0) scale(" + zoom + ")";
  }

  function setZoomed(isZoomed) {
    content.classList.toggle("zoomed", isZoomed);
    if (isZoomed) {
      content.scrollTop = 0;
    }
  }

  function showZoomBadge() {
    zoomBadge.hidden = false;
    // Real zoom = display-height-on-screen / original-image-height
    var meta = images[currentIdx];
    var origH = meta ? (meta.originalHeight || meta.storedHeight) : baseH;
    var realZoom = origH > 0 ? (baseH * zoom) / origH : zoom;
    zoomBadge.textContent = Math.round(realZoom * 100) + "%";
    clearTimeout(zoomBadge._t);
    zoomBadge._t = setTimeout(function () { zoomBadge.hidden = true; }, 1200);
  }

  // --- Zoom around point ---

  function zoomAt(cx, cy, delta) {
    var oldZoom = zoom;
    var newZoom = Math.round((zoom + delta) * 100) / 100;
    newZoom = Math.max(currentMinZoom(), Math.min(MAX_ZOOM, newZoom));
    if (newZoom === oldZoom) return;

    // If transitioning zoom=1 -> >1, capture native scroll to avoid jump
    if (oldZoom <= 1.001 && newZoom > 1.001) {
      // Start transform from natural position
      tx = 0;
      ty = -content.scrollTop;
    }

    // Point in image-space under (cx, cy)
    var ix = (cx - tx) / oldZoom;
    var iy = (cy - ty) / oldZoom;

    zoom = newZoom;
    tx = cx - ix * zoom;
    ty = cy - iy * zoom;

    clampPan();
    setZoomed(zoom > 1.001);
    applyTransform();
    showZoomBadge();
  }

  function resetZoomToFit() {
    var restoreScrollTop = 0;
    if (isMobileReadingMode() && zoom > 1.001) {
      restoreScrollTop = Math.max(0, Math.min(
        Math.max(0, baseH - content.clientHeight),
        Math.round(-ty / zoom)
      ));
    }
    zoom = 1;
    tx = 0; ty = 0;
    setZoomed(false);
    applyTransform();
    if (isMobileReadingMode()) {
      content.scrollTop = restoreScrollTop;
    }
    showZoomBadge();
  }

  // --- Render ---

  function render() {
    var meta = images[currentIdx];
    if (!meta) return;

    var baseUrl = cfg();
    var url = baseUrl + "/" + (meta.r2Key || "");

    var origW = meta.originalWidth || (meta.storedWidth * (meta.displayWidthScale || 1));
    var origH = meta.originalHeight || meta.storedHeight;
    var aspect = origH > 0 ? origW / origH : 1;

    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var dispW, dispH;

    content.classList.toggle("mobile-reading", isMobileReadingMode());

    if (isMobileReadingMode()) {
      dispW = Math.round(vw);
      dispH = Math.round(dispW / aspect);
    } else {
      if (vw * 0.96 / aspect <= vh * 0.88) {
        dispW = Math.round(vw * 0.96);
        dispH = Math.round(dispW / aspect);
      } else {
        dispH = Math.round(vh * 0.88);
        dispW = Math.round(dispH * aspect);
      }
    }

    baseW = dispW;
    baseH = dispH;

    stage.innerHTML = "";
    var img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.draggable = false;
    img.style.width = dispW + "px";
    img.style.height = dispH + "px";
    stage.appendChild(img);

    // Reset zoom/pan for the new image
    resetZoomToFit();
    content.scrollTop = 0;

    // Nav
    counter.textContent = (currentIdx + 1) + " / " + images.length;
    prevBtn.style.visibility = currentIdx > 0 ? "visible" : "hidden";
    nextBtn.style.visibility = currentIdx < images.length - 1 ? "visible" : "hidden";

    // Thumbnails
    highlightThumbnail();
    scrollThumbIntoView();

    // Preload adjacent
    preload(currentIdx - 1);
    preload(currentIdx + 1);
  }

  function preload(idx) {
    if (idx < 0 || idx >= images.length) return;
    var meta = images[idx];
    if (!meta || meta._preloaded) return;
    meta._preloaded = true;
    var link = document.createElement("link");
    link.rel = "preload";
    link.as = "image";
    link.href = cfg() + "/" + (meta.r2Key || "");
    document.head.appendChild(link);
  }

  // --- Thumbnails ---

  function buildThumbnails() {
    thumbStrip.innerHTML = "";
    if (!images || images.length <= 1) return;

    var baseUrl = cfg();
    images.forEach(function (meta, i) {
      var thumb = document.createElement("div");
      thumb.className = "lb-thumb";
      if (i === currentIdx) thumb.classList.add("active");
      thumb.title = (i + 1) + " / " + images.length;

      var tImg = document.createElement("img");
      tImg.src = baseUrl + "/" + (meta.r2Key || "");
      tImg.alt = "";
      tImg.loading = "lazy";
      tImg.decoding = "async";
      tImg.draggable = false;
      thumb.appendChild(tImg);

      thumb.addEventListener("click", function () {
        if (i === currentIdx) return;
        currentIdx = i;
        render();
      });

      thumbStrip.appendChild(thumb);
    });
    scrollThumbIntoView();
  }

  function highlightThumbnail() {
    var thumbs = thumbStrip.querySelectorAll(".lb-thumb");
    thumbs.forEach(function (t, i) { t.classList.toggle("active", i === currentIdx); });
  }

  function scrollThumbIntoView() {
    var active = thumbStrip.querySelector(".lb-thumb.active");
    if (active) active.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }

  // --- Show / Hide ---

  function show(imgs, idx) {
    if (!imgs || imgs.length === 0) return;
    images = imgs;
    currentIdx = idx;
    zoom = 1; tx = 0; ty = 0;
    buildThumbnails();
    render();
    showTip();
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function hide() {
    overlay.hidden = true;
    document.body.style.overflow = "";
    images = [];
    currentIdx = 0;
    zoom = 1; tx = 0; ty = 0;
    stage.innerHTML = "";
    thumbStrip.innerHTML = "";
    content.classList.remove("mobile-reading");
    setZoomed(false);
  }

  function prev() {
    if (currentIdx > 0) { currentIdx--; render(); }
  }

  function next() {
    if (currentIdx < images.length - 1) { currentIdx++; render(); }
  }

  // --- Tip ---

  function showTip() {
    tipEl.textContent = isMobileReadingMode()
      ? "上下滑动阅读 · 双指缩放 · 放大后拖动"
      : "滚轮缩放 · 拖拽移动 · Shift+滚轮上下 · 方向键切换";
    tipEl.hidden = false;
    tipEl.classList.add("visible");
    clearTimeout(tipTimer);
    tipTimer = setTimeout(function () { tipEl.classList.remove("visible"); }, 2600);
  }

  // ================================================================
  //   EVENTS (bound once)
  // ================================================================

  // --- Buttons ---
  closeBtn.addEventListener("click", hide);
  prevBtn.addEventListener("click", prev);
  nextBtn.addEventListener("click", next);

  // --- Overlay click (backdrop) ---
  overlay.addEventListener("click", function (e) {
    if (e.target === overlay) hide();
  });

  // --- Wheel ---
  content.addEventListener("wheel", function (e) {
    e.preventDefault();
    if (e.shiftKey) {
      if (zoom > 1.001) {
        ty -= e.deltaY;
        clampPan();
        applyTransform();
      }
      // at zoom=1, native scroll handles shift+wheel via overflow-y:auto
      return;
    }
    var rect = content.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    var delta = e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
    zoomAt(cx, cy, delta);
  }, { passive: false });

  // --- Drag ---
  content.addEventListener("mousedown", function (e) {
    if (e.button !== 0) return;
    dragging = true;
    dragMoved = false;
    dragSX = e.clientX;
    dragSY = e.clientY;
    dragTX = tx;
    dragTY = ty;
    e.preventDefault();
  });

  window.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    var dx = e.clientX - dragSX;
    var dy = e.clientY - dragSY;
    if (!dragMoved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
    dragMoved = true;
    tx = dragTX + dx;
    ty = dragTY + dy;
    clampPan();
    applyTransform();
  });

  window.addEventListener("mouseup", function () {
    dragging = false;
  });

  // --- Double-click ---
  content.addEventListener("dblclick", function (e) {
    e.preventDefault();
    if (zoom > 1.001) {
      resetZoomToFit();
    } else {
      var rect = content.getBoundingClientRect();
      zoomAt(rect.width / 2, rect.height / 2, 1);
    }
  });

  // --- Touch ---
  content.addEventListener("touchstart", function (e) {
    if (e.touches.length === 2) {
      e.preventDefault();
      if (zoom <= 1.001) {
        tx = 0;
        ty = -content.scrollTop;
      }
      var ddx = e.touches[0].clientX - e.touches[1].clientX;
      var ddy = e.touches[0].clientY - e.touches[1].clientY;
      pinchBaseDist = Math.sqrt(ddx * ddx + ddy * ddy);
      pinchBaseZoom = zoom;
      dragging = false;
    } else if (e.touches.length === 1) {
      dragSX = e.touches[0].clientX;
      dragSY = e.touches[0].clientY;
      dragTX = tx;
      dragTY = ty;
      dragging = zoom > 1.001 || !isMobileReadingMode();
      dragMoved = false;
    }
    swipeStartX = e.touches[0].clientX;
    swipeStartY = e.touches[0].clientY;
  }, { passive: false });

  content.addEventListener("touchmove", function (e) {
    if (e.touches.length === 2 && pinchBaseDist > 0) {
      e.preventDefault();
      var ddx = e.touches[0].clientX - e.touches[1].clientX;
      var ddy = e.touches[0].clientY - e.touches[1].clientY;
      var rect = content.getBoundingClientRect();
      var cx = ((e.touches[0].clientX + e.touches[1].clientX) / 2) - rect.left;
      var cy = ((e.touches[0].clientY + e.touches[1].clientY) / 2) - rect.top;
      var dist = Math.sqrt(ddx * ddx + ddy * ddy);
      var newZoom = pinchBaseZoom * (dist / pinchBaseDist);
      newZoom = Math.max(currentMinZoom(), Math.min(MAX_ZOOM, newZoom));
      if (newZoom === zoom) return;
      var oldZoom = zoom;
      var ix = (cx - tx) / oldZoom;
      var iy = (cy - ty) / oldZoom;
      zoom = newZoom;
      tx = cx - ix * zoom;
      ty = cy - iy * zoom;
      clampPan();
      setZoomed(zoom > 1.001);
      applyTransform();
      showZoomBadge();
    } else if (e.touches.length === 1 && dragging) {
      e.preventDefault();
      var dx = e.touches[0].clientX - dragSX;
      var dy = e.touches[0].clientY - dragSY;
      if (!dragMoved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
      dragMoved = true;
      tx = dragTX + dx;
      ty = dragTY + dy;
      clampPan();
      applyTransform();
    }
  }, { passive: true });

  content.addEventListener("touchend", function (e) {
    if (e.touches.length === 0) {
      // Swipe for prev/next (only at zoom=1 and no drag)
      if (!dragMoved && zoom <= 1.001 && !isMobileReadingMode()) {
        var swipeDx = swipeStartX - e.changedTouches[0].clientX;
        var swipeDy = swipeStartY - e.changedTouches[0].clientY;
        if (Math.abs(swipeDx) > 60 && Math.abs(swipeDx) > Math.abs(swipeDy)) {
          if (swipeDx > 0) next(); else prev();
        }
      }
      dragging = false;
      pinchBaseDist = 0;
      if (zoom <= 1.001) setZoomed(false);
    }
  });

  // --- Keyboard ---
  document.addEventListener("keydown", function (e) {
    if (overlay.hidden) return;
    switch (e.key) {
      case "Escape": hide(); break;
      case "ArrowLeft":  prev(); break;
      case "ArrowRight": next(); break;
      case "+": case "=":
        e.preventDefault();
        zoomAt(content.clientWidth / 2, content.clientHeight / 2, ZOOM_STEP);
        break;
      case "-":
        e.preventDefault();
        zoomAt(content.clientWidth / 2, content.clientHeight / 2, -ZOOM_STEP);
        break;
      case "0":
        e.preventDefault();
        if (zoom > 1.001) resetZoomToFit();
        break;
    }
  });

  // --- Resize ---
  window.addEventListener("resize", function () {
    if (!overlay.hidden) render();
  });

  // --- Dismiss tip on interaction ---
  overlay.addEventListener("wheel", function () {
    if (tipTimer) { clearTimeout(tipTimer); tipEl.classList.remove("visible"); }
  });

  window.openViewer = show;
})();
