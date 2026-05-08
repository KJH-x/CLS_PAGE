// viewer.js — Full-screen image lightbox

(function () {
  var R2_BASE = window.ARCHIVE_CONFIG.R2_PUBLIC_URL;
  var overlay = document.getElementById("lightbox");
  var content = document.getElementById("lbContent");
  var counter = document.getElementById("lbCounter");
  var closeBtn = document.getElementById("lbClose");
  var prevBtn = document.getElementById("lbPrev");
  var nextBtn = document.getElementById("lbNext");

  var images = [];
  var currentIdx = 0;
  var touchStartX = 0;

  function show(imgs, idx) {
    images = imgs;
    currentIdx = idx;
    render();
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
    overlay.focus();
  }

  function hide() {
    overlay.hidden = true;
    document.body.style.overflow = "";
    images = [];
    currentIdx = 0;
    content.innerHTML = "";
  }

  function render() {
    var meta = images[currentIdx];
    if (!meta) return;

    var url = R2_BASE + "/" + meta.r2Key;
    var origW = meta.originalWidth || meta.storedWidth * (meta.displayWidthScale || 3);
    var origH = meta.originalHeight || meta.storedHeight;
    var scale = (meta.displayWidthScale || 3);

    // Compute display dimensions to show at proper aspect ratio
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var maxW = vw * 0.9;
    var maxH = vh * 0.85;
    var aspect = origW / (origH || 1);

    var dispW, dispH;
    if (maxW / aspect <= maxH) {
      dispW = maxW;
      dispH = maxW / aspect;
    } else {
      dispH = maxH;
      dispW = maxH * aspect;
    }

    content.innerHTML = "";
    var img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.style.width = Math.round(dispW) + "px";
    img.style.height = Math.round(dispH) + "px";

    img.addEventListener("click", function (e) {
      e.stopPropagation();
    });

    content.appendChild(img);
    counter.textContent = (currentIdx + 1) + " / " + images.length;

    prevBtn.style.visibility = currentIdx > 0 ? "visible" : "hidden";
    nextBtn.style.visibility = currentIdx < images.length - 1 ? "visible" : "hidden";
  }

  function prev() {
    if (currentIdx > 0) {
      currentIdx--;
      render();
    }
  }

  function next() {
    if (currentIdx < images.length - 1) {
      currentIdx++;
      render();
    }
  }

  // --- Event handlers ---

  closeBtn.addEventListener("click", hide);
  prevBtn.addEventListener("click", prev);
  nextBtn.addEventListener("click", next);

  overlay.addEventListener("click", function (e) {
    if (e.target === overlay) hide();
  });

  document.addEventListener("keydown", function (e) {
    if (overlay.hidden) return;
    switch (e.key) {
      case "Escape": hide(); break;
      case "ArrowLeft": prev(); break;
      case "ArrowRight": next(); break;
    }
  });

  // Touch swipe
  overlay.addEventListener("touchstart", function (e) {
    touchStartX = e.touches[0].clientX;
  }, { passive: true });

  overlay.addEventListener("touchend", function (e) {
    var diff = touchStartX - e.changedTouches[0].clientX;
    if (Math.abs(diff) > 60) {
      if (diff > 0) next();
      else prev();
    }
  });

  window.openViewer = show;
})();
