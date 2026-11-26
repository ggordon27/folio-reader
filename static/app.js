/**
 * Folio — Google Drive EPUB Reader & Gemini Companion
 * Frontend Client Logic
 */

const HIGHLIGHT_CATEGORIES = {
  blue: {
    key: "blue",
    label: "Critical Points",
    short: "Critical Point",
    desc: "Key interventions or important components of the overall argument",
    borderColor: "#2563eb",
  },
  yellow: {
    key: "yellow",
    label: "Significant Points",
    short: "Significant Point",
    desc: "Usually the main idea of the paragraph",
    borderColor: "#ca8a04",
  },
  red: {
    key: "red",
    label: "Critiques of Scholarship",
    short: "Critique",
    desc: "The author's critiques of other scholarship",
    borderColor: "#dc2626",
  },
  green: {
    key: "green",
    label: "Definitions & Operationalization",
    short: "Definition",
    desc: "Key terms or operationalization of key variables",
    borderColor: "#16a34a",
  },
  grey: {
    key: "grey",
    label: "Concerns & Questions",
    short: "Concern",
    desc: "Passages that I either disagree with or find confusing",
    borderColor: "#4b5563",
  },
};

const state = {
  books: [],
  uncompiledFolders: [],
  libraryFormat: localStorage.getItem("folio_library_format") || "epub",
  pdfCategoryFilter: "",
  pdfSyncedOnce: false,
  filterQuery: "",
  folderFilter: "",
  tagFilter: "",
  librarySort: localStorage.getItem("folio_library_sort") || "recent",
  libraryViewMode: localStorage.getItem("folio_library_view") || "grid",
  displayLimit: 120,
  activeBook: null,
  currentChapterIndex: 0,
  pendingScrollPercent: null,
  bookmarks: [],
  annotations: [],
  annFilterColor: "all",
  annGroupBy: "category",
  annSearchQuery: "",
  readingReportMarkdown: "",
  chats: [],
  lastInteractionId: "",
  selectedText: "",
  selectedPageNumber: "",
  activeEndnote: null,
  endnoteDocCache: {},
  attachedPassage: "",
  editingAnnotation: null,
  editorColor: "yellow",
  activeMiniAnnId: "",
  tagPickerBookId: "",
  theme: localStorage.getItem("folio_theme") || "sepia",
  fontSizePct: parseInt(localStorage.getItem("folio_font_size") || "105", 10),
  fontFamily: localStorage.getItem("folio_font_family") || "serif",
  lineHeight: localStorage.getItem("folio_line_height") || "1.72",
  columnWidth: localStorage.getItem("folio_col_width") || "68ch",
  textAlign: localStorage.getItem("folio_text_align") || "left",
  geminiConfigured: false,
  geminiModel: "gemini-4-hydrogen",
};

// DOM Elements
const el = {
  libraryView: document.getElementById("libraryView"),
  readerView: document.getElementById("readerView"),
  libraryGrid: document.getElementById("libraryGrid"),
  libraryLoading: document.getElementById("libraryLoading"),
  libraryLoadingText: document.getElementById("libraryLoadingText"),
  libraryFilterInput: document.getElementById("libraryFilterInput"),
  libraryFolderSelect: document.getElementById("libraryFolderSelect"),
  libraryTagSelect: document.getElementById("libraryTagSelect"),
  librarySortSelect: document.getElementById("librarySortSelect"),
  libraryHeroTitle: document.getElementById("libraryHeroTitle"),
  libraryHeroDesc: document.getElementById("libraryHeroDesc"),
  btnShelfEpub: document.getElementById("btnShelfEpub"),
  btnShelfPdf: document.getElementById("btnShelfPdf"),
  btnShelfAll: document.getElementById("btnShelfAll"),
  epubCountPill: document.getElementById("epubCountPill"),
  pdfCountPill: document.getElementById("pdfCountPill"),
  allCountPill: document.getElementById("allCountPill"),
  pdfCategorySelect: document.getElementById("pdfCategorySelect"),
  btnOpenUncompiled: document.getElementById("btnOpenUncompiled"),
  uncompiledCountBadge: document.getElementById("uncompiledCountBadge"),
  uncompiledModal: document.getElementById("uncompiledModal"),
  btnCloseUncompiled: document.getElementById("btnCloseUncompiled"),
  uncompiledFilterInput: document.getElementById("uncompiledFilterInput"),
  btnCompileAllUncompiled: document.getElementById("btnCompileAllUncompiled"),
  uncompiledList: document.getElementById("uncompiledList"),
  epubFrameContainer: document.getElementById("epubFrameContainer"),
  pdfViewerContainer: document.getElementById("pdfViewerContainer"),
  btnTypographyMenu: document.getElementById("btnTypographyMenu"),
  btnViewGrid: document.getElementById("btnViewGrid"),
  btnViewList: document.getElementById("btnViewList"),
  continueReadingSection: document.getElementById("continueReadingSection"),
  continueReadingRow: document.getElementById("continueReadingRow"),
  btnToggleGlobalSearch: document.getElementById("btnToggleGlobalSearch"),
  globalSearchPanel: document.getElementById("globalSearchPanel"),
  globalSearchInput: document.getElementById("globalSearchInput"),
  btnRunGlobalSearch: document.getElementById("btnRunGlobalSearch"),
  btnCloseGlobalSearch: document.getElementById("btnCloseGlobalSearch"),
  globalSearchResults: document.getElementById("globalSearchResults"),
  btnRefreshLibrary: document.getElementById("btnRefreshLibrary"),

  btnBackToLibrary: document.getElementById("btnBackToLibrary"),
  brandLogo: document.getElementById("brandLogo"),
  activeBookHeader: document.getElementById("activeBookHeader"),
  headerBookTitle: document.getElementById("headerBookTitle"),
  headerChapterTitle: document.getElementById("headerChapterTitle"),
  readerToolbar: document.getElementById("readerToolbar"),

  btnToggleLeftSidebar: document.getElementById("btnToggleLeftSidebar"),
  btnToggleRightSidebar: document.getElementById("btnToggleRightSidebar"),
  leftSidebar: document.getElementById("leftSidebar"),
  rightSidebar: document.getElementById("rightSidebar"),

  btnFontDec: document.getElementById("btnFontDec"),
  btnFontInc: document.getElementById("btnFontInc"),
  fontSizeLabel: document.getElementById("fontSizeLabel"),
  selectFontFamily: document.getElementById("selectFontFamily"),
  btnTypographyMenu: document.getElementById("btnTypographyMenu"),
  typographyPopover: document.getElementById("typographyPopover"),
  selectLineHeight: document.getElementById("selectLineHeight"),
  selectColumnWidth: document.getElementById("selectColumnWidth"),
  selectTextAlign: document.getElementById("selectTextAlign"),

  btnAddBookmark: document.getElementById("btnAddBookmark"),
  btnExportMenu: document.getElementById("btnExportMenu"),
  exportMenuPopover: document.getElementById("exportMenuPopover"),
  btnToggleFocusMode: document.getElementById("btnToggleFocusMode"),
  btnExitFocus: document.getElementById("btnExitFocus"),
  leftResizeHandle: document.getElementById("leftResizeHandle"),
  rightResizeHandle: document.getElementById("rightResizeHandle"),
  chapterProgressBar: document.getElementById("chapterProgressBar"),
  highlightMiniToolbar: document.getElementById("highlightMiniToolbar"),
  btnMiniEditNote: document.getElementById("btnMiniEditNote"),
  btnMiniAskGemini: document.getElementById("btnMiniAskGemini"),
  btnMiniDelete: document.getElementById("btnMiniDelete"),
  tagPickerPopover: document.getElementById("tagPickerPopover"),
  tagPickerChips: document.getElementById("tagPickerChips"),
  tagPickerInput: document.getElementById("tagPickerInput"),
  btnTagPickerAdd: document.getElementById("btnTagPickerAdd"),
  btnSyncDrive: document.getElementById("btnSyncDrive"),
  syncDriveLabel: document.getElementById("syncDriveLabel"),
  btnExportMd: document.getElementById("btnExportMd"),
  btnExportBib: document.getElementById("btnExportBib"),
  btnExportGdoc: document.getElementById("btnExportGdoc"),

  tocList: document.getElementById("tocList"),
  bookSearchInput: document.getElementById("bookSearchInput"),
  btnRunBookSearch: document.getElementById("btnRunBookSearch"),
  bookSearchResults: document.getElementById("bookSearchResults"),
  bmCountBadge: document.getElementById("bmCountBadge"),
  bookmarkLabelInput: document.getElementById("bookmarkLabelInput"),
  btnSaveBookmarkPanel: document.getElementById("btnSaveBookmarkPanel"),
  bookmarksList: document.getElementById("bookmarksList"),

  bookLoadingOverlay: document.getElementById("bookLoadingOverlay"),
  bookLoadingMessage: document.getElementById("bookLoadingMessage"),
  epubFrame: document.getElementById("epubFrame"),
  btnPrevChapter: document.getElementById("btnPrevChapter"),
  btnNextChapter: document.getElementById("btnNextChapter"),
  footerChapterProgress: document.getElementById("footerChapterProgress"),

  selectionPopover: document.getElementById("selectionPopover"),
  btnPopAnnotate: document.getElementById("btnPopAnnotate"),
  btnPopExplain: document.getElementById("btnPopExplain"),
  btnPopCritique: document.getElementById("btnPopCritique"),
  btnPopAskCustom: document.getElementById("btnPopAskCustom"),

  endnotePopover: document.getElementById("endnotePopover"),
  endnoteBadge: document.getElementById("endnoteBadge"),
  endnoteChapterHint: document.getElementById("endnoteChapterHint"),
  endnoteBody: document.getElementById("endnoteBody"),
  btnJumpToEndnote: document.getElementById("btnJumpToEndnote"),
  btnCloseEndnote: document.getElementById("btnCloseEndnote"),
  btnAskGeminiEndnote: document.getElementById("btnAskGeminiEndnote"),

  selectGeminiScope: document.getElementById("selectGeminiScope"),
  btnSummarizeChapter: document.getElementById("btnSummarizeChapter"),
  btnArgumentMap: document.getElementById("btnArgumentMap"),
  btnClearChat: document.getElementById("btnClearChat"),
  chatSelectionBanner: document.getElementById("chatSelectionBanner"),
  chatSelectionQuote: document.getElementById("chatSelectionQuote"),
  btnClearAttachedSelection: document.getElementById("btnClearAttachedSelection"),
  chatMessages: document.getElementById("chatMessages"),
  chatInput: document.getElementById("chatInput"),
  chatModelBadge: document.getElementById("chatModelBadge"),
  btnSendChat: document.getElementById("btnSendChat"),

  annCountBadge: document.getElementById("annCountBadge"),
  annCategoryFilterBar: document.getElementById("annCategoryFilterBar"),
  annSearchInput: document.getElementById("annSearchInput"),
  selectAnnGroupBy: document.getElementById("selectAnnGroupBy"),
  annotationEditorCard: document.getElementById("annotationEditorCard"),
  editorTitle: document.getElementById("editorTitle"),
  editorQuote: document.getElementById("editorQuote"),
  editorNoteInput: document.getElementById("editorNoteInput"),
  editorPageInput: document.getElementById("editorPageInput"),
  btnCancelAnnotation: document.getElementById("btnCancelAnnotation"),
  btnSaveAnnotation: document.getElementById("btnSaveAnnotation"),
  annotationsList: document.getElementById("annotationsList"),

  btnGenStructuredReport: document.getElementById("btnGenStructuredReport"),
  btnGenGeminiReport: document.getElementById("btnGenGeminiReport"),
  btnCopyReport: document.getElementById("btnCopyReport"),
  reportContent: document.getElementById("reportContent"),

  btnOpenCompare: document.getElementById("btnOpenCompare"),
  compareModal: document.getElementById("compareModal"),
  btnCloseCompare: document.getElementById("btnCloseCompare"),
  compareBookA: document.getElementById("compareBookA"),
  compareBookB: document.getElementById("compareBookB"),
  compareTopicInput: document.getElementById("compareTopicInput"),
  btnRunCompare: document.getElementById("btnRunCompare"),
  compareResultBox: document.getElementById("compareResultBox"),

  btnOpenShortcuts: document.getElementById("btnOpenShortcuts"),
  shortcutsModal: document.getElementById("shortcutsModal"),
  btnCloseShortcuts: document.getElementById("btnCloseShortcuts"),

  btnOpenSettings: document.getElementById("btnOpenSettings"),
  geminiStatusDot: document.getElementById("geminiStatusDot"),
  geminiStatusText: document.getElementById("geminiStatusText"),
  settingsModal: document.getElementById("settingsModal"),
  btnCloseSettings: document.getElementById("btnCloseSettings"),
  inputGeminiKey: document.getElementById("inputGeminiKey"),
  selectGeminiModel: document.getElementById("selectGeminiModel"),
  inputGoogleClientId: document.getElementById("inputGoogleClientId"),
  inputGoogleClientSecret: document.getElementById("inputGoogleClientSecret"),
  btnConnectDriveOAuth: document.getElementById("btnConnectDriveOAuth"),
  driveOAuthDot: document.getElementById("driveOAuthDot"),
  driveOAuthStatusText: document.getElementById("driveOAuthStatusText"),
  inputFolioPasscode: document.getElementById("inputFolioPasscode"),
  cacheStatsText: document.getElementById("cacheStatsText"),
  btnPruneCache: document.getElementById("btnPruneCache"),
  hiddenBooksCount: document.getElementById("hiddenBooksCount"),
  hiddenBooksList: document.getElementById("hiddenBooksList"),
  btnSaveSettings: document.getElementById("btnSaveSettings"),

  authModal: document.getElementById("authModal"),
  inputAuthPasscode: document.getElementById("inputAuthPasscode"),
  authErrorMsg: document.getElementById("authErrorMsg"),
  btnUnlockFolio: document.getElementById("btnUnlockFolio"),

  cmdPaletteModal: document.getElementById("cmdPaletteModal"),
  cmdPaletteInput: document.getElementById("cmdPaletteInput"),
  cmdPaletteResults: document.getElementById("cmdPaletteResults"),

  toastContainer: document.getElementById("toastContainer"),
};

// --- Utilities ---

function showToast(msg, duration = 3500, action = null) {
  const t = document.createElement("div");
  t.className = "toast";
  const span = document.createElement("span");
  span.textContent = msg;
  t.appendChild(span);
  if (action && action.label && typeof action.onClick === "function") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-undo-btn";
    btn.textContent = action.label;
    btn.addEventListener("click", () => {
      t.remove();
      action.onClick();
    });
    t.appendChild(btn);
  }
  el.toastContainer.appendChild(t);
  setTimeout(() => {
    t.remove();
  }, duration);
}

function debounce(fn, wait = 150) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(md) {
  if (!md) return "";
  let html = escapeHtml(md);
  // Code blocks
  html = html.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
  // Inline code
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Bold & Italic
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  // Headings
  html = html.replace(/^### (.*)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.*)$/gm, "<h3>$1</h3>");
  html = html.replace(/^# (.*)$/gm, "<h3>$1</h3>");
  // Bullet lists
  html = html.replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
  html = html.replace(/<\/ul>\s*<ul>/g, "");
  // Paragraphs
  const parts = html.split(/\n{2,}/);
  return parts
    .map((p) => {
      const trimmed = p.trim();
      if (!trimmed) return "";
      if (trimmed.startsWith("<h") || trimmed.startsWith("<ul") || trimmed.startsWith("<pre")) {
        return trimmed;
      }
      return `<p>${trimmed.replace(/\n/g, "<br/>")}</p>`;
    })
    .join("");
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

// --- Theme & Typography ---

function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("folio_theme", theme);
  document.querySelectorAll(".theme-dot").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeVal === theme);
  });
  injectReaderStylesIntoIframe();
}

function updateTypography(deltaSize = 0, newFamily = null) {
  if (deltaSize !== 0) {
    state.fontSizePct = Math.max(75, Math.min(160, state.fontSizePct + deltaSize));
    localStorage.setItem("folio_font_size", String(state.fontSizePct));
  }
  if (newFamily) {
    state.fontFamily = newFamily;
    localStorage.setItem("folio_font_family", newFamily);
  }
  el.fontSizeLabel.textContent = `${state.fontSizePct}%`;
  el.selectFontFamily.value = state.fontFamily;
  injectReaderStylesIntoIframe();
}

// --- API Status & Settings ---

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    state.geminiConfigured = data.gemini_configured;
    state.geminiModel = data.gemini_model || "gemini-4-hydrogen";
    updateStatusUI(data.gemini_key_masked);
  } catch (e) {
    console.error("Status check error:", e);
  }
}

function updateStatusUI(maskedKey = "") {
  el.geminiStatusDot.classList.toggle("online", state.geminiConfigured);
  el.geminiStatusText.textContent = state.geminiConfigured
    ? `Gemini Ready (${state.geminiModel.replace("gemini-", "")})`
    : "Set Gemini Key";
  el.chatModelBadge.textContent = state.geminiModel;
  el.selectGeminiModel.value = state.geminiModel;
  if (maskedKey) {
    el.inputGeminiKey.placeholder = `Current key: ${maskedKey} (enter new to replace)`;
  }
}

async function saveSettings() {
  const keyVal = el.inputGeminiKey.value.trim();
  const modelVal = el.selectGeminiModel.value;
  const payload = { gemini_model: modelVal };
  if (keyVal) payload.gemini_api_key = keyVal;
  if (el.inputGoogleClientId) payload.google_client_id = el.inputGoogleClientId.value.trim();
  if (el.inputGoogleClientSecret && el.inputGoogleClientSecret.value.trim()) {
    payload.google_client_secret = el.inputGoogleClientSecret.value.trim();
  }
  if (el.inputFolioPasscode && el.inputFolioPasscode.value.trim()) {
    payload.folio_passcode = el.inputFolioPasscode.value.trim();
  }

  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  state.geminiConfigured = data.gemini_configured;
  state.geminiModel = data.gemini_model;
  el.inputGeminiKey.value = "";
  if (el.inputGoogleClientSecret) el.inputGoogleClientSecret.value = "";
  if (el.inputFolioPasscode) el.inputFolioPasscode.value = "";
  updateStatusUI(data.gemini_key_masked);
  if (el.driveOAuthStatusText) {
    el.driveOAuthStatusText.textContent = data.drive_oauth_connected
      ? "Connected via Cloud OAuth"
      : "Using local gdrive / Drive for Desktop";
  }
  el.settingsModal.classList.add("hidden");
  showToast("Settings saved!");
}

// --- Library View ---

function getBookFormat(book) {
  if (!book) return "epub";
  if (book.format) return book.format.toLowerCase();
  return (book.name || "").toLowerCase().endsWith(".pdf") ? "pdf" : "epub";
}

function isActiveBookPdf() {
  return Boolean(state.activeBook && getBookFormat(state.activeBook) === "pdf");
}

function getBooksForCurrentShelf() {
  if (state.libraryFormat === "all") return state.books;
  return state.books.filter((b) => getBookFormat(b) === state.libraryFormat);
}

function updateShelfCountPills() {
  const epubCnt = state.books.filter((b) => getBookFormat(b) === "epub").length;
  const pdfCnt = state.books.filter((b) => getBookFormat(b) === "pdf").length;
  if (el.epubCountPill) el.epubCountPill.textContent = String(epubCnt);
  if (el.pdfCountPill) el.pdfCountPill.textContent = String(pdfCnt);
  if (el.allCountPill) el.allCountPill.textContent = String(state.books.length);
  if (el.uncompiledCountBadge) el.uncompiledCountBadge.textContent = String(state.uncompiledFolders.length);
  if (el.btnOpenUncompiled) {
    const showUncompiled = state.libraryFormat !== "epub" && state.uncompiledFolders.length > 0;
    el.btnOpenUncompiled.classList.toggle("hidden", !showUncompiled);
  }
}

async function syncPdfsFromDrive() {
  el.libraryLoading.classList.remove("hidden");
  el.libraryLoadingText.textContent = "Scanning Cambridge Core & nerd stuff for PDFs (filtering chapter splits)...";
  try {
    const res = await fetch("/api/pdfs/sync", { method: "POST" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.books = data.books || state.books;
    state.uncompiledFolders = data.uncompiled_folders || [];
    state.pdfSyncedOnce = true;
    updateShelfCountPills();
    populateFolderDropdown();
    populateTagDropdown();
    renderLibraryGrid();
    const msg = `Synced ${data.pdf_count} PDFs (${data.skipped_chapter_pdfs} chapter PDFs filtered, ${data.uncompiled_count} uncompiled book folders).`;
    showToast(msg, 4500);
  } catch (e) {
    showToast(`PDF sync error: ${e.message}`);
  } finally {
    el.libraryLoading.classList.add("hidden");
  }
}

function setLibraryFormat(fmt, opts = {}) {
  const valid = ["epub", "pdf", "all"].includes(fmt) ? fmt : "epub";
  state.libraryFormat = valid;
  localStorage.setItem("folio_library_format", valid);
  state.folderFilter = "";
  state.pdfCategoryFilter = "";
  if (el.pdfCategorySelect) {
    el.pdfCategorySelect.value = "";
    el.pdfCategorySelect.classList.toggle("hidden", valid === "epub");
  }
  document.querySelectorAll(".shelf-tab").forEach((btn) => {
    const active = btn.dataset.format === valid;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (el.libraryHeroTitle && el.libraryHeroDesc) {
    if (valid === "pdf") {
      el.libraryHeroTitle.textContent = "Your Google Drive PDF Library";
      el.libraryHeroDesc.textContent =
        "Scoped to Cambridge Core & nerd stuff — full books, Cambridge Elements, and journal articles (chapter splits kept out of the shelf).";
    } else if (valid === "all") {
      el.libraryHeroTitle.textContent = "Your Full Google Drive Library";
      el.libraryHeroDesc.textContent = "All EPUBs and PDFs together — search, highlight, compare, and discuss with Gemini.";
    } else {
      el.libraryHeroTitle.textContent = "Your Google Drive EPUB Library";
      el.libraryHeroDesc.textContent =
        "Connected to Google Drive. Select any book to download, read, highlight, annotate, and discuss with Gemini.";
    }
  }
  updateShelfCountPills();
  populateFolderDropdown();
  populateTagDropdown();
  renderLibraryGrid();

  // First time opening PDFs shelf with 0 PDFs synced, trigger scoped PDF scan automatically
  const pdfCount = state.books.filter((b) => getBookFormat(b) === "pdf").length;
  if (!opts.skipAutoSync && valid === "pdf" && pdfCount === 0 && !state.pdfSyncedOnce) {
    syncPdfsFromDrive();
  }
}

async function fetchBooks(forceSync = false) {
  if (forceSync && state.libraryFormat === "pdf") {
    return syncPdfsFromDrive();
  }
  el.libraryLoading.classList.remove("hidden");
  el.libraryLoadingText.textContent = forceSync
    ? state.libraryFormat === "all"
      ? "Syncing EPUBs & scoped PDFs from Google Drive..."
      : "Syncing latest EPUB files from your Google Drive..."
    : "Loading your Google Drive library...";
  try {
    const url = forceSync
      ? `/api/books/sync?scope=${encodeURIComponent(state.libraryFormat === "all" ? "all" : "epub")}`
      : "/api/books";
    const method = forceSync ? "POST" : "GET";
    const res = await fetch(url, { method });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.books = data.books || [];
    state.uncompiledFolders = data.uncompiled_folders || [];
    updateShelfCountPills();
    populateFolderDropdown();
    populateTagDropdown();
    renderLibraryGrid();
    if (forceSync) {
      const shelfCount = getBooksForCurrentShelf().length;
      showToast(`Synced ${shelfCount} ${state.libraryFormat === "all" ? "items" : state.libraryFormat.toUpperCase() + "s"} from Google Drive.`);
    }
  } catch (e) {
    showToast(`Error loading library: ${e.message}`);
  } finally {
    el.libraryLoading.classList.add("hidden");
  }
}

function populateFolderDropdown() {
  if (!el.libraryFolderSelect) return;
  const folderCounts = {};
  for (const b of getBooksForCurrentShelf()) {
    const fp = (b.folder_path || "").trim();
    if (fp) {
      folderCounts[fp] = (folderCounts[fp] || 0) + 1;
    }
  }
  const sortedFolders = Object.keys(folderCounts).sort((a, b) => a.localeCompare(b));
  const currentVal = state.folderFilter;
  el.libraryFolderSelect.innerHTML =
    `<option value="">All Drive Folders (${state.books.length} books)</option>` +
    sortedFolders
      .map(
        (f) =>
          `<option value="${escapeHtml(f)}" ${f === currentVal ? "selected" : ""}>📁 ${escapeHtml(f)} (${folderCounts[f]})</option>`
      )
      .join("");
}

const PRESET_TAGS = [
  "Reading",
  "To Read",
  "Reference",
  "Finished",
  "Dissertation",
  "Syllabus",
  "Value & Capital",
  "Energy & Ecology",
  "Race & Empire",
  "Social Reproduction",
  "Critical Theory",
  "Tech & Logistics",
  "Strategy & History",
  "Fiction",
];

function populateTagDropdown() {
  if (!el.libraryTagSelect) return;
  const tagCounts = {};
  for (const b of state.books) {
    for (const t of b.tags || []) {
      tagCounts[t] = (tagCounts[t] || 0) + 1;
    }
  }
  const allTags = Array.from(new Set([...PRESET_TAGS, ...Object.keys(tagCounts)])).sort((a, b) =>
    a.localeCompare(b)
  );
  const current = state.tagFilter;
  el.libraryTagSelect.innerHTML =
    `<option value="">All Collections</option>` +
    allTags
      .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)} (${tagCounts[t] || 0})</option>`)
      .join("");
  el.libraryTagSelect.value = current;
}

async function updateBookTags(bookId, tags) {
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const book = state.books.find((b) => b.id === bookId);
    if (book) book.tags = data.tags || [];
    populateTagDropdown();
    renderLibraryGrid();
  } catch (e) {
    showToast(`Failed to update tags: ${e.message}`);
  }
}

async function runGlobalSearch() {
  const q = (el.globalSearchInput?.value || "").trim();
  if (!q) return;
  el.globalSearchResults.innerHTML = `<p class="muted-hint">Searching across cached books &amp; highlights...</p>`;
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const anns = data.annotation_matches || [];
    const books = data.book_matches || [];
    if (anns.length === 0 && books.length === 0) {
      el.globalSearchResults.innerHTML = `<p class="muted-hint">No matches found for "${escapeHtml(q)}".</p>`;
      return;
    }
    let html = "";
    if (anns.length > 0) {
      html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin:4px 0;">Saved Highlights &amp; Notes (${anns.length})</div>`;
      html += anns
        .map(
          (a) => `
          <div class="global-search-item" data-book-id="${escapeHtml(a.book_id)}" data-ch="${a.chapter_index}" data-phrase="${escapeHtml(a.selected_text.slice(0, 80))}">
            <div class="global-search-item-head">
              <span>🖍️ ${escapeHtml(a.book_title || a.book_name)}</span>
              <span>${escapeHtml(a.chapter_title || `Section ${a.chapter_index + 1}`)}</span>
            </div>
            <div class="global-search-snippet">"${escapeHtml(a.selected_text.slice(0, 180))}"${a.note ? `<br/><em>Note: ${escapeHtml(a.note.slice(0, 120))}</em>` : ""}</div>
          </div>`
        )
        .join("");
    }
    if (books.length > 0) {
      html += `<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin:8px 0 4px;">Cached Book Passages (${books.length})</div>`;
      html += books
        .map(
          (m) => `
          <div class="global-search-item" data-book-id="${escapeHtml(m.book_id)}" data-ch="${m.chapter_index}" data-phrase="${escapeHtml(q)}">
            <div class="global-search-item-head">
              <span>📖 ${escapeHtml(m.book_title)}</span>
              <span>${escapeHtml(m.chapter_title || `Section ${m.chapter_index + 1}`)}</span>
            </div>
            <div class="global-search-snippet">${escapeHtml(m.snippet)}</div>
          </div>`
        )
        .join("");
    }
    el.globalSearchResults.innerHTML = html;
    el.globalSearchResults.querySelectorAll(".global-search-item").forEach((item) => {
      item.addEventListener("click", async () => {
        const bid = item.dataset.bookId;
        const ch = parseInt(item.dataset.ch || "0", 10);
        const phrase = item.dataset.phrase || "";
        await openBook(bid, { chapterIndex: ch, highlightPhrase: phrase });
      });
    });
  } catch (e) {
    el.globalSearchResults.innerHTML = `<p class="muted-hint">Search failed: ${escapeHtml(e.message)}</p>`;
  }
}

function getBookProgressInfo(book) {
  const chapters = (book.manifest && book.manifest.chapters) || [];
  const rawIdx = book.last_chapter_index || 0;
  if (chapters.length === 0) {
    const label = book.last_chapter_title || `Sec. ${rawIdx + 1}`;
    const pct =
      typeof book.progress_percent === "number" && (book.chapter_count || 0) > 0
        ? book.progress_percent
        : Math.round((book.scroll_percent || 0) * 100);
    return { label, pct };
  }
  const idx = Math.max(0, Math.min(chapters.length - 1, rawIdx));
  const sectionTitle = chapters[idx]?.title || `Sec. ${idx + 1}`;
  const totalWords = chapters.reduce((acc, c) => acc + (c.word_count || 0), 0);
  const scrollPct = Math.min(1, Math.max(0, book.scroll_percent || 0));
  let pct = 0;
  if (totalWords > 0) {
    let wordsBefore = 0;
    for (let i = 0; i < idx; i++) wordsBefore += chapters[i].word_count || 0;
    const curWords = chapters[idx]?.word_count || 0;
    pct = Math.min(100, Math.round(((wordsBefore + curWords * scrollPct) / totalWords) * 100));
  } else {
    pct = Math.min(100, Math.round(((idx + scrollPct) / chapters.length) * 100));
  }
  return { label: sectionTitle, pct };
}

function renderContinueReading() {
  if (!el.continueReadingSection || !el.continueReadingRow) return;
  const hasFilter = Boolean(state.filterQuery.trim() || state.folderFilter || state.tagFilter || state.pdfCategoryFilter);
  const recent = getBooksForCurrentShelf().filter((b) => b.last_opened_at).slice(0, 8);
  if (hasFilter || recent.length === 0) {
    el.continueReadingSection.classList.add("hidden");
    return;
  }
  el.continueReadingSection.classList.remove("hidden");
  el.continueReadingRow.innerHTML = recent
    .map((b) => {
      const prog = getBookProgressInfo(b);
      const fmtLabel = getBookFormat(b).toUpperCase();
      return `
        <div class="continue-card" data-book-id="${escapeHtml(b.id)}">
          <div>
            <div class="continue-card-title" title="${escapeHtml(b.title || b.name)}">${escapeHtml(b.title || b.name)}</div>
            <div class="continue-card-sub">${escapeHtml(b.author || fmtLabel)}</div>
          </div>
          <div>
            <div class="continue-card-sub" title="${escapeHtml(prog.label)}" style="margin-bottom:4px;">${escapeHtml(prog.label)} · ${prog.pct}%</div>
            <div class="continue-progress-track"><div class="continue-progress-fill" style="width:${prog.pct}%"></div></div>
          </div>
        </div>`;
    })
    .join("");
  el.continueReadingRow.querySelectorAll(".continue-card").forEach((c) => {
    c.addEventListener("click", () => openBook(c.dataset.bookId));
  });
}

function hideTagPicker() {
  el.tagPickerPopover?.classList.add("hidden");
  state.tagPickerBookId = "";
}

function openTagPicker(bookId, anchorBtn) {
  if (!el.tagPickerPopover || !el.tagPickerChips) return;
  state.tagPickerBookId = bookId;
  const book = state.books.find((b) => b.id === bookId);
  const bookTags = new Set(book?.tags || []);
  const allTags = new Set(PRESET_TAGS);
  for (const b of state.books) {
    for (const t of b.tags || []) allTags.add(t);
  }
  el.tagPickerChips.innerHTML = Array.from(allTags)
    .sort((a, b) => a.localeCompare(b))
    .map(
      (t) => `<button type="button" class="tag-chip-btn ${bookTags.has(t) ? "active" : ""}" data-chip-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`
    )
    .join("");

  el.tagPickerChips.querySelectorAll(".tag-chip-btn").forEach((chip) => {
    chip.addEventListener("click", async (e) => {
      e.stopPropagation();
      const tag = chip.dataset.chipTag;
      const cur = book?.tags || [];
      const next = cur.includes(tag) ? cur.filter((x) => x !== tag) : [...cur, tag];
      await updateBookTags(bookId, next);
      openTagPicker(bookId, anchorBtn);
    });
  });

  const rect = anchorBtn.getBoundingClientRect();
  el.tagPickerPopover.style.left = `${Math.min(window.innerWidth - 255, Math.max(12, rect.left))}px`;
  el.tagPickerPopover.style.top = `${Math.min(window.innerHeight - 200, rect.bottom + 6)}px`;
  el.tagPickerPopover.classList.remove("hidden");
  if (el.tagPickerInput) {
    el.tagPickerInput.value = "";
    el.tagPickerInput.focus();
  }
}

function renderLibraryGrid() {
  renderContinueReading();
  const q = state.filterQuery.toLowerCase().trim();
  const qNorm = q.replace(/[-_]/g, " ");
  const folderFilter = state.folderFilter;
  const tagFilter = state.tagFilter;
  const pdfCatFilter = state.pdfCategoryFilter;

  const filtered = getBooksForCurrentShelf().filter((b) => {
    if (pdfCatFilter && getBookFormat(b) === "pdf" && (b.pdf_category || "book") !== pdfCatFilter) {
      return false;
    }
    if (folderFilter && (b.folder_path || "") !== folderFilter) {
      return false;
    }
    if (tagFilter && !(b.tags || []).includes(tagFilter)) {
      return false;
    }
    if (!q) return true;
    const titleStr = (b.title || "").toLowerCase();
    const nameStr = (b.name || "").toLowerCase();
    const nameNorm = nameStr.replace(/[-_]/g, " ");
    const authorStr = (b.author || "").toLowerCase();
    const folderStr = (b.folder_path || "").toLowerCase();
    const tagsStr = (b.tags || []).join(" ").toLowerCase();
    return (
      titleStr.includes(q) ||
      titleStr.includes(qNorm) ||
      nameStr.includes(q) ||
      nameNorm.includes(qNorm) ||
      authorStr.includes(q) ||
      folderStr.includes(q) ||
      tagsStr.includes(q)
    );
  });

  if (state.librarySort === "title") {
    filtered.sort((a, b) => (a.title || a.name).localeCompare(b.title || b.name));
  } else if (state.librarySort === "author") {
    filtered.sort((a, b) => (a.author || "zzz").localeCompare(b.author || "zzz"));
  } else if (state.librarySort === "annotations") {
    filtered.sort((a, b) => (b.annotation_count || 0) - (a.annotation_count || 0));
  }

  el.libraryGrid.classList.toggle("list-view", state.libraryViewMode === "list");

  if (filtered.length === 0) {
    const emptyMsg =
      state.libraryFormat === "pdf"
        ? `No matching PDFs found. Click <strong>Refresh from Drive</strong> to scan Cambridge Core &amp; nerd stuff.`
        : `No matching ${state.libraryFormat === "all" ? "books" : "EPUB books"} found.`;
    el.libraryGrid.innerHTML = `<p class="muted-hint">${emptyMsg}</p>`;
    return;
  }

  const visibleBooks = q || folderFilter || tagFilter || pdfCatFilter ? filtered : filtered.slice(0, state.displayLimit);

  el.libraryGrid.innerHTML =
    visibleBooks
      .map((book) => {
        const displayTitle = book.title || book.name;
        const folderTag = book.folder_path ? `📁 ${book.folder_path}` : "";
        const authorName = (book.author || "").trim();
        const isPdf = getBookFormat(book) === "pdf";
        const pdfCatLabel =
          book.pdf_category === "article"
            ? "PDF · Article"
            : book.pdf_category === "element"
            ? "PDF · Element"
            : "PDF · Book";
        const coverUrl =
          book.has_cover || (book.downloaded && book.cover_href)
            ? `/api/books/${encodeURIComponent(book.id)}/cover`
            : "";
        const annCount = book.annotation_count || 0;
        const tagsHtml = (book.tags || [])
          .map(
            (t) => `<span class="book-tag-pill">${escapeHtml(t)}<button type="button" class="btn-rm-tag" data-book-id="${escapeHtml(book.id)}" data-tag="${escapeHtml(t)}" title="Remove tag">×</button></span>`
          )
          .join("");

        const prog = book.last_opened_at ? getBookProgressInfo(book) : null;

        if (state.libraryViewMode === "list") {
          return `
          <article class="library-list-row" data-book-id="${escapeHtml(book.id)}">
            <div class="list-row-title" title="${escapeHtml(displayTitle)}">
              ${isPdf ? `<span class="badge badge-pdf-inline">${escapeHtml(pdfCatLabel)}</span> ` : ""}
              ${escapeHtml(displayTitle)}
            </div>
            <div class="list-row-author" title="${escapeHtml(authorName)}">${escapeHtml(authorName || "—")}</div>
            <div class="list-row-tags">
              ${tagsHtml}
              <button type="button" class="btn-add-tag" data-book-id="${escapeHtml(book.id)}">+ Tag</button>
            </div>
            <div class="list-row-author">${annCount > 0 ? `🖍️ ${annCount}` : (isPdf && book.page_count ? `${book.page_count} pp.` : "")}</div>
            <div class="list-row-author" style="display:flex;align-items:center;justify-content:flex-end;gap:8px;" title="${prog ? escapeHtml(prog.label) : ""}">
              <span>${prog ? `${escapeHtml(prog.label)} (${prog.pct}%)` : (book.downloaded ? "Cached" : "Drive")}</span>
              <button type="button" class="btn-remove-book btn-remove-book-inline" data-remove-book="${escapeHtml(book.id)}" title="Remove from library (stays hidden on Drive sync)">×</button>
            </div>
          </article>`;
        }

        return `
        <article class="book-card" data-book-id="${escapeHtml(book.id)}">
          <div class="book-cover-area">
            <button type="button" class="btn-remove-book" data-remove-book="${escapeHtml(book.id)}" title="Remove from library (stays hidden on Drive sync)">×</button>
            <div class="book-badge-row">
              ${isPdf ? `<span class="badge badge-pdf">${escapeHtml(pdfCatLabel)}</span>` : ""}
              ${book.downloaded ? `<span class="badge badge-downloaded">Cached</span>` : `<span class="badge">On Drive</span>`}
              ${annCount > 0 ? `<span class="badge">🖍️ ${annCount}</span>` : ""}
            </div>
            ${
              coverUrl
                ? `<img src="${coverUrl}" alt="${escapeHtml(displayTitle)}" loading="lazy" />`
                : `<div class="book-cover-placeholder ${isPdf ? "placeholder-pdf" : ""}">
                    <div class="placeholder-title">${escapeHtml(displayTitle)}</div>
                    <div class="placeholder-tag">${escapeHtml(authorName ? authorName.split(",")[0] : (isPdf ? pdfCatLabel : "EPUB"))}</div>
                  </div>`
            }
          </div>
          <div class="book-card-body">
            <div>
              <h3 class="book-card-title" title="${escapeHtml(displayTitle)}">${escapeHtml(displayTitle)}</h3>
              <div class="book-card-author" title="${escapeHtml(authorName || folderTag)}">
                ${authorName ? `<strong>${escapeHtml(authorName)}</strong>` : `<span style="opacity:0.75;">${escapeHtml(folderTag || (isPdf ? pdfCatLabel : "EPUB Book"))}</span>`}
              </div>
              <div class="book-tags-row">
                ${tagsHtml}
                <button type="button" class="btn-add-tag" data-book-id="${escapeHtml(book.id)}">+ Tag</button>
              </div>
            </div>
            <div class="book-card-meta">
              <span title="${escapeHtml(folderTag)}">${escapeHtml(folderTag ? folderTag.split(" / ").pop() : formatBytes(book.size))}${isPdf && book.page_count ? ` · ${book.page_count}p` : ""}</span>
              <span title="${prog ? escapeHtml(prog.label) : ""}" style="max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${prog ? `${escapeHtml(prog.label)} · ${prog.pct}%` : (isPdf ? "Open PDF →" : "Open Book →")}</span>
            </div>
          </div>
        </article>
      `;
      })
      .join("") +
    (!q && !folderFilter && !tagFilter && !pdfCatFilter && filtered.length > state.displayLimit
      ? `<div style="grid-column: 1 / -1; text-align: center; padding: 16px;">
          <button id="btnShowMoreBooks" class="btn btn-secondary">
            Show More Books (${state.displayLimit} of ${filtered.length} shown — or use Search / Folder Filter above)
          </button>
        </div>`
      : "");

  el.libraryGrid.querySelectorAll(".book-card, .library-list-row").forEach((card) => {
    card.addEventListener("click", () => {
      openBook(card.dataset.bookId);
    });
  });

  el.libraryGrid.querySelectorAll(".btn-add-tag").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openTagPicker(btn.dataset.bookId, btn);
    });
  });

  el.libraryGrid.querySelectorAll(".btn-rm-tag").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const bid = btn.dataset.bookId;
      const tag = btn.dataset.tag;
      const book = state.books.find((b) => b.id === bid);
      const nextTags = (book?.tags || []).filter((t) => t !== tag);
      updateBookTags(bid, nextTags);
    });
  });

  el.libraryGrid.querySelectorAll("[data-remove-book]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      removeBookFromLibrary(btn.dataset.removeBook);
    });
  });

  const btnMore = document.getElementById("btnShowMoreBooks");
  if (btnMore) {
    btnMore.addEventListener("click", () => {
      state.displayLimit += 200;
      renderLibraryGrid();
    });
  }
}

async function removeBookFromLibrary(bookId) {
  const book = state.books.find((b) => b.id === bookId);
  if (!book) return;
  const title = book.title || book.name || "Book";
  if ((book.annotation_count || 0) > 0) {
    const ok = window.confirm(
      `Remove "${title}" from your library?\n\nIt has ${book.annotation_count} saved highlight(s). They will be preserved if you ever restore it from Settings -> Removed / Hidden Books.`
    );
    if (!ok) return;
  }
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}`, { method: "DELETE" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.books = state.books.filter((b) => b.id !== bookId);
    populateFolderDropdown();
    populateTagDropdown();
    renderLibraryGrid();
    showToast(`Removed "${title}" from library`, 6000, {
      label: "Undo",
      onClick: () => restoreBookToLibrary(bookId),
    });
  } catch (e) {
    showToast(`Failed to remove book: ${e.message}`);
  }
}

async function restoreBookToLibrary(bookId) {
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}/restore`, { method: "POST" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.book && !state.books.some((b) => b.id === data.book.id)) {
      state.books.unshift(data.book);
    }
    populateFolderDropdown();
    populateTagDropdown();
    renderLibraryGrid();
    refreshHiddenBooksSettings();
    showToast(`Restored "${data.book?.title || "book"}" to library.`);
  } catch (e) {
    showToast(`Failed to restore book: ${e.message}`);
  }
}

async function refreshHiddenBooksSettings() {
  if (!el.hiddenBooksList) return;
  try {
    const res = await fetch("/api/books/hidden");
    const data = await res.json();
    const items = data.hidden_books || [];
    if (el.hiddenBooksCount) el.hiddenBooksCount.textContent = `(${items.length})`;
    if (items.length === 0) {
      el.hiddenBooksList.innerHTML = `<p class="muted-hint" style="margin:0;">No removed books.</p>`;
      return;
    }
    el.hiddenBooksList.innerHTML = items
      .map(
        (b) => `
        <div class="hidden-book-row">
          <div class="hidden-book-info">
            <div class="hidden-book-title" title="${escapeHtml(b.title || b.name)}">${escapeHtml(b.title || b.name)}</div>
            ${b.author ? `<div class="hidden-book-author">${escapeHtml(b.author)}</div>` : ""}
          </div>
          <button type="button" class="btn btn-xs btn-secondary" data-restore-book="${escapeHtml(b.id)}">Restore</button>
        </div>`
      )
      .join("");
    el.hiddenBooksList.querySelectorAll("[data-restore-book]").forEach((btn) => {
      btn.addEventListener("click", () => restoreBookToLibrary(btn.dataset.restoreBook));
    });
  } catch (e) {
    el.hiddenBooksList.innerHTML = `<p class="muted-hint" style="margin:0;">Unable to load hidden books.</p>`;
  }
}

// --- Opening & Rendering an EPUB Book ---

async function openBook(bookId, opts = {}) {
  el.libraryView.classList.add("hidden");
  el.readerView.classList.remove("hidden");
  if (window.innerWidth <= 900) {
    el.leftSidebar?.classList.add("collapsed");
    el.rightSidebar?.classList.add("collapsed");
  }
  el.btnBackToLibrary.classList.remove("hidden");
  el.activeBookHeader.classList.remove("hidden");
  el.readerToolbar.classList.remove("hidden");

  el.bookLoadingOverlay.classList.remove("hidden");
  el.bookLoadingMessage.textContent = "Downloading & unpacking EPUB from Google Drive...";

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}`);
    const book = await res.json();
    if (book.error) throw new Error(book.error);

    state.activeBook = book;
    state.endnoteDocCache = {};
    state.annotations = book.annotations || [];
    state.bookmarks = book.bookmarks || [];
    state.chats = book.chats || [];
    state.readingReportMarkdown = book.reading_report || "";
    state.currentChapterIndex =
      typeof opts.chapterIndex === "number" ? opts.chapterIndex : book.last_chapter_index || 0;
    state.pendingScrollPercent =
      typeof opts.scrollPercent === "number"
        ? opts.scrollPercent
        : typeof opts.chapterIndex === "number"
        ? 0
        : book.scroll_percent || 0;

    // Find latest interaction_id for stateful multi-turn Gemini conversation
    const lastAsst = [...state.chats].reverse().find((c) => c.role === "assistant" && c.interaction_id);
    state.lastInteractionId = lastAsst ? lastAsst.interaction_id : "";

    el.headerBookTitle.textContent = book.title || book.name;
    renderToc();
    renderBookmarksList();
    renderAnnotationsList();
    renderChatMessages();
    renderReadingReport();

    // Pick first substantial chapter if opening for the first time and chapter 0 is just a cover
    const chapters = (book.manifest && book.manifest.chapters) || [];
    const isPdf = getBookFormat(book) === "pdf";

    if (isPdf) {
      el.epubFrameContainer?.classList.add("hidden");
      el.pdfViewerContainer?.classList.remove("hidden");
      if (el.btnTypographyMenu) el.btnTypographyMenu.closest(".toolbar-dropdown-wrap")?.classList.add("hidden");

      const targetCh = chapters[state.currentChapterIndex] || chapters[0];
      const pageCount = book.page_count || book.manifest?.page_count || 1;
      let startPage = opts.pageNumber || (targetCh ? targetCh.start_page : 1) || 1;
      if (!opts.pageNumber && typeof opts.chapterIndex !== "number" && book.scroll_percent > 0 && pageCount > 1) {
        startPage = Math.max(1, Math.min(pageCount, Math.round(book.scroll_percent * (pageCount - 1)) + 1));
      }

      el.bookLoadingMessage.textContent = "Rendering PDF pages...";
      const _mapPageToChapterIdx = (pno) => {
        for (const ch of chapters) {
          if (pno >= (ch.start_page || 1) && pno <= (ch.end_page || pageCount)) {
            return ch.index;
          }
        }
        return 0;
      };

      const debouncedPdfProgress = debounce((pno, scrollPct) => {
        saveReadingProgress(scrollPct);
      }, 600);

      await window.FolioPdfViewer.open(
        bookId,
        { startPage },
        {
          getAnnotations: () => state.annotations,
          onPageChange: (pno, total) => {
            const chIdx = _mapPageToChapterIdx(pno);
            state.currentChapterIndex = chIdx;
            const curCh = chapters[chIdx];
            el.headerChapterTitle.textContent = curCh ? `${curCh.title} · p. ${pno}` : `Page ${pno}`;
            el.footerChapterProgress.textContent = `Page ${pno} of ${total}${curCh ? ` · ${curCh.title}` : ""}`;
            el.btnPrevChapter.disabled = chIdx <= 0;
            el.btnNextChapter.disabled = chIdx >= chapters.length - 1;
            const bar = document.getElementById("chapterProgressBar");
            if (bar) bar.style.width = `${Math.round((pno / Math.max(1, total)) * 100)}%`;
            renderToc();
          },
          onScrollSave: (pno, scrollPct) => {
            debouncedPdfProgress(pno, scrollPct);
          },
          onTextSelect: ({ text, pageNumber, pageNum }) => {
            const chIdx = _mapPageToChapterIdx(pageNum);
            state.currentChapterIndex = chIdx;
            state.selectedText = text;
            state.selectedPageNumber = pageNumber;
            el.selectionPopover?.classList.remove("hidden");
          },
        }
      );
    } else {
      window.FolioPdfViewer?.destroy();
      el.pdfViewerContainer?.classList.add("hidden");
      el.epubFrameContainer?.classList.remove("hidden");
      if (el.btnTypographyMenu) el.btnTypographyMenu.closest(".toolbar-dropdown-wrap")?.classList.remove("hidden");

      if (
        typeof opts.chapterIndex !== "number" &&
        !book.last_opened_at &&
        state.currentChapterIndex === 0 &&
        chapters.length > 1 &&
        chapters[0].word_count < 30
      ) {
        state.currentChapterIndex = 1;
      }

      await loadChapter(state.currentChapterIndex, opts.highlightPhrase || "");
    }
  } catch (e) {
    showToast(`Failed to open book: ${e.message}`);
    backToLibrary();
  } finally {
    el.bookLoadingOverlay.classList.add("hidden");
  }
}

function backToLibrary() {
  if (state.activeBook) {
    const idx = state.books.findIndex((b) => b.id === state.activeBook.id);
    if (idx !== -1) {
      const chapters = state.activeBook.manifest?.chapters || [];
      const ch = chapters[state.currentChapterIndex];
      const sp = isActiveBookPdf()
        ? (window.FolioPdfViewer?.getScrollPercent() || 0)
        : getIframeScrollPercent();
      const updated = {
        ...state.books[idx],
        downloaded: 1,
        has_cover: state.activeBook.has_cover || state.books[idx].has_cover,
        cover_href: state.activeBook.cover_href || state.books[idx].cover_href,
        page_count: state.activeBook.page_count || state.books[idx].page_count || 0,
        last_chapter_index: state.currentChapterIndex,
        scroll_percent: sp,
        last_chapter_title: ch?.title || state.books[idx].last_chapter_title || "",
        chapter_count: chapters.length || state.books[idx].chapter_count || 0,
        last_opened_at: new Date().toISOString(),
        annotation_count: state.annotations.length,
      };
      const prog = getBookProgressInfo({ ...updated, manifest: state.activeBook.manifest });
      updated.progress_percent = prog.pct;
      state.books[idx] = updated;
      // Move to front for "recent" ordering
      state.books.splice(idx, 1);
      state.books.unshift(updated);
    }
  }
  window.FolioPdfViewer?.destroy();
  el.pdfViewerContainer?.classList.add("hidden");
  el.epubFrameContainer?.classList.remove("hidden");
  if (el.btnTypographyMenu) el.btnTypographyMenu.closest(".toolbar-dropdown-wrap")?.classList.remove("hidden");
  state.activeBook = null;
  state.selectedText = "";
  state.endnoteDocCache = {};
  hideSelectionPopover();
  el.readerView.classList.add("hidden");
  el.btnBackToLibrary.classList.add("hidden");
  el.activeBookHeader.classList.add("hidden");
  el.readerToolbar.classList.add("hidden");
  el.libraryView.classList.remove("hidden");
  updateShelfCountPills();
  renderLibraryGrid();
}

function renderToc() {
  if (!state.activeBook || !state.activeBook.manifest) return;
  const chapters = state.activeBook.manifest.chapters || [];
  el.tocList.innerHTML = chapters
    .map((ch) => {
      const isActive = ch.index === state.currentChapterIndex;
      return `
        <button class="toc-item ${isActive ? "active" : ""}" data-ch-index="${ch.index}">
          <span>${escapeHtml(ch.title)}</span>
        </button>
      `;
    })
    .join("");

  el.tocList.querySelectorAll(".toc-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.chIndex, 10);
      loadChapter(idx);
    });
  });
}

async function loadChapter(chapterIndex, highlightSearchPhrase = "") {
  if (!state.activeBook || !state.activeBook.manifest) return;
  const chapters = state.activeBook.manifest.chapters || [];
  if (chapterIndex < 0 || chapterIndex >= chapters.length) return;

  state.currentChapterIndex = chapterIndex;
  hideSelectionPopover();
  hideEndnotePopover();

  const ch = chapters[chapterIndex];
  if (isActiveBookPdf()) {
    const startPg = ch.start_page || 1;
    window.FolioPdfViewer?.scrollToPage(startPg);
    renderToc();
    return;
  }
  el.headerChapterTitle.textContent = ch.title;
  el.footerChapterProgress.textContent = `Section ${chapterIndex + 1} of ${chapters.length} · ${ch.title}`;
  el.btnPrevChapter.disabled = chapterIndex <= 0;
  el.btnNextChapter.disabled = chapterIndex >= chapters.length - 1;
  if (document.activeElement && document.activeElement.disabled) {
    document.activeElement.blur();
  }

  renderToc();

  const chapterUrl = `/epub/${encodeURIComponent(state.activeBook.id)}/${ch.href}`;

  return new Promise((resolve) => {
    el.epubFrame.onload = () => {
      injectReaderStylesIntoIframe();
      decorateEpubPageMarkers();
      attachIframeListeners();
      applySavedHighlightsToIframe();
      if (highlightSearchPhrase) {
        state.pendingScrollPercent = null;
        scrollToPhraseInIframe(highlightSearchPhrase);
      } else if (state.pendingScrollPercent && state.pendingScrollPercent > 0) {
        const pct = state.pendingScrollPercent;
        state.pendingScrollPercent = null;
        setTimeout(() => scrollIframeToPercent(pct), 40);
      } else {
        state.pendingScrollPercent = null;
      }
      saveReadingProgress(getIframeScrollPercent());
      resolve();
    };
    el.epubFrame.src = chapterUrl;
  });
}

function injectReaderStylesIntoIframe() {
  const doc = el.epubFrame.contentDocument;
  if (!doc || !doc.head) return;

  let styleEl = doc.getElementById("folio-reader-injected-style");
  if (!styleEl) {
    styleEl = doc.createElement("style");
    styleEl.id = "folio-reader-injected-style";
    doc.head.appendChild(styleEl);
  }

  const themePalettes = {
    sepia: { bg: "#fbf7f0", fg: "#1e1b18", link: "#8c4a27", quoteBg: "#f2eadb" },
    light: { bg: "#ffffff", fg: "#0f172a", link: "#2563eb", quoteBg: "#f1f5f9" },
    dark:  { bg: "#17191d", fg: "#e5e7eb", link: "#d97757", quoteBg: "#212429" },
  };
  const pal = themePalettes[state.theme] || themePalettes.sepia;

  const fontMap = {
    serif: `Georgia, "Iowan Old Style", "Palatino Linotype", Palatino, serif`,
    sans: `-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif`,
    mono: `"SFMono-Regular", Consolas, Menlo, monospace`,
  };
  const fontStack = fontMap[state.fontFamily] || fontMap.serif;

  styleEl.textContent = `
    html, body {
      background-color: ${pal.bg} !important;
      color: ${pal.fg} !important;
      font-family: ${fontStack} !important;
      font-size: ${state.fontSizePct}% !important;
      line-height: ${state.lineHeight || "1.72"} !important;
      max-width: ${state.columnWidth || "68ch"} !important;
      text-align: ${state.textAlign || "left"} !important;
      margin: 0 auto !important;
      padding: 28px 32px 64px 32px !important;
      overflow-wrap: break-word !important;
    }
    body *:not(mark.folio-hl) {
      color: ${pal.fg} !important;
    }
    p, li, div, span, blockquote, dd, dt, i, em, b, strong, small {
      font-family: inherit !important;
      line-height: ${state.lineHeight || "1.72"} !important;
    }
    h1, h2, h3, h4, h5, h6 {
      color: ${pal.fg} !important;
      line-height: 1.3 !important;
      margin-top: 1.4em !important;
      margin-bottom: 0.6em !important;
    }
    a[href] {
      color: ${pal.link} !important;
      text-decoration: underline !important;
      text-underline-offset: 2px !important;
    }
    a:not([href]) {
      color: ${pal.fg} !important;
      text-decoration: none !important;
    }
    a[data-folio-page]::before {
      content: " [p. " attr(data-folio-page) "] ";
      font-size: 0.72em !important;
      font-family: -apple-system, BlinkMacSystemFont, Inter, sans-serif !important;
      font-weight: 600 !important;
      color: ${pal.link} !important;
      opacity: 0.68 !important;
      padding: 0 3px !important;
      vertical-align: baseline !important;
      user-select: none !important;
      -webkit-user-select: none !important;
    }
    img, svg {
      max-width: 100% !important;
      height: auto !important;
      border-radius: 4px;
    }
    blockquote {
      background: ${pal.quoteBg} !important;
      padding: 12px 16px !important;
      border-left: 3px solid ${pal.link} !important;
      border-radius: 4px !important;
    }
    mark.folio-hl {
      border-radius: 2px;
      padding: 1px 0;
      cursor: pointer;
      color: #111827 !important;
    }
    mark.folio-hl * {
      color: #111827 !important;
    }
    mark.folio-hl-yellow { background-color: #fef08a !important; border-bottom: 2px solid #ca8a04; }
    mark.folio-hl-green  { background-color: #bbf7d0 !important; border-bottom: 2px solid #16a34a; }
    mark.folio-hl-blue   { background-color: #bfdbfe !important; border-bottom: 2px solid #2563eb; }
    mark.folio-hl-red    { background-color: #fecaca !important; border-bottom: 2px solid #dc2626; }
    mark.folio-hl-grey   { background-color: #e5e7eb !important; border-bottom: 2px solid #4b5563; }
    mark.folio-hl-pink   { background-color: #fbcfe8 !important; }
  `;
}

function decorateEpubPageMarkers() {
  const doc = el.epubFrame.contentDocument;
  if (!doc || !doc.body) return;

  const seenPages = new Set();
  doc.querySelectorAll("[id], [name], [title]").forEach((node) => {
    const idVal = node.getAttribute("id") || node.getAttribute("name") || "";
    const epubType = node.getAttribute("epub:type") || node.getAttribute("role") || "";
    let pageNum = "";

    const m = idVal.match(/^(?:page[_-]?|pg[_-]?|p[_-]?)(\d+|[ivxlcdm]+)$/i);
    if (m) {
      pageNum = m[1];
    } else if (epubType.toLowerCase().includes("pagebreak")) {
      pageNum = node.getAttribute("title") || node.getAttribute("aria-label") || idVal;
    }

    if (pageNum && !seenPages.has(pageNum)) {
      seenPages.add(pageNum);
      node.setAttribute("data-folio-page", pageNum);
    }
  });
}

function detectPageNumberForRange(doc, range) {
  if (!doc || !range) return "";
  const markers = Array.from(doc.querySelectorAll("[data-folio-page]"));
  if (markers.length === 0) {
    // Fallback: compute standard 250-word academic location / section page
    const preRange = doc.createRange();
    preRange.selectNodeContents(doc.body);
    preRange.setEnd(range.startContainer, range.startOffset);
    const wordsBefore = preRange.toString().trim().split(/\s+/).filter(Boolean).length;
    const estPage = Math.floor(wordsBefore / 275) + 1;
    return `sec. p. ${estPage}`;
  }

  let startPage = "";
  let endPage = "";

  for (const m of markers) {
    const pg = m.getAttribute("data-folio-page");
    try {
      const cmp = range.comparePoint(m, 0);
      // cmp === -1: marker is before selection start
      // cmp ===  0: marker is inside the selection range
      // cmp ===  1: marker is after selection end
      if (cmp === -1) {
        startPage = pg;
        endPage = pg;
      } else if (cmp === 0) {
        if (!startPage) startPage = pg;
        endPage = pg;
      } else {
        break;
      }
    } catch (e) {
      // ignore
    }
  }

  if (!startPage && markers.length > 0) {
    startPage = markers[0].getAttribute("data-folio-page");
  }
  if (!endPage) endPage = startPage;

  if (startPage && endPage && startPage !== endPage) {
    return `pp. ${startPage}–${endPage}`;
  }
  if (startPage) {
    return `p. ${startPage}`;
  }
  return "";
}

function getIframeScrollPercent() {
  const win = el.epubFrame?.contentWindow;
  const doc = el.epubFrame?.contentDocument;
  if (!win || !doc || !doc.documentElement) return 0;
  const maxScroll = Math.max(0, doc.documentElement.scrollHeight - win.innerHeight);
  if (maxScroll <= 0) return 0;
  return Math.min(1, Math.max(0, win.scrollY / maxScroll));
}

function scrollIframeToPercent(pct) {
  const win = el.epubFrame?.contentWindow;
  const doc = el.epubFrame?.contentDocument;
  if (!win || !doc || !doc.documentElement) return;
  const maxScroll = Math.max(0, doc.documentElement.scrollHeight - win.innerHeight);
  win.scrollTo({ top: Math.round(maxScroll * Math.min(1, Math.max(0, pct))), behavior: "instant" });
}

function getVisibleExcerpt() {
  const doc = el.epubFrame?.contentDocument;
  const win = el.epubFrame?.contentWindow;
  if (!doc || !win) return "";
  const paras = Array.from(doc.querySelectorAll("p, h1, h2, h3, blockquote"));
  for (const p of paras) {
    const r = p.getBoundingClientRect();
    if (r.bottom > 0 && r.top < win.innerHeight && p.textContent.trim().length > 15) {
      return p.textContent.trim().replace(/\s+/g, " ").slice(0, 180);
    }
  }
  return "";
}

function renderBookmarksList() {
  if (!el.bookmarksList) return;
  const bms = state.bookmarks || [];
  if (el.bmCountBadge) el.bmCountBadge.textContent = String(bms.length);
  if (bms.length === 0) {
    el.bookmarksList.innerHTML = `<p class="muted-hint">Press <strong>b</strong> or click <strong>🔖 Bookmark</strong> to save your exact reading position.</p>`;
    return;
  }
  el.bookmarksList.innerHTML = bms
    .map(
      (bm) => `
      <div class="bookmark-card" data-bm-id="${escapeHtml(bm.id)}">
        <div class="bookmark-card-top">
          <span>🔖 ${escapeHtml(bm.label || `Section ${bm.chapter_index + 1}`)}</span>
          <button type="button" class="btn-close-xs btn-del-bm" data-bm-id="${escapeHtml(bm.id)}" title="Delete bookmark">×</button>
        </div>
        <div class="bookmark-card-meta">${escapeHtml(bm.chapter_title || `Section ${bm.chapter_index + 1}`)} · ${Math.round((bm.scroll_percent || 0) * 100)}%</div>
        ${bm.excerpt ? `<div class="bookmark-card-meta" style="font-style:italic; margin-top:4px;">"${escapeHtml(bm.excerpt.slice(0, 110))}..."</div>` : ""}
      </div>`
    )
    .join("");

  el.bookmarksList.querySelectorAll(".bookmark-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".btn-del-bm")) return;
      const bm = bms.find((x) => x.id === card.dataset.bmId);
      if (!bm) return;
      if (bm.chapter_index === state.currentChapterIndex) {
        scrollIframeToPercent(bm.scroll_percent || 0);
      } else {
        state.pendingScrollPercent = bm.scroll_percent || 0;
        loadChapter(bm.chapter_index);
      }
    });
  });

  el.bookmarksList.querySelectorAll(".btn-del-bm").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await deleteBookmark(btn.dataset.bmId);
    });
  });
}

async function addBookmark(customLabel = "") {
  if (!state.activeBook) return;
  const chapters = state.activeBook.manifest?.chapters || [];
  const ch = chapters[state.currentChapterIndex] || {};
  const scrollPct = getIframeScrollPercent();
  const excerpt = getVisibleExcerpt();
  const label = (customLabel || "").trim() || `${ch.title || `Section ${state.currentChapterIndex + 1}`} (${Math.round(scrollPct * 100)}%)`;

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/bookmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_index: state.currentChapterIndex,
        chapter_title: ch.title || "",
        scroll_percent: scrollPct,
        label,
        excerpt,
      }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.bookmarks.push(data.bookmark);
    renderBookmarksList();
    if (el.bookmarkLabelInput) el.bookmarkLabelInput.value = "";
    showToast(`🔖 Saved bookmark: ${label}`);
  } catch (e) {
    showToast(`Failed to save bookmark: ${e.message}`);
  }
}

async function deleteBookmark(bmId) {
  if (!state.activeBook) return;
  try {
    await fetch(
      `/api/books/${encodeURIComponent(state.activeBook.id)}/bookmarks/${encodeURIComponent(bmId)}`,
      { method: "DELETE" }
    );
    state.bookmarks = state.bookmarks.filter((b) => b.id !== bmId);
    renderBookmarksList();
  } catch (e) {
    showToast(`Failed to delete bookmark: ${e.message}`);
  }
}

function isEditableElement(elNode) {
  if (!elNode) return false;
  const tag = (elNode.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || Boolean(elNode.isContentEditable);
}

function handleReaderKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (el.cmdPaletteModal && el.cmdPaletteModal.classList.contains("hidden")) {
      openCommandPalette();
    } else {
      closeCommandPalette();
    }
    return;
  }

  if (e.key === "Escape") {
    if (el.cmdPaletteModal && !el.cmdPaletteModal.classList.contains("hidden")) {
      closeCommandPalette();
      return;
    }
    if (el.shortcutsModal && !el.shortcutsModal.classList.contains("hidden")) {
      el.shortcutsModal.classList.add("hidden");
      return;
    }
    if (el.compareModal && !el.compareModal.classList.contains("hidden")) {
      el.compareModal.classList.add("hidden");
      return;
    }
    if (el.settingsModal && !el.settingsModal.classList.contains("hidden")) {
      el.settingsModal.classList.add("hidden");
      return;
    }
    if (document.body?.classList?.contains("focus-mode")) {
      document.body.classList.remove("focus-mode");
      return;
    }
    el.typographyPopover?.classList.add("hidden");
    el.exportMenuPopover?.classList.add("hidden");
    hideTagPicker();
    hideHighlightMiniToolbar();
    hideEndnotePopover();
    hideSelectionPopover();
    return;
  }

  if (isEditableElement(e.target)) return;

  if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (el.shortcutsModal) {
      e.preventDefault();
      el.shortcutsModal.classList.toggle("hidden");
    }
    return;
  }

  if (!state.activeBook || el.readerView.classList.contains("hidden")) return;
  if (el.settingsModal && !el.settingsModal.classList.contains("hidden")) return;
  if (el.shortcutsModal && !el.shortcutsModal.classList.contains("hidden")) return;
  if (el.compareModal && !el.compareModal.classList.contains("hidden")) return;
  if (el.bookLoadingOverlay && !el.bookLoadingOverlay.classList.contains("hidden")) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  if (e.key === " " || e.code === "Space") {
    e.preventDefault();
    const win = el.epubFrame?.contentWindow;
    if (win) {
      const delta = Math.round(win.innerHeight * 0.85) * (e.shiftKey ? -1 : 1);
      win.scrollBy({ top: delta, behavior: "smooth" });
    }
    return;
  }

  if (e.shiftKey) return;

  if (e.key === "ArrowLeft") {
    if (e.repeat) return;
    e.preventDefault();
    if (state.currentChapterIndex > 0) {
      loadChapter(state.currentChapterIndex - 1);
    }
  } else if (e.key === "ArrowRight") {
    if (e.repeat) return;
    e.preventDefault();
    const chapters = state.activeBook?.manifest?.chapters || [];
    if (state.currentChapterIndex < chapters.length - 1) {
      loadChapter(state.currentChapterIndex + 1);
    }
  } else if (e.key === "j" || e.key === "k") {
    e.preventDefault();
    const win = el.epubFrame?.contentWindow;
    if (win) win.scrollBy({ top: e.key === "j" ? 220 : -220, behavior: "smooth" });
  } else if (e.key === "[") {
    e.preventDefault();
    el.leftSidebar?.classList.toggle("collapsed");
  } else if (e.key === "]") {
    e.preventDefault();
    el.rightSidebar?.classList.toggle("collapsed");
  } else if (e.key === "b" && !e.repeat) {
    e.preventDefault();
    addBookmark();
  } else if (e.key === "f" && !e.repeat) {
    e.preventDefault();
    document.body?.classList?.toggle("focus-mode");
  }
}

let _prefetchedChapterKey = "";
function prefetchNextChapter() {
  if (!state.activeBook) return;
  const chapters = state.activeBook.manifest?.chapters || [];
  const nextCh = chapters[state.currentChapterIndex + 1];
  if (!nextCh || !nextCh.href) return;
  const key = `${state.activeBook.id}::${nextCh.href}`;
  if (_prefetchedChapterKey === key) return;
  _prefetchedChapterKey = key;
  fetch(`/epub/${encodeURIComponent(state.activeBook.id)}/${nextCh.href}`).catch(() => {});
}

function updateChapterProgressFooter() {
  if (!state.activeBook || !el.footerChapterProgress) return;
  const chapters = state.activeBook.manifest?.chapters || [];
  const ch = chapters[state.currentChapterIndex];
  if (!ch) return;
  const pct = Math.round(getIframeScrollPercent() * 100);
  if (el.chapterProgressBar) {
    el.chapterProgressBar.style.width = `${pct}%`;
  }
  if (pct >= 65) {
    prefetchNextChapter();
  }
  const words = ch.word_count || 0;
  const minsLeft = words > 0 ? Math.max(1, Math.round(((words * (1 - pct / 100)) / 230))) : 0;
  const timeHint = minsLeft > 0 ? ` · ~${minsLeft} min left` : "";
  el.footerChapterProgress.textContent = `Section ${state.currentChapterIndex + 1} of ${chapters.length} · ${ch.title} (${pct}%${timeHint})`;
}

let scrollSaveTimer = null;
function attachIframeListeners() {
  const doc = el.epubFrame.contentDocument;
  const win = el.epubFrame.contentWindow;
  if (!doc || !win) return;

  doc.addEventListener("mouseup", () => {
    setTimeout(() => handleIframeSelection(win), 20);
  });

  doc.addEventListener("keydown", handleReaderKeydown);

  // Touch swipe left/right for tablet chapter navigation
  let touchStartX = 0;
  let touchStartY = 0;
  doc.addEventListener("touchstart", (e) => {
    if (e.touches.length === 1) {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    }
  }, { passive: true });
  doc.addEventListener("touchend", (e) => {
    if (e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    if (Math.abs(dx) > 75 && Math.abs(dx) > Math.abs(dy) * 1.6) {
      if (dx < 0) {
        const chapters = state.activeBook?.manifest?.chapters || [];
        if (state.currentChapterIndex < chapters.length - 1) loadChapter(state.currentChapterIndex + 1);
      } else if (state.currentChapterIndex > 0) {
        loadChapter(state.currentChapterIndex - 1);
      }
    }
  }, { passive: true });

  updateChapterProgressFooter();
  let scrollRafPending = false;
  win.addEventListener(
    "scroll",
    () => {
      if (!scrollRafPending) {
        scrollRafPending = true;
        win.requestAnimationFrame(() => {
          scrollRafPending = false;
          updateChapterProgressFooter();
          hideHighlightMiniToolbar();
        });
      }
      if (scrollSaveTimer) clearTimeout(scrollSaveTimer);
      scrollSaveTimer = setTimeout(() => {
        saveReadingProgress(getIframeScrollPercent());
      }, 450);
    },
    { passive: true }
  );

  // Intercept internal chapter links & endnotes/footnotes inside the EPUB
  doc.querySelectorAll("a[href]").forEach((a) => {
    a.addEventListener("click", async (ev) => {
      const rawHref = a.getAttribute("href") || "";
      if (rawHref.startsWith("http://") || rawHref.startsWith("https://") || rawHref.startsWith("mailto:")) {
        a.setAttribute("target", "_blank");
        return;
      }

      const chapters = state.activeBook?.manifest?.chapters || [];
      const currentHref = chapters[state.currentChapterIndex]?.href || "";
      const baseDir = currentHref.includes("/") ? currentHref.substring(0, currentHref.lastIndexOf("/") + 1) : "";
      const cleanTarget = rawHref.split("#")[0];
      const frag = rawHref.split("#")[1] || "";

      // Resolve relative href
      let resolvedHref = currentHref;
      if (cleanTarget) {
        const parts = (baseDir + cleanTarget).split("/");
        const norm = [];
        for (const p of parts) {
          if (p === "." || p === "") continue;
          if (p === "..") norm.pop();
          else norm.push(p);
        }
        resolvedHref = norm.join("/");
      }

      const targetCh = chapters.find((c) => c.href === resolvedHref || (cleanTarget && c.href.endsWith(cleanTarget)));

      // Determine if this link is an endnote / footnote / bibliographic reference
      const linkText = (a.textContent || "").trim();
      const epubType = (a.getAttribute("epub:type") || a.getAttribute("role") || "").toLowerCase();
      const cls = (a.className || "").toLowerCase();
      const isInsideSup = Boolean(a.closest("sup"));
      const isNoteHref = /note|fn|endnote|foot|bib|cit|ref/i.test(rawHref);
      const isNumericRef = /^\[?\d+[a-z]?\]?$|^\([0-9]+\)$|^[*†‡§]$/.test(linkText);

      const looksLikeEndnote =
        Boolean(frag) &&
        (isInsideSup ||
          epubType.includes("noteref") ||
          epubType.includes("biblioref") ||
          cls.includes("footnote") ||
          cls.includes("endnote") ||
          cls.includes("noteref") ||
          isNoteHref ||
          isNumericRef);

      if (looksLikeEndnote) {
        ev.preventDefault();
        ev.stopPropagation();
        const pageAtRef = detectPageNumberForElement(doc, a);
        const opened = await tryShowInlineEndnote({
          resolvedHref,
          frag,
          linkText: linkText || frag,
          targetChapterIndex: targetCh ? targetCh.index : state.currentChapterIndex,
          pageNumber: pageAtRef,
        });
        if (opened) return;
      }

      if (rawHref.startsWith("#")) {
        return; // Allow normal anchor jump within same chapter
      }

      if (targetCh) {
        ev.preventDefault();
        loadChapter(targetCh.index).then(() => {
          if (frag) {
            const targetEl = el.epubFrame.contentDocument?.getElementById(frag);
            if (targetEl) targetEl.scrollIntoView({ behavior: "smooth" });
          }
        });
      }
    });
  });
}

function detectPageNumberForElement(doc, elNode) {
  if (!doc || !elNode) return "";
  try {
    const r = doc.createRange();
    r.selectNode(elNode);
    return detectPageNumberForRange(doc, r);
  } catch (e) {
    return "";
  }
}

async function tryShowInlineEndnote({ resolvedHref, frag, linkText, targetChapterIndex, pageNumber }) {
  if (!state.activeBook || !frag) return false;

  try {
    const chapters = state.activeBook.manifest?.chapters || [];
    const currentHref = chapters[state.currentChapterIndex]?.href || "";
    let noteDoc = null;

    if (resolvedHref === currentHref) {
      noteDoc = el.epubFrame.contentDocument;
    } else {
      const cacheKey = `${state.activeBook.id}::${resolvedHref}`;
      if (state.endnoteDocCache[cacheKey]) {
        noteDoc = state.endnoteDocCache[cacheKey];
      } else {
        const res = await fetch(`/epub/${encodeURIComponent(state.activeBook.id)}/${resolvedHref}`);
        if (!res.ok) return false;
        const htmlText = await res.text();
        const parser = new DOMParser();
        noteDoc = parser.parseFromString(htmlText, "text/html");
        state.endnoteDocCache[cacheKey] = noteDoc;
      }
    }

    if (!noteDoc) return false;

    let targetEl =
      noteDoc.getElementById(frag) ||
      noteDoc.querySelector(`[name="${CSS.escape(frag)}"]`) ||
      noteDoc.querySelector(`a[id="${CSS.escape(frag)}"]`);

    if (!targetEl) return false;

    // If targetEl is an inline anchor inside a paragraph/li/div/aside, grab the enclosing note container
    const tag = (targetEl.tagName || "").toLowerCase();
    let container = targetEl;
    if (tag === "a" || tag === "span" || tag === "sup") {
      container =
        targetEl.closest("aside, li, p, div.Footnote, div.FootnoteContent, dd") ||
        targetEl.parentElement ||
        targetEl;
    }

    const clone = container.cloneNode(true);
    // Remove leading back-link number if it just repeats the footnote number
    const firstLink = clone.querySelector("a[href]");
    if (firstLink && firstLink.textContent.trim() === linkText.replace(/[\[\]()]/g, "").trim()) {
      firstLink.remove();
    }

    const cleanHtml = clone.innerHTML.trim();
    const plainText = (clone.textContent || "").replace(/\s+/g, " ").trim();
    if (!plainText) return false;

    const currentChTitle = chapters[state.currentChapterIndex]?.title || `Section ${state.currentChapterIndex + 1}`;
    state.activeEndnote = {
      number: linkText,
      html: cleanHtml,
      plainText: `[Note ${linkText}] ${plainText}`,
      resolvedHref,
      frag,
      targetChapterIndex,
      sourceChapterIndex: state.currentChapterIndex,
      sourceChapterTitle: currentChTitle,
      sourceChapterHref: currentHref,
      pageNumber: pageNumber ? `${pageNumber}, n. ${linkText.replace(/\D/g, "") || linkText}` : `n. ${linkText}`,
    };

    el.endnoteBadge.textContent = `Note ${linkText}`;
    el.endnoteChapterHint.textContent = `${currentChTitle}${pageNumber ? ` (${pageNumber})` : ""}`;
    el.endnoteBody.innerHTML = cleanHtml;

    // Make external links in the endnote open in a new tab
    el.endnoteBody.querySelectorAll("a[href]").forEach((lnk) => {
      const h = lnk.getAttribute("href") || "";
      if (h.startsWith("http://") || h.startsWith("https://")) {
        lnk.setAttribute("target", "_blank");
      } else {
        // Prevent accidental navigation away when clicking backlinks inside the note popup
        lnk.addEventListener("click", (e) => e.preventDefault());
      }
    });

    el.endnotePopover.classList.remove("hidden");
    return true;
  } catch (e) {
    console.error("Error loading inline endnote:", e);
    return false;
  }
}

function hideEndnotePopover() {
  if (el.endnotePopover) {
    el.endnotePopover.classList.add("hidden");
  }
}

function handleIframeSelection(win) {
  const sel = win.getSelection();
  const text = sel ? sel.toString().trim() : "";
  if (text && text.length >= 2 && sel.rangeCount > 0) {
    state.selectedText = text;
    state.selectedPageNumber = detectPageNumberForRange(win.document, sel.getRangeAt(0));
    el.selectionPopover.classList.remove("hidden");
  } else {
    hideSelectionPopover();
  }
}

function hideSelectionPopover() {
  el.selectionPopover.classList.add("hidden");
}

async function saveReadingProgress(scrollPct = 0) {
  if (!state.activeBook) return;
  try {
    await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_index: state.currentChapterIndex,
        scroll_percent: Number(scrollPct) || 0,
      }),
    });
  } catch (e) {
    // Non-blocking
  }
}

// --- Highlighting DOM Text in Iframe ---

function buildDocumentTextIndex(doc) {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.nodeValue && node.nodeValue.length > 0 && isTextNodeVisible(node)) {
      textNodes.push(node);
    }
  }

  const chars = [];
  const charMap = []; // index in normDoc -> { node, offset }
  let lastWasSpace = true;

  for (const tn of textNodes) {
    const val = tn.nodeValue;
    for (let i = 0; i < val.length; i++) {
      const ch = val[i];
      if (/\s/.test(ch)) {
        if (!lastWasSpace) {
          chars.push(" ");
          charMap.push({ node: tn, offset: i });
          lastWasSpace = true;
        }
      } else {
        chars.push(ch);
        charMap.push({ node: tn, offset: i });
        lastWasSpace = false;
      }
    }
  }

  const normDoc = chars.join("");
  return { normDoc, normDocLower: normDoc.toLowerCase(), charMap };
}

function findNthOccurrence(haystack, needle, occurrenceIndex = 0) {
  let idx = -1;
  let searchFrom = 0;
  for (let n = 0; n <= occurrenceIndex; n++) {
    const found = haystack.indexOf(needle, searchFrom);
    if (found === -1) return idx;
    idx = found;
    searchFrom = found + 1;
  }
  return idx;
}

function applySavedHighlightsToIframe() {
  const doc = el.epubFrame.contentDocument;
  if (!doc || !doc.body || !state.activeBook) return;

  // Remove existing highlight marks first to avoid duplicates
  doc.querySelectorAll("mark.folio-hl").forEach((m) => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });

  const chAnns = state.annotations.filter((a) => a.chapter_index === state.currentChapterIndex);
  if (chAnns.length === 0) return;

  // Build normalized document text index ONCE per chapter pass (avoids O(H * N) reflows)
  const docIndex = buildDocumentTextIndex(doc);
  const matches = [];
  let updatedAnyPage = false;

  for (const ann of chAnns) {
    const match = resolveHighlightMatch(doc, docIndex, ann.selected_text, ann.occurrence_index || 0);
    if (!match) continue;
    matches.push({ ann, match });
    if (!ann.page_number && match.detectedPage) {
      ann.page_number = match.detectedPage;
      updatedAnyPage = true;
      // Backfill page number in SQLite silently
      fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ann),
      }).catch(() => {});
    }
  }

  // Sort matches from end of document to start so DOM mutations never invalidate earlier text-node offsets
  matches.sort((a, b) => b.match.matchIdx - a.match.matchIdx);
  for (const { ann, match } of matches) {
    wrapHighlightSegments(doc, match.segments, ann.color || "yellow", ann.id);
  }

  if (updatedAnyPage) {
    renderAnnotationsList();
  }
}

function isTextNodeVisible(node) {
  const parent = node?.parentElement;
  if (!parent) return false;
  if (parent.closest("[hidden], script, style, noscript, template")) return false;
  if (parent.style && (parent.style.display === "none" || parent.style.visibility === "hidden")) {
    return false;
  }
  return true;
}

function resolveHighlightMatch(doc, docIndex, phrase, occurrenceIndex = 0) {
  if (!phrase || phrase.length < 2) return null;

  const normTarget = phrase.replace(/\s+/g, " ").trim();
  if (!normTarget) return null;

  let matchIdx = findNthOccurrence(docIndex.normDoc, normTarget, occurrenceIndex);
  if (matchIdx === -1) {
    matchIdx = findNthOccurrence(docIndex.normDocLower, normTarget.toLowerCase(), occurrenceIndex);
  }
  if (matchIdx === -1) return null;

  const endIdx = matchIdx + normTarget.length - 1;
  if (endIdx >= docIndex.charMap.length) return null;

  // Group matched range by individual textNode segments so we can wrap across <i>, <a>, <sup>, etc.
  const segments = [];
  let curNode = null;
  let curStart = 0;
  let curEnd = 0;

  for (let k = matchIdx; k <= endIdx; k++) {
    const entry = docIndex.charMap[k];
    if (entry.node !== curNode) {
      if (curNode) {
        segments.push({ node: curNode, start: curStart, end: curEnd });
      }
      curNode = entry.node;
      curStart = entry.offset;
      curEnd = entry.offset + 1;
    } else {
      curEnd = entry.offset + 1;
    }
  }
  if (curNode) {
    segments.push({ node: curNode, start: curStart, end: curEnd });
  }

  // Compute page number for the full span before mutating DOM
  let detectedPage = "";
  if (segments.length > 0) {
    try {
      const fullRange = doc.createRange();
      fullRange.setStart(segments[0].node, segments[0].start);
      fullRange.setEnd(segments[segments.length - 1].node, segments[segments.length - 1].end);
      detectedPage = detectPageNumberForRange(doc, fullRange);
    } catch (e) {
      // ignore
    }
  }

  return { matchIdx, endIdx, segments, detectedPage };
}

function wrapHighlightSegments(doc, segments, color, annId) {
  // Wrap segments in reverse order so earlier offsets in the same node aren't shifted
  for (let s = segments.length - 1; s >= 0; s--) {
    const seg = segments[s];
    try {
      if (!seg.node.isConnected || seg.end > seg.node.nodeValue.length) continue;
      const r = doc.createRange();
      r.setStart(seg.node, seg.start);
      r.setEnd(seg.node, seg.end);
      const mark = doc.createElement("mark");
      mark.className = `folio-hl folio-hl-${color}`;
      mark.dataset.annId = annId;
      mark.title = "Click to edit, recode, or ask Gemini";
      mark.addEventListener("click", (ev) => {
        ev.stopPropagation();
        showHighlightMiniToolbar(mark, annId);
      });
      r.surroundContents(mark);
    } catch (e) {
      // ignore
    }
  }
}

function scrollToPhraseInIframe(phrase) {
  const doc = el.epubFrame.contentDocument;
  if (!doc || !doc.body || !phrase) return;
  const snippet = phrase.trim().slice(0, 50).toLowerCase();
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, null);
  let node;
  while ((node = walker.nextNode())) {
    if ((node.nodeValue || "").toLowerCase().includes(snippet)) {
      if (node.parentElement) {
        node.parentElement.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
    }
  }
}

// --- Annotations & Sync to Drive ---

async function quickHighlightSelection(color) {
  if (!state.selectedText || !state.activeBook) return;
  const ch = state.activeBook.manifest.chapters[state.currentChapterIndex];
  const payload = {
    chapter_index: state.currentChapterIndex,
    chapter_href: ch.href,
    chapter_title: ch.title,
    page_number: state.selectedPageNumber || "",
    selected_text: state.selectedText,
    note: "",
    color: color,
  };
  await saveAnnotationToServer(payload);
  hideSelectionPopover();
  el.epubFrame.contentWindow?.getSelection()?.removeAllRanges();
}

function openAnnotationEditor(selectedText, existingAnn = null) {
  switchRightTab("annotations");
  el.annotationEditorCard.classList.remove("hidden");
  state.editingAnnotation = existingAnn;
  state.editorColor = existingAnn ? existingAnn.color : "yellow";
  el.editorTitle.textContent = existingAnn ? "Edit Highlight & Note" : "New Highlight & Note";
  el.editorQuote.textContent = `"${selectedText}"`;
  el.editorNoteInput.value = existingAnn ? existingAnn.note || "" : "";
  if (el.editorPageInput) {
    el.editorPageInput.value = existingAnn ? existingAnn.page_number || "" : state.selectedPageNumber || "";
  }

  document.querySelectorAll("[data-ed-color]").forEach((b) => {
    b.classList.toggle("active", b.dataset.edColor === state.editorColor);
  });
  el.editorNoteInput.focus();
}

async function saveAnnotationToServer(payload) {
  if (!state.activeBook) return null;
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const saved = data.annotation;
    const idx = state.annotations.findIndex((a) => a.id === saved.id);
    if (idx !== -1) state.annotations[idx] = saved;
    else state.annotations.push(saved);

    renderAnnotationsList();
    if (isActiveBookPdf()) {
      window.FolioPdfViewer?.refreshHighlights();
    } else {
      applySavedHighlightsToIframe();
    }
    showToast("Highlight saved locally.");
    return saved;
  } catch (e) {
    showToast(`Failed to save highlight: ${e.message}`);
    return null;
  }
}

function renderSingleAnnotationCard(ann) {
  const cKey = (ann.color || "yellow").toLowerCase();
  const catMeta = HIGHLIGHT_CATEGORIES[cKey] || {
    key: cKey,
    short: cKey.toUpperCase(),
    label: cKey.toUpperCase(),
  };
  const chTitle = ann.chapter_title || `Section ${ann.chapter_index + 1}`;
  const pageTag = ann.page_number ? ` · <strong>${escapeHtml(ann.page_number)}</strong>` : "";
  return `
    <div class="annotation-card" data-ann-card-id="${escapeHtml(ann.id)}">
      <div class="ann-header">
        <span class="ann-chapter-tag" data-jump-ch="${ann.chapter_index}" data-jump-page="${escapeHtml(ann.page_number || "")}" data-jump-text="${escapeHtml(ann.selected_text.slice(0, 60))}">
          ${escapeHtml(chTitle)}${pageTag}
        </span>
        <span class="ann-cat-badge">
          <span class="swatch-dot swatch-${escapeHtml(cKey)}"></span>
          ${escapeHtml(catMeta.short)}
        </span>
      </div>
      <div class="ann-quote color-${escapeHtml(cKey)}">
        "${escapeHtml(ann.selected_text)}"
      </div>
      ${ann.note ? `<div class="ann-note">${escapeHtml(ann.note)}</div>` : ""}
      ${
        ann.gemini_response
          ? `<div class="ann-gemini"><strong>✨ Gemini:</strong> ${renderMarkdown(ann.gemini_response)}</div>`
          : ""
      }
      <div class="ann-actions">
        <button class="btn btn-xs btn-ghost" data-edit-ann="${escapeHtml(ann.id)}">Edit / Recode</button>
        <button class="btn btn-xs btn-ghost" data-cite-ann="${escapeHtml(ann.id)}" title="Copy quote &amp; academic citation">Cite</button>
        <button class="btn btn-xs btn-ghost" data-ask-ann="${escapeHtml(ann.id)}">Ask Gemini</button>
        <button class="btn btn-xs btn-ghost" data-del-ann="${escapeHtml(ann.id)}">Delete</button>
      </div>
    </div>
  `;
}

function updateCategoryCounts() {
  const counts = { all: state.annotations.length, blue: 0, yellow: 0, red: 0, green: 0, grey: 0 };
  for (const a of state.annotations) {
    const c = (a.color || "yellow").toLowerCase();
    if (counts[c] !== undefined) counts[c]++;
  }
  const setTxt = (id, val) => {
    const node = document.getElementById(id);
    if (node) node.textContent = String(val);
  };
  setTxt("countAll", counts.all);
  setTxt("countBlue", counts.blue);
  setTxt("countYellow", counts.yellow);
  setTxt("countRed", counts.red);
  setTxt("countGreen", counts.green);
  setTxt("countGrey", counts.grey);
}

function renderAnnotationsList() {
  el.annCountBadge.textContent = String(state.annotations.length);
  updateCategoryCounts();

  if (state.annotations.length === 0) {
    el.annotationsList.innerHTML = `
      <p class="muted-hint">
        No highlights yet. Select any passage in the book to code it:<br/><br/>
        <strong>Blue:</strong> Critical Points · <strong>Yellow:</strong> Significant Points · <strong>Red:</strong> Critiques · <strong>Green:</strong> Definitions · <strong>Grey:</strong> Concerns
      </p>
    `;
    return;
  }

  const filterColor = state.annFilterColor || "all";
  const q = (state.annSearchQuery || "").toLowerCase().trim();

  const filtered = state.annotations.filter((a) => {
    const c = (a.color || "yellow").toLowerCase();
    if (filterColor !== "all" && c !== filterColor) return false;
    if (!q) return true;
    return (
      (a.selected_text || "").toLowerCase().includes(q) ||
      (a.note || "").toLowerCase().includes(q) ||
      (a.chapter_title || "").toLowerCase().includes(q) ||
      (a.gemini_response || "").toLowerCase().includes(q)
    );
  });

  if (filtered.length === 0) {
    el.annotationsList.innerHTML = `<p class="muted-hint">No highlights match this category or search filter.</p>`;
    return;
  }

  if (state.annGroupBy === "category" && filterColor === "all") {
    const order = ["blue", "yellow", "red", "green", "grey"];
    const blocks = [];
    for (const cKey of order) {
      const catMeta = HIGHLIGHT_CATEGORIES[cKey];
      const items = filtered.filter((a) => (a.color || "yellow").toLowerCase() === cKey);
      if (items.length === 0) continue;
      blocks.push(`
        <div class="ann-category-group-header" style="border-left-color: ${catMeta.borderColor};">
          <div>
            <span>${escapeHtml(catMeta.label)} (${items.length})</span><br/>
            <small>${escapeHtml(catMeta.desc)}</small>
          </div>
        </div>
        ${items.map(renderSingleAnnotationCard).join("")}
      `);
    }
    // Include any legacy/other colors if present
    const otherItems = filtered.filter((a) => !order.includes((a.color || "yellow").toLowerCase()));
    if (otherItems.length > 0) {
      blocks.push(otherItems.map(renderSingleAnnotationCard).join(""));
    }
    el.annotationsList.innerHTML = blocks.join("");
  } else {
    el.annotationsList.innerHTML = filtered.map(renderSingleAnnotationCard).join("");
  }

  // Event listeners
  el.annotationsList.querySelectorAll("[data-jump-ch]").forEach((tag) => {
    tag.addEventListener("click", () => {
      const chIdx = parseInt(tag.dataset.jumpCh, 10);
      const phrase = tag.dataset.jumpText;
      if (isActiveBookPdf()) {
        const pgMatch = (tag.dataset.jumpPage || "").match(/(\d+)/);
        if (pgMatch) {
          window.FolioPdfViewer?.scrollToPage(parseInt(pgMatch[1], 10));
          return;
        }
        loadChapter(chIdx, phrase);
        return;
      }
      if (chIdx !== state.currentChapterIndex) {
        loadChapter(chIdx, phrase);
      } else {
        scrollToPhraseInIframe(phrase);
      }
    });
  });

  el.annotationsList.querySelectorAll("[data-edit-ann]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const ann = state.annotations.find((a) => a.id === btn.dataset.editAnn);
      if (ann) openAnnotationEditor(ann.selected_text, ann);
    });
  });

  el.annotationsList.querySelectorAll("[data-cite-ann]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ann = state.annotations.find((a) => a.id === btn.dataset.citeAnn);
      if (!ann || !state.activeBook) return;
      const author = (state.activeBook.author || "").trim();
      const title = (state.activeBook.title || state.activeBook.name || "").trim();
      const loc = ann.page_number || ann.chapter_title || `Section ${(ann.chapter_index || 0) + 1}`;
      const citation = `"${(ann.selected_text || "").trim()}" (${author ? `${author}, ` : ""}${title}, ${loc})`;
      try {
        await navigator.clipboard.writeText(citation);
        showToast("Copied quote & citation to clipboard!");
      } catch (e) {
        showToast("Could not copy citation.");
      }
    });
  });

  el.annotationsList.querySelectorAll("[data-ask-ann]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const ann = state.annotations.find((a) => a.id === btn.dataset.askAnn);
      if (ann) {
        attachSelectionToChat(ann.selected_text);
        switchRightTab("gemini");
        el.chatInput.focus();
      }
    });
  });

  el.annotationsList.querySelectorAll("[data-del-ann]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const annId = btn.dataset.delAnn;
      await fetch(
        `/api/books/${encodeURIComponent(state.activeBook.id)}/annotations/${encodeURIComponent(annId)}`,
        { method: "DELETE" }
      );
      state.annotations = state.annotations.filter((a) => a.id !== annId);
      renderAnnotationsList();
      applySavedHighlightsToIframe();
      showToast("Annotation deleted.");
    });
  });
}

function renderReadingReport() {
  if (!el.reportContent) return;
  if (!state.readingReportMarkdown) {
    el.reportContent.innerHTML = `
      <div class="chat-empty-state">
        <div class="sparkle-badge">📋</div>
        <h3>Generate a Reading Report</h3>
        <p>Click <strong>⚡ Compile by Category</strong> for an instant structured dossier organized by your 5 highlight colors (Critical Points, Significant Points, Critiques, Definitions, Concerns), or <strong>✨ AI Synthesis Report</strong> to have Gemini synthesize an analytical report from your notes.</p>
      </div>
    `;
    return;
  }
  el.reportContent.innerHTML = `<div class="report-rendered">${renderMarkdown(state.readingReportMarkdown)}</div>`;
}

async function generateReadingReport(mode = "structured") {
  if (!state.activeBook) return;
  switchRightTab("report");
  el.reportContent.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;padding:24px;">
      <div class="spinner"></div>
      <span>${
        mode === "gemini"
          ? "Gemini is synthesizing your color-coded highlights into a Reading Report..."
          : "Compiling color-coded Reading Report..."
      }</span>
    </div>
  `;
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, model: state.geminiModel }),
    });
    const data = await res.json();
    if (data.code === "MISSING_API_KEY") {
      el.settingsModal.classList.remove("hidden");
      el.inputGeminiKey.focus();
      renderReadingReport();
      showToast("Enter your Gemini API Key for AI synthesis, or use '⚡ Compile by Category'.");
      return;
    }
    if (data.error) throw new Error(data.error);
    state.readingReportMarkdown = data.report_markdown;
    renderReadingReport();
    showToast(mode === "gemini" ? "AI Reading Report generated!" : "Structured Reading Report compiled!");
  } catch (e) {
    renderReadingReport();
    showToast(`Report error: ${e.message}`);
  }
}

async function syncAnnotationsToGoogleDrive() {
  if (!state.activeBook) return;
  const origLabel = el.syncDriveLabel.textContent;
  el.syncDriveLabel.textContent = "Syncing to Drive...";
  el.btnSyncDrive.disabled = true;

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/sync-drive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    showToast(`Synced ${data.count} highlights to Google Drive (${data.json_filename} & ${data.md_filename})!`);
  } catch (e) {
    showToast(`Google Drive sync failed: ${e.message}`);
  } finally {
    el.syncDriveLabel.textContent = origLabel;
    el.btnSyncDrive.disabled = false;
  }
}

// --- Gemini Companion Chat & Actions ---

function attachSelectionToChat(text) {
  state.attachedPassage = text;
  if (text) {
    el.chatSelectionBanner.classList.remove("hidden");
    el.chatSelectionQuote.textContent = `"${text}"`;
  } else {
    el.chatSelectionBanner.classList.add("hidden");
    el.chatSelectionQuote.textContent = "";
  }
}

function renderChatMessages() {
  if (!state.chats || state.chats.length === 0) {
    el.chatMessages.innerHTML = `
      <div class="chat-empty-state">
        <div class="sparkle-badge">✨</div>
        <h3>Ask Gemini about your reading</h3>
        <p>Select any passage in the text to explain, analyze, or annotate — or ask a question below about the current chapter or entire book.</p>
      </div>
    `;
    return;
  }

  el.chatMessages.innerHTML = state.chats
    .map((msg) => {
      if (msg.role === "user") {
        return `
          <div class="chat-bubble chat-bubble-user">
            ${msg.selected_text ? `<div class="bubble-quote">"${escapeHtml(msg.selected_text)}"</div>` : ""}
            <div>${escapeHtml(msg.content)}</div>
          </div>
        `;
      } else {
        return `
          <div class="chat-bubble chat-bubble-assistant">
            <div class="assistant-content">${renderMarkdown(msg.content)}</div>
            <div class="bubble-footer">
              <span>${escapeHtml(msg.model || "Gemini 4")}</span>
              <button class="btn btn-xs btn-secondary" data-save-as-note="${escapeHtml(msg.id)}">
                📌 Save as Annotation Note
              </button>
            </div>
          </div>
        `;
      }
    })
    .join("");

  el.chatMessages.querySelectorAll("[data-save-as-note]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const msg = state.chats.find((c) => c.id === btn.dataset.saveAsNote);
      if (!msg || !state.activeBook) return;
      const ch = state.activeBook.manifest.chapters[msg.chapter_index ?? state.currentChapterIndex] || {
        href: "",
        title: `Chapter ${state.currentChapterIndex + 1}`,
      };
      const passage = msg.selected_text || `[Chapter Note: ${ch.title}]`;
      await saveAnnotationToServer({
        chapter_index: msg.chapter_index ?? state.currentChapterIndex,
        chapter_href: ch.href,
        chapter_title: ch.title,
        selected_text: passage,
        note: "",
        gemini_response: msg.content,
        color: "blue",
      });
      switchRightTab("annotations");
    });
  });

  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

async function askGemini({ question = "", selectedText = "", action = "ask" }) {
  if (!state.activeBook) return;
  switchRightTab("gemini");

  const scope = el.selectGeminiScope.value;
  const passage = selectedText || state.attachedPassage || "";

  // Optimistic user + streaming assistant bubble
  const emptyState = el.chatMessages.querySelector(".chat-empty-state");
  if (emptyState) emptyState.remove();

  const tempEl = document.createElement("div");
  tempEl.className = "chat-bubble chat-bubble-assistant";
  tempEl.innerHTML = `<div class="assistant-content" style="display:flex;align-items:center;gap:8px;"><div class="spinner"></div><span>Gemini is reading &amp; thinking...</span></div>`;
  el.chatMessages.appendChild(tempEl);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;

  el.btnSendChat.disabled = true;

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/gemini/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        selected_text: passage,
        action,
        chapter_index: state.currentChapterIndex,
        scope,
        previous_interaction_id: state.lastInteractionId,
        model: state.geminiModel,
      }),
    });

    if (!res.ok || !res.body) {
      const errData = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      tempEl.remove();
      if (errData.code === "MISSING_API_KEY") {
        el.settingsModal.classList.remove("hidden");
        el.inputGeminiKey.focus();
        showToast("Please enter your Gemini API Key to ask Gemini questions.");
        return;
      }
      throw new Error(errData.error || `Gemini error (${res.status})`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let streamedText = "";
    let finalDone = null;
    const contentDiv = tempEl.querySelector(".assistant-content");

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const block of parts) {
        for (const line of block.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;
          let ev;
          try {
            ev = JSON.parse(jsonStr);
          } catch {
            continue;
          }
          if (ev.type === "error") {
            tempEl.remove();
            if (ev.code === "MISSING_API_KEY") {
              el.settingsModal.classList.remove("hidden");
              el.inputGeminiKey.focus();
              showToast("Please enter your Gemini API Key to ask Gemini questions.");
              return;
            }
            throw new Error(ev.error || "Gemini streaming error");
          } else if (ev.type === "delta" && ev.text) {
            streamedText += ev.text;
            if (contentDiv) {
              contentDiv.style.display = "block";
              contentDiv.innerHTML = renderMarkdown(streamedText);
            }
            el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
          } else if (ev.type === "done") {
            finalDone = ev;
          }
        }
      }
    }

    tempEl.remove();
    if (finalDone) {
      state.lastInteractionId = finalDone.interaction_id || state.lastInteractionId;
      state.chats.push(finalDone.user_message, finalDone.assistant_message);
      attachSelectionToChat("");
      renderChatMessages();
    }
  } catch (e) {
    tempEl.remove();
    showToast(`Gemini error: ${e.message}`);
  } finally {
    el.btnSendChat.disabled = false;
  }
}

// --- Full-Book Search ---

async function runBookSearch() {
  const q = el.bookSearchInput.value.trim();
  if (!q || !state.activeBook) return;

  el.bookSearchResults.innerHTML = `<div class="loading-banner"><div class="spinner"></div><span>Searching chapters...</span></div>`;
  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(state.activeBook.id)}/search?q=${encodeURIComponent(q)}`
    );
    const data = await res.json();
    const hits = data.results || [];
    if (hits.length === 0) {
      el.bookSearchResults.innerHTML = `<p class="muted-hint">No matches found for "${escapeHtml(q)}".</p>`;
      return;
    }
    el.bookSearchResults.innerHTML = hits
      .map(
        (h) => `
        <div class="search-hit-card" data-hit-ch="${h.chapter_index}" data-hit-q="${escapeHtml(q)}">
          <div class="search-hit-chapter">${escapeHtml(h.chapter_title)}</div>
          <div class="search-hit-snippet">${escapeHtml(h.snippet)}</div>
        </div>
      `
      )
      .join("");

    el.bookSearchResults.querySelectorAll(".search-hit-card").forEach((card) => {
      card.addEventListener("click", () => {
        const chIdx = parseInt(card.dataset.hitCh, 10);
        loadChapter(chIdx, card.dataset.hitQ);
      });
    });
  } catch (e) {
    el.bookSearchResults.innerHTML = `<p class="muted-hint">Search failed: ${escapeHtml(e.message)}</p>`;
  }
}

// --- Sidebar Tab Switching ---

function switchLeftTab(tabName) {
  document.querySelectorAll("[data-left-tab]").forEach((b) => {
    b.classList.toggle("active", b.dataset.leftTab === tabName);
  });
  document.getElementById("leftTabToc").classList.toggle("active", tabName === "toc");
  document.getElementById("leftTabSearch").classList.toggle("active", tabName === "search");
}

function switchRightTab(tabName) {
  el.rightSidebar.classList.remove("collapsed");
  document.querySelectorAll("[data-right-tab]").forEach((b) => {
    b.classList.toggle("active", b.dataset.rightTab === tabName);
  });
  document.getElementById("rightTabGemini").classList.toggle("active", tabName === "gemini");
  document.getElementById("rightTabAnnotations").classList.toggle("active", tabName === "annotations");
  const repTab = document.getElementById("rightTabReport");
  if (repTab) repTab.classList.toggle("active", tabName === "report");
}

// --- Event Wiring ---

function initEventListeners() {
  // Navigation & Library
  el.btnBackToLibrary.addEventListener("click", backToLibrary);
  el.brandLogo.addEventListener("click", () => {
    if (state.activeBook) backToLibrary();
  });
  el.btnRefreshLibrary.addEventListener("click", () => fetchBooks(true));
  const debouncedRenderLibrary = debounce(() => renderLibraryGrid(), 150);
  el.libraryFilterInput.addEventListener("input", (e) => {
    state.filterQuery = e.target.value;
    debouncedRenderLibrary();
  });
  if (el.libraryFolderSelect) {
    el.libraryFolderSelect.addEventListener("change", (e) => {
      state.folderFilter = e.target.value;
      renderLibraryGrid();
    });
  }

  // Sidebars
  el.btnToggleLeftSidebar.addEventListener("click", () => {
    el.leftSidebar.classList.toggle("collapsed");
  });
  el.btnToggleRightSidebar.addEventListener("click", () => {
    el.rightSidebar.classList.toggle("collapsed");
  });

  document.querySelectorAll("[data-left-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchLeftTab(btn.dataset.leftTab));
  });
  document.querySelectorAll("[data-right-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchRightTab(btn.dataset.rightTab));
  });

  // Theme & Typography
  document.querySelectorAll(".theme-dot").forEach((dot) => {
    dot.addEventListener("click", () => applyTheme(dot.dataset.themeVal));
  });
  el.btnFontDec.addEventListener("click", () => updateTypography(-10));
  el.btnFontInc.addEventListener("click", () => updateTypography(10));
  el.selectFontFamily.addEventListener("change", (e) => updateTypography(0, e.target.value));

  // Chapter Paging
  el.btnPrevChapter.addEventListener("click", () => {
    if (state.currentChapterIndex > 0) loadChapter(state.currentChapterIndex - 1);
  });
  el.btnNextChapter.addEventListener("click", () => {
    const chapters = state.activeBook?.manifest?.chapters || [];
    if (state.currentChapterIndex < chapters.length - 1) loadChapter(state.currentChapterIndex + 1);
  });

  // Selection Popover Actions
  el.selectionPopover.querySelectorAll("[data-color]").forEach((sw) => {
    sw.addEventListener("click", () => quickHighlightSelection(sw.dataset.color));
  });

  el.btnPopAnnotate.addEventListener("click", () => {
    if (!state.selectedText) return;
    openAnnotationEditor(state.selectedText, null);
    hideSelectionPopover();
  });

  el.btnPopExplain.addEventListener("click", () => {
    if (!state.selectedText) return;
    const text = state.selectedText;
    hideSelectionPopover();
    askGemini({ selectedText: text, action: "explain" });
  });

  el.btnPopCritique.addEventListener("click", () => {
    if (!state.selectedText) return;
    const text = state.selectedText;
    hideSelectionPopover();
    askGemini({ selectedText: text, action: "critique" });
  });

  el.btnPopAskCustom.addEventListener("click", () => {
    if (!state.selectedText) return;
    attachSelectionToChat(state.selectedText);
    hideSelectionPopover();
    switchRightTab("gemini");
    el.chatInput.focus();
  });

  // Inline Endnote Popover Actions
  if (el.btnCloseEndnote) {
    el.btnCloseEndnote.addEventListener("click", hideEndnotePopover);
  }
  if (el.btnJumpToEndnote) {
    el.btnJumpToEndnote.addEventListener("click", () => {
      const en = state.activeEndnote;
      if (!en) return;
      hideEndnotePopover();
      loadChapter(en.targetChapterIndex).then(() => {
        if (en.frag) {
          const targetEl = el.epubFrame.contentDocument?.getElementById(en.frag);
          if (targetEl) targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    });
  }
  if (el.endnotePopover) {
    el.endnotePopover.querySelectorAll("[data-note-color]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const en = state.activeEndnote;
        if (!en || !state.activeBook) return;
        const color = btn.dataset.noteColor;
        await saveAnnotationToServer({
          chapter_index: en.sourceChapterIndex,
          chapter_href: en.sourceChapterHref,
          chapter_title: en.sourceChapterTitle,
          page_number: en.pageNumber,
          selected_text: en.plainText,
          note: "",
          color,
        });
        hideEndnotePopover();
      });
    });
  }
  if (el.btnAskGeminiEndnote) {
    el.btnAskGeminiEndnote.addEventListener("click", () => {
      const en = state.activeEndnote;
      if (!en) return;
      attachSelectionToChat(en.plainText);
      hideEndnotePopover();
      switchRightTab("gemini");
      el.chatInput.focus();
    });
  }

  // Annotation Editor
  document.querySelectorAll("[data-ed-color]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.editorColor = btn.dataset.edColor;
      document.querySelectorAll("[data-ed-color]").forEach((b) => {
        b.classList.toggle("active", b.dataset.edColor === state.editorColor);
      });
    });
  });

  el.btnCancelAnnotation.addEventListener("click", () => {
    el.annotationEditorCard.classList.add("hidden");
    state.editingAnnotation = null;
  });

  el.btnSaveAnnotation.addEventListener("click", async () => {
    if (!state.activeBook) return;
    const ch = state.activeBook.manifest.chapters[state.currentChapterIndex];
    const existing = state.editingAnnotation;
    const pageVal = el.editorPageInput ? el.editorPageInput.value.trim() : "";
    const payload = {
      id: existing ? existing.id : undefined,
      chapter_index: existing ? existing.chapter_index : state.currentChapterIndex,
      chapter_href: existing ? existing.chapter_href : ch.href,
      chapter_title: existing ? existing.chapter_title : ch.title,
      page_number: pageVal || (existing ? existing.page_number : state.selectedPageNumber) || "",
      selected_text: existing ? existing.selected_text : state.selectedText,
      note: el.editorNoteInput.value.trim(),
      gemini_response: existing ? existing.gemini_response : "",
      color: state.editorColor,
    };
    await saveAnnotationToServer(payload);
    el.annotationEditorCard.classList.add("hidden");
    state.editingAnnotation = null;
  });

  // Sync & Export
  el.btnSyncDrive.addEventListener("click", syncAnnotationsToGoogleDrive);
  el.btnExportMd.addEventListener("click", () => {
    if (!state.activeBook) return;
    window.open(`/api/books/${encodeURIComponent(state.activeBook.id)}/export.md`, "_blank");
  });
  if (el.btnExportBib) {
    el.btnExportBib.addEventListener("click", () => {
      if (!state.activeBook) return;
      window.open(`/api/books/${encodeURIComponent(state.activeBook.id)}/export.bib`, "_blank");
    });
  }
  if (el.btnExportGdoc) {
    el.btnExportGdoc.addEventListener("click", exportNotesToGoogleDoc);
  }

  // Bookmarks & Collections & Global Search
  if (el.btnAddBookmark) {
    el.btnAddBookmark.addEventListener("click", () => addBookmark());
  }
  if (el.btnSaveBookmarkPanel) {
    el.btnSaveBookmarkPanel.addEventListener("click", () => addBookmark(el.bookmarkLabelInput?.value || ""));
  }
  if (el.libraryTagSelect) {
    el.libraryTagSelect.addEventListener("change", (e) => {
      state.tagFilter = e.target.value;
      renderLibraryGrid();
    });
  }
  if (el.btnToggleGlobalSearch) {
    el.btnToggleGlobalSearch.addEventListener("click", () => {
      el.globalSearchPanel?.classList.toggle("hidden");
      if (!el.globalSearchPanel?.classList.contains("hidden")) {
        el.globalSearchInput?.focus();
      }
    });
  }
  if (el.btnCloseGlobalSearch) {
    el.btnCloseGlobalSearch.addEventListener("click", () => {
      el.globalSearchPanel?.classList.add("hidden");
    });
  }
  if (el.btnRunGlobalSearch) {
    el.btnRunGlobalSearch.addEventListener("click", runGlobalSearch);
  }
  if (el.globalSearchInput) {
    el.globalSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") runGlobalSearch();
    });
  }

  // Category Filtering, Search & Grouping in Highlights Tab
  if (el.annCategoryFilterBar) {
    el.annCategoryFilterBar.querySelectorAll("[data-filter-color]").forEach((pill) => {
      pill.addEventListener("click", () => {
        state.annFilterColor = pill.dataset.filterColor;
        el.annCategoryFilterBar.querySelectorAll("[data-filter-color]").forEach((p) => {
          p.classList.toggle("active", p.dataset.filterColor === state.annFilterColor);
        });
        renderAnnotationsList();
      });
    });
  }
  if (el.annSearchInput) {
    const debouncedRenderAnnotations = debounce(() => renderAnnotationsList(), 150);
    el.annSearchInput.addEventListener("input", (e) => {
      state.annSearchQuery = e.target.value;
      debouncedRenderAnnotations();
    });
  }
  if (el.selectAnnGroupBy) {
    el.selectAnnGroupBy.addEventListener("change", (e) => {
      state.annGroupBy = e.target.value;
      renderAnnotationsList();
    });
  }

  // Reading Report Buttons
  if (el.btnGenStructuredReport) {
    el.btnGenStructuredReport.addEventListener("click", () => generateReadingReport("structured"));
  }
  if (el.btnGenGeminiReport) {
    el.btnGenGeminiReport.addEventListener("click", () => generateReadingReport("gemini"));
  }
  if (el.btnCopyReport) {
    el.btnCopyReport.addEventListener("click", () => {
      if (!state.readingReportMarkdown) {
        showToast("Generate a report first!");
        return;
      }
      navigator.clipboard.writeText(state.readingReportMarkdown);
      showToast("Reading Report copied to clipboard as Markdown!");
    });
  }

  // Gemini Chat
  el.btnClearAttachedSelection.addEventListener("click", () => attachSelectionToChat(""));
  el.btnSummarizeChapter.addEventListener("click", () => {
    askGemini({ action: "summarize" });
  });
  if (el.btnArgumentMap) {
    el.btnArgumentMap.addEventListener("click", generateArgumentMap);
  }
  el.btnClearChat.addEventListener("click", async () => {
    if (!state.activeBook) return;
    await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/chats`, { method: "DELETE" });
    state.chats = [];
    state.lastInteractionId = "";
    renderChatMessages();
    showToast("Chat history cleared.");
  });

  el.btnSendChat.addEventListener("click", () => {
    const q = el.chatInput.value.trim();
    if (!q && !state.attachedPassage) return;
    el.chatInput.value = "";
    askGemini({ question: q, action: "ask" });
  });

  el.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el.btnSendChat.click();
    }
  });

  // Full-Book Search
  el.btnRunBookSearch.addEventListener("click", runBookSearch);
  el.bookSearchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runBookSearch();
  });

  // Settings, Shortcuts & Compare Modals
  const refreshCacheStats = async () => {
    if (!el.cacheStatsText) return;
    try {
      const res = await fetch("/api/cache/stats?days=10");
      const st = await res.json();
      if (st.error) throw new Error(st.error);
      el.cacheStatsText.textContent = `${st.total_cached} cached (${formatBytes(st.cache_bytes)}) · ${st.stale_count} unopened for 10+ days`;
    } catch (e) {
      el.cacheStatsText.textContent = "Unable to check cache stats.";
    }
  };
  el.btnOpenSettings.addEventListener("click", () => {
    el.settingsModal.classList.remove("hidden");
    refreshCacheStats();
    refreshHiddenBooksSettings();
  });
  el.btnPruneCache?.addEventListener("click", async () => {
    el.btnPruneCache.disabled = true;
    try {
      const res = await fetch("/api/cache/prune", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 10 }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      showToast(`Cleared ${data.cleared_count} cached EPUB(s) (${formatBytes(data.freed_bytes)} freed). Highlights & notes preserved.`);
      await refreshCacheStats();
      await fetchBooks(false);
    } catch (e) {
      showToast(`Cache clear failed: ${e.message}`);
    } finally {
      el.btnPruneCache.disabled = false;
    }
  });
  el.btnCloseSettings.addEventListener("click", () => {
    el.settingsModal.classList.add("hidden");
  });
  el.btnSaveSettings.addEventListener("click", saveSettings);

  if (el.btnOpenShortcuts) {
    el.btnOpenShortcuts.addEventListener("click", () => {
      el.shortcutsModal?.classList.remove("hidden");
    });
  }
  if (el.btnCloseShortcuts) {
    el.btnCloseShortcuts.addEventListener("click", () => {
      el.shortcutsModal?.classList.add("hidden");
    });
  }
  if (el.btnOpenCompare) {
    el.btnOpenCompare.addEventListener("click", openCompareModal);
  }
  if (el.btnCloseCompare) {
    el.btnCloseCompare.addEventListener("click", () => {
      el.compareModal?.classList.add("hidden");
    });
  }
  if (el.btnRunCompare) {
    el.btnRunCompare.addEventListener("click", runBookComparison);
  }

  // Global keyboard listener for Left / Right arrow chapter switching & shortcuts
  window.addEventListener("keydown", handleReaderKeydown);
}

async function exportNotesToGoogleDoc() {
  if (!state.activeBook) return;
  showToast("Exporting notes to Google Docs...");
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/export-gdoc`, {
      method: "POST",
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const link = data.doc?.webViewLink;
    if (link) {
      window.open(link, "_blank");
      showToast("Exported to Google Docs!");
    } else {
      showToast("Exported to Google Docs in your Drive folder.");
    }
  } catch (e) {
    showToast(`Google Docs export failed: ${e.message}`);
  }
}

async function generateArgumentMap(force = false) {
  if (!state.activeBook) return;
  const empty = el.chatMessages.querySelector(".chat-empty-state");
  if (empty) empty.remove();

  const loadingCard = document.createElement("div");
  loadingCard.className = "chat-bubble chat-bubble-assistant";
  loadingCard.innerHTML = `<div class="assistant-content">🗺️ ${force ? "Regenerating" : "Loading"} chapter thesis, key claims, terms, and critiques...</div>`;
  el.chatMessages.appendChild(loadingCard);
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(state.activeBook.id)}/argument-map`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_index: state.currentChapterIndex, force: Boolean(force) }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const m = data.map || {};
    const renderItems = (items, titleKey, bodyKey) =>
      (items || [])
        .map(
          (it) => `
          <div class="arg-map-item">
            <div><strong>${escapeHtml(it[titleKey] || "")}</strong>${bodyKey && it[bodyKey] ? `: ${escapeHtml(it[bodyKey])}` : ""}</div>
            ${
              it.quote
                ? `<button type="button" class="arg-jump-btn" data-jump-quote="${escapeHtml(it.quote)}">Jump to passage →</button>`
                : ""
            }
          </div>`
        )
        .join("");

    loadingCard.innerHTML = `
      <div class="arg-map-card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <div><strong>🗺️ Chapter Argument Map</strong> <span style="opacity:0.65;font-size:11px;">(${escapeHtml(data.model || "")}${data.cached ? " · saved" : ""})</span></div>
          <button type="button" class="btn btn-xs btn-ghost btn-regen-argmap" title="Regenerate with Gemini">↻ Regenerate</button>
        </div>
        ${m.thesis ? `<div class="arg-map-section"><h4>Thesis</h4><div>${escapeHtml(m.thesis)}</div></div>` : ""}
        ${(m.key_claims || []).length ? `<div class="arg-map-section"><h4>Key Claims</h4>${renderItems(m.key_claims, "role", "claim")}</div>` : ""}
        ${(m.key_terms || []).length ? `<div class="arg-map-section"><h4>Key Terms</h4>${renderItems(m.key_terms, "term", "definition")}</div>` : ""}
        ${(m.critiques || []).length ? `<div class="arg-map-section"><h4>Critiques</h4>${renderItems(m.critiques, "target", "critique")}</div>` : ""}
      </div>`;
    loadingCard.querySelectorAll("[data-jump-quote]").forEach((btn) => {
      btn.addEventListener("click", () => {
        scrollToPhraseInIframe(btn.dataset.jumpQuote);
      });
    });
    loadingCard.querySelector(".btn-regen-argmap")?.addEventListener("click", () => {
      loadingCard.remove();
      generateArgumentMap(true);
    });
  } catch (e) {
    loadingCard.innerHTML = `<div class="assistant-content">Argument map failed: ${escapeHtml(e.message)}</div>`;
  }
}

function openCompareModal() {
  if (!el.compareModal || !el.compareBookA || !el.compareBookB) return;
  const optionsHtml = state.books
    .slice(0, 300)
    .map((b) => `<option value="${escapeHtml(b.id)}">${escapeHtml(b.title || b.name)}${b.author ? ` — ${escapeHtml(b.author)}` : ""}</option>`)
    .join("");
  el.compareBookA.innerHTML = optionsHtml;
  el.compareBookB.innerHTML = optionsHtml;
  if (state.activeBook) {
    el.compareBookA.value = state.activeBook.id;
  }
  if (state.books.length > 1 && el.compareBookB.value === el.compareBookA.value) {
    el.compareBookB.selectedIndex = 1;
  }
  el.compareResultBox?.classList.add("hidden");
  el.compareModal.classList.remove("hidden");
}

async function runBookComparison() {
  const idA = el.compareBookA?.value;
  const idB = el.compareBookB?.value;
  const topic = (el.compareTopicInput?.value || "").trim();
  if (!idA || !idB || idA === idB || !topic) {
    showToast("Select two different books and enter a concept or question.");
    return;
  }
  el.compareResultBox.classList.remove("hidden");
  el.compareResultBox.innerHTML = "✨ Comparing books with Gemini...";
  try {
    const res = await fetch("/api/gemini/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_ids: [idA, idB], topic }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    el.compareResultBox.innerHTML = renderMarkdown(data.comparison || "No response.");
  } catch (e) {
    el.compareResultBox.innerHTML = `Comparison failed: ${escapeHtml(e.message)}`;
  }
}

function hideHighlightMiniToolbar() {
  el.highlightMiniToolbar?.classList.add("hidden");
  state.activeMiniAnnId = "";
}

function showHighlightMiniToolbar(markEl, annId) {
  if (!el.highlightMiniToolbar || !markEl) return;
  state.activeMiniAnnId = annId;
  const markRect = markEl.getBoundingClientRect();
  const frameRect = el.epubFrame.getBoundingClientRect();
  const centerRect = el.epubFrame.parentElement.parentElement.getBoundingClientRect();
  const top = Math.max(8, frameRect.top - centerRect.top + markRect.top - 42);
  const left = Math.min(
    centerRect.width - 270,
    Math.max(12, frameRect.left - centerRect.left + markRect.left)
  );
  el.highlightMiniToolbar.style.top = `${top}px`;
  el.highlightMiniToolbar.style.left = `${left}px`;
  el.highlightMiniToolbar.classList.remove("hidden");
}

function initSidebarResizers() {
  const savedLeft = localStorage.getItem("folio_left_width");
  const savedRight = localStorage.getItem("folio_right_width");
  if (savedLeft && el.leftSidebar) el.leftSidebar.style.width = `${savedLeft}px`;
  if (savedRight && el.rightSidebar) el.rightSidebar.style.width = `${savedRight}px`;

  const attachDrag = (handle, sidebar, isLeft, storageKey) => {
    if (!handle || !sidebar) return;
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      handle.classList.add("active");
      if (el.epubFrame) el.epubFrame.style.pointerEvents = "none";
      const onMove = (mv) => {
        const w = isLeft ? mv.clientX : window.innerWidth - mv.clientX;
        const clamped = Math.max(220, Math.min(620, w));
        sidebar.style.width = `${clamped}px`;
        localStorage.setItem(storageKey, String(clamped));
      };
      const onUp = () => {
        handle.classList.remove("active");
        if (el.epubFrame) el.epubFrame.style.pointerEvents = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
  };
  attachDrag(el.leftResizeHandle, el.leftSidebar, true, "folio_left_width");
  attachDrag(el.rightResizeHandle, el.rightSidebar, false, "folio_right_width");
}

function initNewUiControls() {
  // Typography & Export Popovers
  el.btnTypographyMenu?.addEventListener("click", (e) => {
    e.stopPropagation();
    el.exportMenuPopover?.classList.add("hidden");
    el.typographyPopover?.classList.toggle("hidden");
  });
  el.btnExportMenu?.addEventListener("click", (e) => {
    e.stopPropagation();
    el.typographyPopover?.classList.add("hidden");
    el.exportMenuPopover?.classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (el.typographyPopover && !e.target.closest(".toolbar-dropdown-wrap")) {
      el.typographyPopover.classList.add("hidden");
    }
    if (el.exportMenuPopover && !e.target.closest(".toolbar-dropdown-wrap")) {
      el.exportMenuPopover.classList.add("hidden");
    }
    if (el.tagPickerPopover && !e.target.closest("#tagPickerPopover") && !e.target.closest(".btn-add-tag")) {
      hideTagPicker();
    }
    if (el.highlightMiniToolbar && !e.target.closest("#highlightMiniToolbar")) {
      hideHighlightMiniToolbar();
    }
  });

  // Extended Typography Controls
  if (el.selectLineHeight) {
    el.selectLineHeight.value = state.lineHeight;
    el.selectLineHeight.addEventListener("change", (e) => {
      state.lineHeight = e.target.value;
      localStorage.setItem("folio_line_height", state.lineHeight);
      injectReaderStylesIntoIframe();
    });
  }
  if (el.selectColumnWidth) {
    el.selectColumnWidth.value = state.columnWidth;
    el.selectColumnWidth.addEventListener("change", (e) => {
      state.columnWidth = e.target.value;
      localStorage.setItem("folio_col_width", state.columnWidth);
      injectReaderStylesIntoIframe();
    });
  }
  if (el.selectTextAlign) {
    el.selectTextAlign.value = state.textAlign;
    el.selectTextAlign.addEventListener("change", (e) => {
      state.textAlign = e.target.value;
      localStorage.setItem("folio_text_align", state.textAlign);
      injectReaderStylesIntoIframe();
    });
  }

  // Library Sort & Grid/List View
  if (el.librarySortSelect) {
    el.librarySortSelect.value = state.librarySort;
    el.librarySortSelect.addEventListener("change", (e) => {
      state.librarySort = e.target.value;
      localStorage.setItem("folio_library_sort", state.librarySort);
      renderLibraryGrid();
    });
  }
  const setViewMode = (mode) => {
    state.libraryViewMode = mode;
    localStorage.setItem("folio_library_view", mode);
    el.btnViewGrid?.classList.toggle("active", mode === "grid");
    el.btnViewList?.classList.toggle("active", mode === "list");
    renderLibraryGrid();
  };
  el.btnViewGrid?.classList.toggle("active", state.libraryViewMode === "grid");
  el.btnViewList?.classList.toggle("active", state.libraryViewMode === "list");
  el.btnViewGrid?.addEventListener("click", () => setViewMode("grid"));
  el.btnViewList?.addEventListener("click", () => setViewMode("list"));

  // Inline Tag Picker Add
  const submitNewTag = async () => {
    const bid = state.tagPickerBookId;
    const val = (el.tagPickerInput?.value || "").trim();
    if (!bid || !val) return;
    const book = state.books.find((b) => b.id === bid);
    await updateBookTags(bid, [...(book?.tags || []), val]);
    el.tagPickerInput.value = "";
    hideTagPicker();
  };
  el.btnTagPickerAdd?.addEventListener("click", submitNewTag);
  el.tagPickerInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitNewTag();
  });

  // Focus Mode
  el.btnToggleFocusMode?.addEventListener("click", () => document.body.classList.toggle("focus-mode"));
  el.btnExitFocus?.addEventListener("click", () => document.body.classList.remove("focus-mode"));

  // In-Place Highlight Mini-Toolbar Actions
  el.highlightMiniToolbar?.querySelectorAll("[data-mini-color]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ann = state.annotations.find((a) => a.id === state.activeMiniAnnId);
      if (!ann) return;
      await saveAnnotationToServer({ ...ann, color: btn.dataset.miniColor });
      hideHighlightMiniToolbar();
    });
  });
  el.btnMiniEditNote?.addEventListener("click", () => {
    const annId = state.activeMiniAnnId;
    hideHighlightMiniToolbar();
    switchRightTab("annotations");
    const card = el.annotationsList.querySelector(`[data-ann-card-id="${annId}"]`);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.querySelector("[data-edit-ann]")?.click();
    }
  });
  el.btnMiniAskGemini?.addEventListener("click", () => {
    const ann = state.annotations.find((a) => a.id === state.activeMiniAnnId);
    hideHighlightMiniToolbar();
    if (!ann) return;
    switchRightTab("gemini");
    attachSelectionToChat(ann.selected_text);
    el.chatInput?.focus();
  });
  el.btnMiniDelete?.addEventListener("click", async () => {
    const annId = state.activeMiniAnnId;
    hideHighlightMiniToolbar();
    if (!annId || !state.activeBook) return;
    await fetch(
      `/api/books/${encodeURIComponent(state.activeBook.id)}/annotations/${encodeURIComponent(annId)}`,
      { method: "DELETE" }
    );
    state.annotations = state.annotations.filter((a) => a.id !== annId);
    applySavedHighlightsToIframe();
    renderAnnotationsList();
    showToast("Highlight deleted.");
  });
  // Drive OAuth Connect
  el.btnConnectDriveOAuth?.addEventListener("click", async () => {
    const cid = el.inputGoogleClientId?.value.trim();
    const csec = el.inputGoogleClientSecret?.value.trim();
    if (cid || csec) {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(cid ? { google_client_id: cid } : {}),
          ...(csec ? { google_client_secret: csec } : {}),
        }),
      });
    }
    const res = await fetch("/api/drive/oauth/start");
    const data = await res.json();
    if (data.error) {
      showToast(data.error);
      return;
    }
    if (data.auth_url) window.location.href = data.auth_url;
  });

  // Passcode Unlock
  const submitUnlock = async () => {
    const pc = (el.inputAuthPasscode?.value || "").trim();
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: pc }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      if (el.authErrorMsg) {
        el.authErrorMsg.textContent = data.error || "Incorrect passcode";
        el.authErrorMsg.classList.remove("hidden");
      }
      return;
    }
    el.authModal?.classList.add("hidden");
    await loadStatus();
    await fetchBooks(false);
  };
  el.btnUnlockFolio?.addEventListener("click", submitUnlock);
  el.inputAuthPasscode?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitUnlock();
  });
}

// --- Command Palette (Cmd/Ctrl+K) ---

let _cmdItems = [];
let _cmdActiveIdx = 0;

function openCommandPalette() {
  if (!el.cmdPaletteModal || !el.cmdPaletteInput) return;
  el.cmdPaletteModal.classList.remove("hidden");
  el.cmdPaletteInput.value = "";
  _cmdActiveIdx = 0;
  renderCommandPalette("");
  el.cmdPaletteInput.focus();
}

function closeCommandPalette() {
  el.cmdPaletteModal?.classList.add("hidden");
}

function buildCommandPaletteLocalItems(q) {
  const ql = q.toLowerCase().trim();
  const groups = [];

  if (state.activeBook) {
    const chapters = state.activeBook.manifest?.chapters || [];
    const chMatches = chapters
      .filter((c) => !ql || (c.title || "").toLowerCase().includes(ql))
      .slice(0, 6)
      .map((c) => ({
        title: c.title || `Section ${c.index + 1}`,
        sub: `Current book · Section ${c.index + 1}`,
        badge: "Chapter",
        run: () => loadChapter(c.index),
      }));
    if (chMatches.length) groups.push({ label: "Chapters in Current Book", items: chMatches });
  }

  const bookMatches = state.books
    .filter((b) => {
      if (!ql) return Boolean(b.last_opened_at);
      return (
        (b.title || "").toLowerCase().includes(ql) ||
        (b.author || "").toLowerCase().includes(ql) ||
        (b.name || "").toLowerCase().includes(ql)
      );
    })
    .slice(0, ql ? 8 : 5)
    .map((b) => ({
      title: b.title || b.name,
      sub: b.author || b.folder_path || "EPUB",
      badge: b.downloaded ? "Cached" : "Drive",
      run: () => openBook(b.id),
    }));
  if (bookMatches.length) groups.push({ label: ql ? "Library Books" : "Recent Books", items: bookMatches });

  const actions = [
    ...(state.activeBook
      ? [
          { title: "Toggle Focus / Zen Mode", sub: "Shortcut: f", badge: "Action", run: () => document.body.classList.toggle("focus-mode") },
          { title: "Generate Chapter Argument Map", sub: "Gemini thesis, claims & terms", badge: "Action", run: () => { switchRightTab("gemini"); generateArgumentMap(false); } },
          { title: "Sync Highlights to Google Drive", sub: "Upload .annotations.json & - Notes.md", badge: "Action", run: () => syncAnnotationsToGoogleDrive() },
          { title: "Back to Library", sub: "Return to book grid", badge: "Action", run: () => backToLibrary() },
        ]
      : []),
    { title: "Compare Books with Gemini", sub: "Cross-book concept synthesis", badge: "Action", run: () => openCompareModal() },
    { title: "Open Settings", sub: "Gemini API key, OAuth, cache", badge: "Action", run: () => el.btnOpenSettings?.click() },
  ].filter((a) => !ql || a.title.toLowerCase().includes(ql) || a.sub.toLowerCase().includes(ql));
  if (actions.length) groups.push({ label: "Quick Actions", items: actions });

  return groups;
}

function paintCommandPaletteGroups(groups) {
  if (!el.cmdPaletteResults) return;
  _cmdItems = [];
  if (groups.length === 0) {
    el.cmdPaletteResults.innerHTML = `<p class="muted-hint" style="padding:12px;">No matches found.</p>`;
    return;
  }
  let html = "";
  for (const g of groups) {
    html += `<div class="cmd-palette-group">${escapeHtml(g.label)}</div>`;
    for (const item of g.items) {
      const idx = _cmdItems.length;
      _cmdItems.push(item);
      html += `
        <div class="cmd-palette-item ${idx === _cmdActiveIdx ? "active" : ""}" data-cmd-idx="${idx}">
          <div class="cmd-palette-item-main">
            <div class="cmd-palette-item-title">${escapeHtml(item.title)}</div>
            <div class="cmd-palette-item-sub">${escapeHtml(item.sub || "")}</div>
          </div>
          <span class="cmd-palette-item-badge">${escapeHtml(item.badge || "")}</span>
        </div>`;
    }
  }
  el.cmdPaletteResults.innerHTML = html;
  el.cmdPaletteResults.querySelectorAll(".cmd-palette-item").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = parseInt(row.dataset.cmdIdx, 10);
      const item = _cmdItems[idx];
      if (item) {
        closeCommandPalette();
        item.run();
      }
    });
  });
}

let _cmdSearchSeq = 0;
function renderCommandPalette(q) {
  _cmdActiveIdx = 0;
  const localGroups = buildCommandPaletteLocalItems(q);
  paintCommandPaletteGroups(localGroups);

  const trimmed = q.trim();
  if (trimmed.length < 3) return;
  const seq = ++_cmdSearchSeq;
  fetch(`/api/search?q=${encodeURIComponent(trimmed)}`)
    .then((r) => r.json())
    .then((data) => {
      if (seq !== _cmdSearchSeq || el.cmdPaletteModal?.classList.contains("hidden")) return;
      const extraGroups = [...localGroups];
      const anns = (data.annotation_matches || []).slice(0, 5);
      if (anns.length) {
        extraGroups.push({
          label: "Highlights & Notes (FTS5)",
          items: anns.map((a) => ({
            title: `"${(a.selected_text || "").slice(0, 80)}"`,
            sub: `${a.book_title || a.book_name} · ${a.chapter_title || ""}`,
            badge: "Highlight",
            run: () => openBook(a.book_id, { chapterIndex: a.chapter_index, highlightPhrase: (a.selected_text || "").slice(0, 60) }),
          })),
        });
      }
      const bms = (data.book_matches || []).slice(0, 5);
      if (bms.length) {
        extraGroups.push({
          label: "Full-Text Passages (FTS5)",
          items: bms.map((m) => ({
            title: m.book_title,
            sub: `${m.chapter_title}: ${(m.snippet || "").slice(0, 90)}`,
            badge: "Passage",
            run: () => openBook(m.book_id, { chapterIndex: m.chapter_index, highlightPhrase: trimmed }),
          })),
        });
      }
      paintCommandPaletteGroups(extraGroups);
    })
    .catch(() => {});
}

function initCommandPalette() {
  if (!el.cmdPaletteModal || !el.cmdPaletteInput) return;
  el.cmdPaletteModal.addEventListener("click", (e) => {
    if (e.target === el.cmdPaletteModal) closeCommandPalette();
  });
  const debouncedCmd = debounce((val) => renderCommandPalette(val), 140);
  el.cmdPaletteInput.addEventListener("input", (e) => debouncedCmd(e.target.value));
  el.cmdPaletteInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (_cmdItems.length) {
        _cmdActiveIdx = (_cmdActiveIdx + 1) % _cmdItems.length;
        el.cmdPaletteResults.querySelectorAll(".cmd-palette-item").forEach((n, i) => n.classList.toggle("active", i === _cmdActiveIdx));
        el.cmdPaletteResults.querySelector(".cmd-palette-item.active")?.scrollIntoView({ block: "nearest" });
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (_cmdItems.length) {
        _cmdActiveIdx = (_cmdActiveIdx - 1 + _cmdItems.length) % _cmdItems.length;
        el.cmdPaletteResults.querySelectorAll(".cmd-palette-item").forEach((n, i) => n.classList.toggle("active", i === _cmdActiveIdx));
        el.cmdPaletteResults.querySelector(".cmd-palette-item.active")?.scrollIntoView({ block: "nearest" });
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = _cmdItems[_cmdActiveIdx];
      if (item) {
        closeCommandPalette();
        item.run();
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeCommandPalette();
    }
  });
}

// --- Shelf Format Switcher & Uncompiled Chapter-Only Books Modal (BookTool) ---

function renderUncompiledList() {
  if (!el.uncompiledList) return;
  const q = (el.uncompiledFilterInput?.value || "").toLowerCase().trim();
  const rows = state.uncompiledFolders.filter((f) => {
    if (!q) return true;
    return (
      (f.folder_name || "").toLowerCase().includes(q) ||
      (f.folder_path || "").toLowerCase().includes(q)
    );
  });

  if (rows.length === 0) {
    el.uncompiledList.innerHTML = `<p class="muted-hint">No uncompiled chapter-only book folders found.</p>`;
    return;
  }

  el.uncompiledList.innerHTML = rows
    .map(
      (f) => `
      <div class="uncompiled-row" data-folder-id="${escapeHtml(f.folder_id)}">
        <div class="uncompiled-row-info">
          <div class="uncompiled-row-title">${escapeHtml(f.folder_name)}</div>
          <div class="uncompiled-row-meta">
            <span>📁 ${escapeHtml(f.folder_path || f.source_root)}</span>
            <span>· ${f.chapter_count} chapter PDFs (${formatBytes(f.total_size)})</span>
          </div>
        </div>
        <button class="btn btn-primary btn-xs btn-compile-folder" data-compile-folder="${escapeHtml(f.folder_id)}">
          ⚡ Compile with BookTool
        </button>
      </div>`
    )
    .join("");

  el.uncompiledList.querySelectorAll("[data-compile-folder]").forEach((btn) => {
    btn.addEventListener("click", () => compileUncompiledFolder(btn.dataset.compileFolder, btn));
  });
}

async function compileUncompiledFolder(folderId, btnEl = null) {
  const folder = state.uncompiledFolders.find((f) => f.folder_id === folderId);
  const label = folder?.folder_name || folderId;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "Compiling...";
  }
  try {
    const res = await fetch(`/api/pdfs/compile/${encodeURIComponent(folderId)}`, { method: "POST" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    state.uncompiledFolders = data.uncompiled_folders || state.uncompiledFolders.filter((f) => f.folder_id !== folderId);
    if (data.book) {
      const idx = state.books.findIndex((b) => b.id === data.book.id);
      if (idx !== -1) state.books[idx] = data.book;
      else state.books.unshift(data.book);
    }
    updateShelfCountPills();
    populateFolderDropdown();
    renderLibraryGrid();
    renderUncompiledList();

    const pages = data.compile_meta?.total_pages ? ` (${data.compile_meta.total_pages} pp.)` : "";
    showToast(
      `Compiled "${label}"${pages} & uploaded to Drive!`,
      5500,
      data.book
        ? {
            label: "Open PDF",
            onClick: () => {
              el.uncompiledModal?.classList.add("hidden");
              openBook(data.book.id);
            },
          }
        : null
    );
    return true;
  } catch (e) {
    showToast(`Failed to compile "${label}": ${e.message}`, 5000);
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "⚡ Compile with BookTool";
    }
    return false;
  }
}

async function compileVisibleUncompiled() {
  const btns = Array.from(el.uncompiledList?.querySelectorAll("[data-compile-folder]") || []);
  if (!btns.length) return;
  if (!confirm(`Compile ${btns.length} chapter-only book folder(s) with BookTool and upload the full-book PDFs to Google Drive?`)) {
    return;
  }
  if (el.btnCompileAllUncompiled) {
    el.btnCompileAllUncompiled.disabled = true;
    el.btnCompileAllUncompiled.textContent = `Compiling 0/${btns.length}...`;
  }
  let done = 0;
  for (const btn of btns) {
    const fid = btn.dataset.compileFolder;
    await compileUncompiledFolder(fid, btn);
    done += 1;
    if (el.btnCompileAllUncompiled) {
      el.btnCompileAllUncompiled.textContent = `Compiling ${done}/${btns.length}...`;
    }
  }
  if (el.btnCompileAllUncompiled) {
    el.btnCompileAllUncompiled.disabled = false;
    el.btnCompileAllUncompiled.textContent = "⚡ Compile Visible";
  }
}

function initPdfAndShelfControls() {
  document.querySelectorAll(".shelf-tab").forEach((btn) => {
    btn.addEventListener("click", () => setLibraryFormat(btn.dataset.format));
  });

  el.pdfCategorySelect?.addEventListener("change", (e) => {
    state.pdfCategoryFilter = e.target.value;
    renderLibraryGrid();
  });

  el.btnOpenUncompiled?.addEventListener("click", () => {
    renderUncompiledList();
    el.uncompiledModal?.classList.remove("hidden");
  });

  el.btnCloseUncompiled?.addEventListener("click", () => {
    el.uncompiledModal?.classList.add("hidden");
  });

  el.uncompiledModal?.addEventListener("click", (e) => {
    if (e.target === el.uncompiledModal) el.uncompiledModal.classList.add("hidden");
  });

  el.uncompiledFilterInput?.addEventListener("input", () => renderUncompiledList());
  el.btnCompileAllUncompiled?.addEventListener("click", () => compileVisibleUncompiled());
}

// --- Boot ---
(async function init() {
  applyTheme(state.theme);
  updateTypography(0, state.fontFamily);
  initEventListeners();
  initNewUiControls();
  initPdfAndShelfControls();
  initSidebarResizers();
  initCommandPalette();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    const authRes = await fetch("/api/auth/status");
    const authData = await authRes.json();
    if (authData.passcode_required && !authData.authenticated) {
      el.authModal?.classList.remove("hidden");
      el.inputAuthPasscode?.focus();
      return;
    }
  } catch (e) {
    // ignore
  }
  await loadStatus();
  if (el.googleClientId !== undefined) {
    // no-op
  }
  if (new URLSearchParams(window.location.search).get("drive_connected") === "1") {
    showToast("Google Drive connected via Cloud OAuth!");
    window.history.replaceState({}, "", "/");
  }
  setLibraryFormat(state.libraryFormat, { skipAutoSync: true });
  await fetchBooks(false);
})();
