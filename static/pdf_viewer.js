/**
 * FolioPdfViewer — PDF.js lazy-rendered continuous viewer with selectable textLayer,
 * saved scholarly highlights, TOC navigation, and zoom/fit-width controls.
 */
(function () {
  if (typeof window.pdfjsLib !== "undefined") {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }

  const pdfState = {
    bookId: "",
    doc: null,
    pageCount: 0,
    scale: 1.25,
    fitWidth: true,
    currentPage: 1,
    renderedPages: new Set(),
    renderingPages: new Set(),
    observer: null,
    callbacks: {},
    pendingHighlightPhrase: "",
    baseViewportWidth: 612,
    baseViewportHeight: 792,
  };

  function _els() {
    return {
      container: document.getElementById("pdfViewerContainer"),
      scroll: document.getElementById("pdfPagesScroll"),
      wrap: document.getElementById("pdfPagesWrap"),
      pageInput: document.getElementById("pdfPageInput"),
      pageTotal: document.getElementById("pdfPageTotal"),
      zoomLabel: document.getElementById("pdfZoomLabel"),
      btnPrev: document.getElementById("btnPdfPrevPage"),
      btnNext: document.getElementById("btnPdfNextPage"),
      btnZoomOut: document.getElementById("btnPdfZoomOut"),
      btnZoomIn: document.getElementById("btnPdfZoomIn"),
      btnFitWidth: document.getElementById("btnPdfFitWidth"),
    };
  }

  function _computeFitScale(targetWidth = null) {
    const { scroll } = _els();
    if (!scroll) return 1.25;
    const avail = Math.max(320, scroll.clientWidth - 48);
    const refW = Math.max(200, targetWidth || pdfState.baseViewportWidth);
    const raw = avail / refW;
    return Math.max(0.6, Math.min(3.0, raw));
  }

  async function _measureMedianBodyViewport(doc) {
    const n = doc.numPages || 1;
    // Sample interior pages (skipping page 1 when n > 2 so oversized Project MUSE / JSTOR cover sheets don't skew Fit Width)
    const sampleIndices = n <= 2
      ? Array.from({ length: n }, (_, i) => i + 1)
      : Array.from(new Set([
          2,
          Math.min(n, 4),
          Math.min(n, Math.max(5, Math.floor(n * 0.25))),
          Math.min(n, Math.max(6, Math.floor(n * 0.5))),
          Math.min(n, Math.max(7, Math.floor(n * 0.75))),
        ]));
    const widths = [];
    const heights = [];
    for (const pno of sampleIndices) {
      try {
        const pg = await doc.getPage(pno);
        const vp = pg.getViewport({ scale: 1.0 });
        if (vp.width > 100 && vp.height > 100) {
          widths.push(vp.width);
          heights.push(vp.height);
        }
      } catch (_) {}
    }
    if (!widths.length) return { width: 612, height: 792 };
    widths.sort((a, b) => a - b);
    heights.sort((a, b) => a - b);
    const mid = Math.floor(widths.length / 2);
    return { width: widths[mid], height: heights[mid] };
  }

  async function fitCurrentPageWidth() {
    if (!pdfState.doc) return;
    let refWidth = pdfState.baseViewportWidth;
    try {
      const pno = pdfState.currentPage === 1 && pdfState.pageCount > 2 ? 2 : pdfState.currentPage;
      const pg = await pdfState.doc.getPage(pno);
      const vp = pg.getViewport({ scale: 1.0 });
      if (vp.width > 100) refWidth = vp.width;
    } catch (_) {}
    await setScale(_computeFitScale(refWidth), true);
  }

  function _updateToolbar() {
    const { pageInput, pageTotal, zoomLabel, btnPrev, btnNext } = _els();
    if (pageInput) {
      pageInput.value = String(pdfState.currentPage);
      pageInput.max = String(Math.max(1, pdfState.pageCount));
    }
    if (pageTotal) pageTotal.textContent = String(pdfState.pageCount || 1);
    if (zoomLabel) zoomLabel.textContent = `${Math.round(pdfState.scale * 100)}%`;
    if (btnPrev) btnPrev.disabled = pdfState.currentPage <= 1;
    if (btnNext) btnNext.disabled = pdfState.currentPage >= pdfState.pageCount;
  }

  function _teardownObserver() {
    if (pdfState.observer) {
      pdfState.observer.disconnect();
      pdfState.observer = null;
    }
  }

  function destroy() {
    _teardownObserver();
    if (pdfState.doc) {
      try {
        pdfState.doc.destroy();
      } catch (_) {}
    }
    pdfState.doc = null;
    pdfState.bookId = "";
    pdfState.pageCount = 0;
    pdfState.currentPage = 1;
    pdfState.renderedPages.clear();
    pdfState.renderingPages.clear();
    const { wrap } = _els();
    if (wrap) wrap.innerHTML = "";
  }

  function _buildPlaceholders() {
    const { wrap, scroll } = _els();
    if (!wrap || !pdfState.doc) return;
    _teardownObserver();
    pdfState.renderedPages.clear();
    pdfState.renderingPages.clear();

    const estW = Math.round(pdfState.baseViewportWidth * pdfState.scale);
    const estH = Math.round(pdfState.baseViewportHeight * pdfState.scale);

    const frag = document.createDocumentFragment();
    for (let p = 1; p <= pdfState.pageCount; p++) {
      const slot = document.createElement("div");
      slot.className = "pdf-page-slot";
      slot.dataset.pageNumber = String(p);
      slot.style.width = `${estW}px`;
      slot.style.minHeight = `${estH}px`;
      slot.innerHTML = `<div class="pdf-page-badge">p. ${p}</div>`;
      frag.appendChild(slot);
    }
    wrap.innerHTML = "";
    wrap.appendChild(frag);

    pdfState.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pno = parseInt(entry.target.dataset.pageNumber, 10);
            if (pno) _renderPage(pno);
          }
        }
      },
      { root: scroll, rootMargin: "700px 0px" }
    );

    wrap.querySelectorAll(".pdf-page-slot").forEach((slot) => pdfState.observer.observe(slot));
  }

  async function _renderPage(pageNum) {
    if (!pdfState.doc || pdfState.renderedPages.has(pageNum) || pdfState.renderingPages.has(pageNum)) {
      return;
    }
    const { wrap } = _els();
    const slot = wrap?.querySelector(`.pdf-page-slot[data-page-number="${pageNum}"]`);
    if (!slot) return;

    pdfState.renderingPages.add(pageNum);
    try {
      const page = await pdfState.doc.getPage(pageNum);
      const viewport = page.getViewport({ scale: pdfState.scale });
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      slot.style.width = `${Math.round(viewport.width)}px`;
      slot.style.height = `${Math.round(viewport.height)}px`;
      slot.style.minHeight = `${Math.round(viewport.height)}px`;
      slot.style.setProperty("--scale-factor", String(pdfState.scale));
      slot.innerHTML = "";

      const canvas = document.createElement("canvas");
      canvas.className = "pdf-page-canvas";
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      slot.appendChild(canvas);

      const ctx = canvas.getContext("2d", { alpha: false });
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      await page.render({ canvasContext: ctx, viewport }).promise;

      const hlLayerDiv = document.createElement("div");
      hlLayerDiv.className = "pdf-highlight-layer";
      hlLayerDiv.style.width = `${viewport.width}px`;
      hlLayerDiv.style.height = `${viewport.height}px`;
      slot.appendChild(hlLayerDiv);

      const textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      textLayerDiv.style.width = `${viewport.width}px`;
      textLayerDiv.style.height = `${viewport.height}px`;
      slot.appendChild(textLayerDiv);

      const textContent = await page.getTextContent();
      if (window.pdfjsLib && typeof window.pdfjsLib.renderTextLayer === "function") {
        await window.pdfjsLib.renderTextLayer({
          textContentSource: textContent,
          container: textLayerDiv,
          viewport,
          textDivs: [],
        }).promise;
      }

      const badge = document.createElement("div");
      badge.className = "pdf-page-badge";
      badge.textContent = `p. ${pageNum}`;
      slot.appendChild(badge);

      pdfState.renderedPages.add(pageNum);
      _applyHighlightsToPage(pageNum, slot, textLayerDiv, hlLayerDiv);
    } catch (err) {
      console.warn(`PDF page ${pageNum} render error:`, err);
    } finally {
      pdfState.renderingPages.delete(pageNum);
    }
  }

  function _buildCompactTextIndex(rootEl) {
    // Maps every non-whitespace character in textLayer text nodes to (node, offset)
    // so PDF kerning spaces (e.g. "anx i eties") never break character-exact matching.
    const chars = [];
    const nodes = [];
    const offsets = [];
    const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      const val = node.nodeValue || "";
      for (let i = 0; i < val.length; i++) {
        const ch = val[i];
        if (!/\s/.test(ch)) {
          chars.push(ch.toLowerCase());
          nodes.push(node);
          offsets.push(i);
        }
      }
    }
    return { compact: chars.join(""), nodes, offsets };
  }

  function _applyHighlightsToPage(pageNum, slot, textLayerDiv, hlLayerDiv) {
    if (!slot || !textLayerDiv || !hlLayerDiv) return;
    hlLayerDiv.innerHTML = "";

    const getAnnotations = pdfState.callbacks.getAnnotations;
    const anns = typeof getAnnotations === "function" ? getAnnotations() : [];
    const pageTag = `p. ${pageNum}`;

    const pageAnns = anns.filter((a) => {
      const pn = (a.page_number || "").trim().toLowerCase();
      return pn === pageTag || pn === String(pageNum);
    });
    if (!pageAnns.length) return;

    const index = _buildCompactTextIndex(textLayerDiv);
    if (!index.compact.length) return;

    const slotRect = slot.getBoundingClientRect();
    if (!slotRect.width || !slotRect.height) return;

    for (const ann of pageAnns) {
      const needle = (ann.selected_text || "").replace(/\s+/g, "").toLowerCase();
      if (needle.length < 2) continue;

      let startIdx = index.compact.indexOf(needle);
      let matchLen = needle.length;
      if (startIdx === -1 && needle.length > 32) {
        // Fallback: anchor on start + end chunks
        const head = needle.slice(0, 28);
        const tail = needle.slice(-24);
        const hIdx = index.compact.indexOf(head);
        if (hIdx !== -1) {
          startIdx = hIdx;
          const tIdx = index.compact.indexOf(tail, hIdx + head.length);
          matchLen = tIdx !== -1 && tIdx - hIdx < needle.length * 1.4
            ? (tIdx + tail.length) - hIdx
            : Math.min(needle.length, index.compact.length - hIdx);
        }
      }
      if (startIdx === -1) continue;

      const endCharIdx = Math.min(index.compact.length - 1, startIdx + matchLen - 1);
      const startNode = index.nodes[startIdx];
      const startOff = index.offsets[startIdx];
      const endNode = index.nodes[endCharIdx];
      const endOff = index.offsets[endCharIdx] + 1;

      try {
        const range = document.createRange();
        range.setStart(startNode, startOff);
        range.setEnd(endNode, Math.min(endOff, (endNode.nodeValue || "").length));
        const rects = Array.from(range.getClientRects());
        const color = (ann.color || "yellow").toLowerCase();

        for (const r of rects) {
          if (r.width < 1 || r.height < 1) continue;
          const box = document.createElement("div");
          box.className = `pdf-hl-rect hl-${color}`;
          box.dataset.annId = ann.id;
          box.style.left = `${r.left - slotRect.left}px`;
          box.style.top = `${r.top - slotRect.top}px`;
          box.style.width = `${r.width}px`;
          box.style.height = `${r.height}px`;
          hlLayerDiv.appendChild(box);
        }
      } catch (err) {
        console.warn("PDF highlight range error:", err);
      }
    }
  }

  function refreshHighlights() {
    const { wrap } = _els();
    if (!wrap) return;
    for (const pno of pdfState.renderedPages) {
      const slot = wrap.querySelector(`.pdf-page-slot[data-page-number="${pno}"]`);
      const tl = slot?.querySelector(".textLayer");
      const hl = slot?.querySelector(".pdf-highlight-layer");
      if (slot && tl && hl) _applyHighlightsToPage(pno, slot, tl, hl);
    }
  }

  function _detectCurrentPageFromScroll() {
    const { scroll, wrap } = _els();
    if (!scroll || !wrap || !pdfState.pageCount) return 1;
    const scrollMid = scroll.scrollTop + scroll.clientHeight * 0.25;
    const slots = wrap.querySelectorAll(".pdf-page-slot");
    let best = 1;
    for (const slot of slots) {
      if (slot.offsetTop <= scrollMid) {
        best = parseInt(slot.dataset.pageNumber, 10) || best;
      } else {
        break;
      }
    }
    return best;
  }

  function getScrollPercent() {
    const { scroll } = _els();
    if (!scroll) return 0;
    const maxScroll = scroll.scrollHeight - scroll.clientHeight;
    if (maxScroll <= 0) return 0;
    return Math.max(0, Math.min(1, scroll.scrollTop / maxScroll));
  }

  function scrollToPage(pageNum, opts = {}) {
    const { scroll, wrap } = _els();
    if (!scroll || !wrap || !pdfState.pageCount) return;
    const clamped = Math.max(1, Math.min(pdfState.pageCount, parseInt(pageNum, 10) || 1));
    const slot = wrap.querySelector(`.pdf-page-slot[data-page-number="${clamped}"]`);
    if (slot) {
      scroll.scrollTo({
        top: Math.max(0, slot.offsetTop - 12),
        behavior: opts.instant ? "auto" : "smooth",
      });
      pdfState.currentPage = clamped;
      _updateToolbar();
      _renderPage(clamped);
      if (clamped + 1 <= pdfState.pageCount) _renderPage(clamped + 1);
      if (typeof pdfState.callbacks.onPageChange === "function") {
        pdfState.callbacks.onPageChange(clamped, pdfState.pageCount);
      }
    }
  }

  async function setScale(newScale, isFitWidth = false) {
    const curPage = pdfState.currentPage;
    pdfState.fitWidth = isFitWidth;
    pdfState.scale = Math.max(0.5, Math.min(3.5, newScale));
    _updateToolbar();
    _buildPlaceholders();
    scrollToPage(curPage, { instant: true });
  }

  async function open(bookId, opts = {}, callbacks = {}) {
    destroy();
    pdfState.bookId = bookId;
    pdfState.callbacks = callbacks || {};

    if (typeof window.pdfjsLib === "undefined") {
      throw new Error("PDF.js failed to load. Check your network connection.");
    }

    const url = `/pdf/${encodeURIComponent(bookId)}/book.pdf`;
    const loadingTask = window.pdfjsLib.getDocument({ url, withCredentials: true });
    const doc = await loadingTask.promise;
    pdfState.doc = doc;
    pdfState.pageCount = doc.numPages || 1;

    const bodyVp = await _measureMedianBodyViewport(doc);
    pdfState.baseViewportWidth = bodyVp.width || 612;
    pdfState.baseViewportHeight = bodyVp.height || 792;
    pdfState.scale = _computeFitScale(pdfState.baseViewportWidth);
    pdfState.fitWidth = true;

    const startPage = Math.max(1, Math.min(pdfState.pageCount, opts.startPage || 1));
    pdfState.currentPage = startPage;
    _updateToolbar();
    _buildPlaceholders();
    scrollToPage(startPage, { instant: true });
  }

  function _bindControlsOnce() {
    const { scroll, pageInput, btnPrev, btnNext, btnZoomOut, btnZoomIn, btnFitWidth, wrap } = _els();
    if (!scroll || scroll.dataset.pdfBound) return;
    scroll.dataset.pdfBound = "1";

    let scrollTimer = null;
    scroll.addEventListener("scroll", () => {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        const pno = _detectCurrentPageFromScroll();
        if (pno !== pdfState.currentPage) {
          pdfState.currentPage = pno;
          _updateToolbar();
          if (typeof pdfState.callbacks.onPageChange === "function") {
            pdfState.callbacks.onPageChange(pno, pdfState.pageCount);
          }
        }
        if (typeof pdfState.callbacks.onScrollSave === "function") {
          pdfState.callbacks.onScrollSave(pdfState.currentPage, getScrollPercent());
        }
      }, 180);
    });

    btnPrev?.addEventListener("click", () => scrollToPage(pdfState.currentPage - 1));
    btnNext?.addEventListener("click", () => scrollToPage(pdfState.currentPage + 1));
    pageInput?.addEventListener("change", () => {
      const val = parseInt(pageInput.value, 10);
      if (!isNaN(val)) scrollToPage(val);
    });
    pageInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const val = parseInt(pageInput.value, 10);
        if (!isNaN(val)) scrollToPage(val);
      }
    });

    btnZoomOut?.addEventListener("click", () => setScale(pdfState.scale - 0.15, false));
    btnZoomIn?.addEventListener("click", () => setScale(pdfState.scale + 0.15, false));
    btnFitWidth?.addEventListener("click", () => fitCurrentPageWidth());

    // Keyboard & keypad zoom shortcuts (+ / = / NumpadAdd, - / NumpadSubtract, 0 / Numpad0)
    document.addEventListener("keydown", (e) => {
      const { container } = _els();
      if (!pdfState.doc || !container || container.classList.contains("hidden")) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) {
        return;
      }
      if (e.key === "+" || e.key === "=" || e.code === "NumpadAdd") {
        e.preventDefault();
        setScale(pdfState.scale + 0.15, false);
      } else if (e.key === "-" || e.key === "_" || e.code === "NumpadSubtract") {
        e.preventDefault();
        setScale(pdfState.scale - 0.15, false);
      } else if (e.key === "0" || e.code === "Numpad0") {
        e.preventDefault();
        fitCurrentPageWidth();
      }
    });

    // Trackpad pinch-zoom / Ctrl+wheel inside PDF viewer
    let wheelZoomTimer = null;
    let pendingWheelScale = null;
    scroll.addEventListener(
      "wheel",
      (e) => {
        if (!pdfState.doc || !(e.ctrlKey || e.metaKey)) return;
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.12 : -0.12;
        pendingWheelScale = Math.max(0.5, Math.min(3.5, (pendingWheelScale ?? pdfState.scale) + delta));
        if (wheelZoomTimer) clearTimeout(wheelZoomTimer);
        wheelZoomTimer = setTimeout(() => {
          const target = pendingWheelScale;
          pendingWheelScale = null;
          if (target !== null) setScale(target, false);
        }, 90);
      },
      { passive: false }
    );

    // Text selection inside PDF textLayer -> trigger Folio's selectionPopover
    wrap?.addEventListener("mouseup", () => {
      setTimeout(() => {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) return;
        const text = sel.toString().trim();
        if (text.length < 2) return;
        const anchorNode = sel.anchorNode;
        const anchorEl = anchorNode?.nodeType === 3 ? anchorNode.parentElement : anchorNode;
        const slot = anchorEl?.closest?.(".pdf-page-slot");
        if (!slot) return;
        const pageNum = parseInt(slot.dataset.pageNumber, 10) || pdfState.currentPage;
        if (typeof pdfState.callbacks.onTextSelect === "function") {
          pdfState.callbacks.onTextSelect({ text, pageNumber: `p. ${pageNum}`, pageNum });
        }
      }, 20);
    });

    window.addEventListener("resize", () => {
      if (pdfState.doc && pdfState.fitWidth) {
        const nextScale = _computeFitScale();
        if (Math.abs(nextScale - pdfState.scale) > 0.08) {
          setScale(nextScale, true);
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", _bindControlsOnce);

  window.FolioPdfViewer = {
    open,
    destroy,
    scrollToPage,
    refreshHighlights,
    getCurrentPage: () => pdfState.currentPage,
    getPageCount: () => pdfState.pageCount,
    getScrollPercent,
  };
})();
