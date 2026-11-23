import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { renderRenameView, createRenameStore, RENAME_JOB_TYPES } from "./rename_view.js";
import { renderGalleryView } from "./gallery_view.js";
import {
  CONFIRM_FOR,
  MODEL_DOWNLOAD_JOB,
  finishedText,
  galleryKey,
  isPaid,
  latestRecords,
  recordMessage,
  rowDeleteHtml,
  versionActionsHtml,
  versionKey,
} from "./model_actions.js";
import { galleryOnlyBases, STAT_SORTS } from "./gallery_core.js";
import { archiveResult, checkDoneStatus, issueMessage, rowIssue, rowLabel } from "./check_results.js";
import { infoHtml, menuHtml, moreMenuItems, savedArchiveFolder, toolbarHtml, updateToolbar, wireToolbar } from "./panel_toolbar.js";
import { noteUsed, promptNodesOf } from "./prompt_target.js";
import { createScrollMemory, findScroller } from "./scroll_memory.js";
// The server refuses requests without this header (see request_guard.js).
import { guardHeaders } from "./request_guard.js";

const TAB_ID = "warp-studio-updater";
const EXTENSION_NAME = "warpstudio.updater";
const TAB_ICON_CLASS = "cu-tab-icon";
const TAB_ICON = `pi pi-refresh ${TAB_ICON_CLASS}`;
const TAB_ICON_URL = new URL("./icon-monochrome.svg", import.meta.url).href;
const MODEL_TYPES = ["checkpoint", "lora", "vae", "unet", "embedding"];
const PAGE_SIZES = [25, 50, 100];
const POLL_MS = 800;
const QUIET_RELOAD_MS = 3000;
const ETA_SMOOTHING = 0.25;

const SETTINGS = {
  apiKey: "WarpStudio.Updater.APIKey",
  cacheTtlMinutes: "WarpStudio.Updater.CacheTtlMinutes",
  requestTimeoutSeconds: "WarpStudio.Updater.RequestTimeoutSeconds",
  maxRetries: "WarpStudio.Updater.MaxRetries",
  requestDelayMs: "WarpStudio.Updater.RequestDelayMs",
  treatSidecarsAsInstalled: "WarpStudio.Updater.TreatSidecarsAsInstalled",
  useComfyPaths: "WarpStudio.Updater.PathSources.UseComfy",
  useExtraModelPaths: "WarpStudio.Updater.PathSources.UseExtraModelPaths",
  useCustomPaths: "WarpStudio.Updater.PathSources.UseCustom",
  archiveFolder: "WarpStudio.Updater.ArchiveFolder",
  customCheckpoint: "WarpStudio.Updater.CustomPaths.Checkpoint",
  customLora: "WarpStudio.Updater.CustomPaths.Lora",
  customVae: "WarpStudio.Updater.CustomPaths.VAE",
  customUnet: "WarpStudio.Updater.CustomPaths.UNet",
  customEmbedding: "WarpStudio.Updater.CustomPaths.Embedding",
  matureMode: "WarpStudio.Updater.MatureContent",
  namingModel: "WarpStudio.Updater.NamingModel",
  fallbackCivarchive: "WarpStudio.Updater.Fallback.Civarchive",
  fallbackHuggingFace: "WarpStudio.Updater.Fallback.HuggingFace",
  fallbackAiToolkitFolders: "WarpStudio.Updater.Fallback.AiToolkitFolders",
  fallbackOwnCreator: "WarpStudio.Updater.Fallback.OwnCreator",
};

const GROUP_OPTIONS = [
  { value: "none", label: "Nothing", short: "None" },
  { value: "type", label: "Model type", short: "Type" },
  { value: "baseFamily", label: "Base model", short: "Base" },
];

const GALLERY_ARRANGE_KEY = "warpstudio.gallery.arrange";
const GALLERY_GROUPS = [
  { value: "base", label: "Base" },
  { value: "none", label: "None" },
];
const GALLERY_SORTS = [
  { value: "name", label: "Name" },
  { value: "newest", label: "Newest on disk" },
  { value: "downloads", label: "Most downloaded" },
  { value: "likes", label: "Most liked" },
  { value: "comments", label: "Most comments" },
];

/* The Gallery's Group by and Sort, remembered across reloads. */
function readGalleryArrange() {
  const fallback = { groupBy: "base", sort: "name" };
  try {
    const saved = JSON.parse(globalThis.localStorage?.getItem(GALLERY_ARRANGE_KEY) ?? "null");
    return {
      groupBy: GALLERY_GROUPS.some((o) => o.value === saved?.groupBy) ? saved.groupBy : fallback.groupBy,
      sort: GALLERY_SORTS.some((o) => o.value === saved?.sort) ? saved.sort : fallback.sort,
    };
  } catch (_) {
    return fallback;
  }
}

function writeGalleryArrange(value) {
  try {
    globalThis.localStorage?.setItem(GALLERY_ARRANGE_KEY, JSON.stringify(value));
  } catch (_) {
    // not remembered; the Gallery works the same
  }
}

const SORT_OPTIONS = [
  { value: "name", label: "Name A\u2013Z" },
  { value: "name-desc", label: "Name Z\u2013A" },
  { value: "latest-date-desc", label: "Newest release" },
  { value: "latest-date", label: "Oldest release" },
  { value: "behind", label: "Furthest behind" },
];

const state = {
  roots: {},
  rootsExpanded: false,
  currentJobId: null,
  currentJobType: null,
  currentJobStatus: null,
  currentSummary: null,
  currentProgress: 0,
  currentTotal: 0,
  currentItemCount: 0,
  etaSecPerItem: null,
  etaLastSample: null,
  pollTimer: null,
  lastStatus: "",
  lastItemCount: -1,
  forceNextRecheck: false,
  cachedJobId: null,
  cacheFilesChanged: null,
  sidecarWarnings: [],
  treatSidecarsAsInstalled: null,

  checkJobId: null,
  checkSummary: null,
  archiveInfo: null,
  archiveError: "",
  // The archive folder the server kept (not the setting as typed): the ⋯ menu offers it.
  savedArchiveFolder: "",
  cachedAt: null,
  scanSummary: null,
  resultItems: [],
  resultTotal: 0,
  resultOffset: 0,
  resultRequestSeq: 0,
  pageSize: PAGE_SIZES[0],
  pageOffset: 0,
  scanHint: "",
  filterTypes: null,
  filterBases: null,
  showHidden: false,
  sortOrder: "name",
  groupBy: "type",
  thenBy: "baseFamily",
  matureMode: "show",
  matureHidden: 0,
  groups: [],
  collapsed: new Set(),
  startsMidPrimary: false,
  startsMidSecondary: false,
  lastRenderSignature: "",
  lastPageKey: "",
  lastQuietReloadAt: 0,
  downloadWatch: null,
  renderedEntries: null,
  freshPaths: new Set(),
  facets: { modelTypes: [], baseModels: [] },
  scopeTypes: [...MODEL_TYPES],
  forceRehash: false,
  refetchMetadata: false,
  statusText: "",
  // Errors stay on screen under the job bar; routine text is the job bar's label while a job runs.
  errorText: "",
  jobText: "",
  noticeText: "",
  resultMode: "gallery",
  modeCounts: { updates: 0, issues: 0 },
  stopRequested: false,

  rootEl: null,
  sidecarWarningsEl: null,
  statusEl: null,
  progressWrapEl: null,
  progressFillEl: null,
  progressTextEl: null,
  scanReportEl: null,
  viewSwitchEl: null,
  jobBarEl: null,
  jobTextEl: null,
  gearEl: null,
  settingsEl: null,
  settingsOpen: false,
  // One toolbar per tab: its element and its wiring.
  toolbarEls: {},
  toolbarWiring: {},
  resultsEl: null,
  pageInfoEl: null,
  prevEl: null,
  nextEl: null,
  rootsSummaryEl: null,
  rootsDetailsEl: null,
  pauseEl: null,
  stopEl: null,
  jobControlsEl: null,

  settingsSyncTimer: null,
  // ComfyUI calls every setting's onChange as it registers, before setup().
  // Syncing then would post this install's stored values over the backend's
  // config (a fresh ComfyUI folder would erase the API key and custom paths),
  // so syncing stays off until the backend's values have been loaded.
  suspendSettingsSync: true,
  progressEtaEl: null,
  lightboxKeyHandler: null,
  recoveringJob: false,

  currentMessage: "",
  // The Rename view keeps its rows and choices here, so they survive ComfyUI
  // rebuilding the tab each time it is opened.
  renameStore: createRenameStore(),
  renameView: null,
  renamePaneEl: null,
  updatesPaneEl: null,
  // The Gallery view, its pane, its filter options, and whether it has loaded.
  galleryView: null,
  galleryPaneEl: null,
  galleryFacets: { modelTypes: [], baseModels: [] },
  galleryLoaded: false,
  galleryTabEl: null,
  renameTabEl: null,
  // The Gallery's search lasts the session; "Include downloadable" too, the arrangement is stored.
  galleryQuery: "",
  includeDownloadable: false,
  galleryArrange: readGalleryArrange(),
  // Civitai downloads, likes and comments by model id, for the stat sorts.
  modelStats: new Map(),
  // An "open the gallery" request that arrived while the panel was not drawn.
  pendingGallery: null,
  // Scroll offset per view, and the view whose pane is showing.
  scrollMemory: createScrollMemory(),
  shownMode: null,
  // Text encoders the backend can run, from /rename/status: the naming-model setting's options.
  namingModels: [],
  syncedNamingModel: null,
  // True only after the user picks a naming model in the settings: until then
  // the panel never posts one, so it cannot overwrite the backend's choice.
  namingModelDirty: false,
  // Object URLs of previews read from local files, and path -> request. Kept
  // across re-renders of the list; revoked when the results' job changes.
  localPreviewUrls: new Set(),
  localPreviewCache: new Map(),
  localPreviewJobId: null,
  lightboxLocalUrls: null,
  // Model downloads: the newest record per key (version:ID), and whether the
  // server can send a replaced file to the Recycle Bin.
  downloads: new Map(),
  canTrash: false,
};

app.registerExtension({
  name: EXTENSION_NAME,
  settings: [
    { id: SETTINGS.apiKey, name: "API Key", type: "text", defaultValue: "", attrs: { type: "password", autocomplete: "off" }, tooltip: "Optional Civitai API key for restricted resources.", category: ["Warp Studio", "Civitai Network", "API Key"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.cacheTtlMinutes, name: "Cache Duration (minutes)", type: "number", defaultValue: 240, attrs: { min: 0, max: 10080, step: 30 }, tooltip: "How long to reuse cached check results before re-checking. 0 = always check fresh.", category: ["Warp Studio", "Civitai General", "Cache Duration"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.requestTimeoutSeconds, name: "Request Timeout (seconds)", type: "number", defaultValue: 30, attrs: { min: 5, max: 300, step: 1 }, category: ["Warp Studio", "Civitai Network", "Request Timeout"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.maxRetries, name: "Max Retries", type: "number", defaultValue: 4, attrs: { min: 0, max: 10, step: 1 }, category: ["Warp Studio", "Civitai Network", "Max Retries"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.requestDelayMs, name: "Delay Between Models (ms)", type: "number", defaultValue: 120, attrs: { min: 0, max: 3000, step: 10 }, tooltip: "Small delay between model checks to reduce request bursts.", category: ["Warp Studio", "Civitai Network", "Request Delay"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.treatSidecarsAsInstalled, name: "Treat .civitai.info as Installed", type: "boolean", defaultValue: true, tooltip: "Count valid .civitai.info files as installed model versions when the weight file is missing.", category: ["Warp Studio", "Civitai General", "Sidecar-only Models"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.useComfyPaths, name: "Use Comfy Default Paths", type: "boolean", defaultValue: true, category: ["Warp Studio", "Civitai Path Sources", "Comfy Defaults"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.useExtraModelPaths, name: "Use extra_model_paths.yaml", type: "boolean", defaultValue: true, category: ["Warp Studio", "Civitai Path Sources", "Extra Model Paths"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.useCustomPaths, name: "Use Custom Paths", type: "boolean", defaultValue: true, category: ["Warp Studio", "Civitai Path Sources", "Custom Paths"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.archiveFolder, name: "Archive Folder", type: "text", defaultValue: "", tooltip: "A folder of models you moved off this drive. Check with archive, under ⋯ in Updates, looks for new versions of them. Warp Studio only reads its .civitai.info files.", category: ["Warp Studio", "Civitai Path Sources", "Archive Folder"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.customCheckpoint, name: "Checkpoint Paths", type: "text", defaultValue: "", tooltip: "Optional extra checkpoint roots. Use ';' or new lines.", category: ["Warp Studio", "Civitai Custom Paths", "Checkpoint"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.customLora, name: "LoRA Paths", type: "text", defaultValue: "", tooltip: "Optional extra LoRA roots. Use ';' or new lines.", category: ["Warp Studio", "Civitai Custom Paths", "LoRA"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.customVae, name: "VAE Paths", type: "text", defaultValue: "", tooltip: "Optional extra VAE roots. Use ';' or new lines.", category: ["Warp Studio", "Civitai Custom Paths", "VAE"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.customUnet, name: "UNet Paths", type: "text", defaultValue: "", tooltip: "Optional extra UNet roots. Use ';' or new lines.", category: ["Warp Studio", "Civitai Custom Paths", "UNet"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.customEmbedding, name: "Embedding Paths", type: "text", defaultValue: "", tooltip: "Optional extra embedding roots. Use ';' or new lines.", category: ["Warp Studio", "Civitai Custom Paths", "Embedding"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.matureMode, name: "Mature content", type: "combo", defaultValue: "show", options: [
        { value: "show", text: "Show everything" },
        { value: "blur", text: "Blur previews" },
        { value: "hide", text: "Hide mature models" },
      ], tooltip: "Blur hides the preview image only \u2014 titles stay readable so you can still identify a model. Hide removes mature models from results and reports how many were left out.", category: ["Warp Studio", "Civitai General", "Mature Content"], onChange: () => { scheduleSettingsSync(); onMatureModeChanged(); } },
    { id: SETTINGS.namingModel, name: "Naming model", type: "combo", defaultValue: "", options: (value) => namingModelOptions(value), tooltip: "The text encoder that Generate Text runs to propose the new file names. The list shows the files in your text_encoders folders.", category: ["Warp Studio", "Civitai Rename", "Naming Model"], onChange: (value) => {
      // Only a choice made here is sent: the backend picks the model itself after a download.
      state.namingModelDirty = String(value ?? "") !== state.syncedNamingModel;
      scheduleSettingsSync();
    } },
    { id: SETTINGS.fallbackCivarchive, name: "Look up previews on civarchive", type: "boolean", defaultValue: false, tooltip: "Files Civitai can't serve are looked up on civarchive.com by their SHA256 hash. Only the hash is sent.", category: ["Warp Studio", "Civitai Preview Fallback", "Civarchive"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.fallbackHuggingFace, name: "Hugging Face authors and repos", type: "text", defaultValue: "malcolmrey", tooltip: "Matched by file hash. Authors (malcolmrey) or repos (owner/name), separated by ';' or new lines. Leave empty to skip Hugging Face.", category: ["Warp Studio", "Civitai Preview Fallback", "Hugging Face"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.fallbackAiToolkitFolders, name: "ai-toolkit output folders", type: "text", defaultValue: "", tooltip: "Folders that hold your ai-toolkit training runs. Their sample images become previews for your own trainings. Use ';' or new lines.", category: ["Warp Studio", "Civitai Preview Fallback", "ai-toolkit Folders"], onChange: () => scheduleSettingsSync() },
    { id: SETTINGS.fallbackOwnCreator, name: "Creator of your own trainings", type: "text", defaultValue: "", category: ["Warp Studio", "Civitai Preview Fallback", "Own Creator"], onChange: () => scheduleSettingsSync() },
  ],
  async setup() {
    injectStyles();
    if (await hydrateSettingsFromBackend()) scheduleSettingsSync(true);
    // The naming-model setting lists the text encoders the backend found.
    getJson("/warp-studio/updater/rename/status")
      .then((status) => setNamingModels(status.namingModels))
      .catch(() => {});
    app.extensionManager.registerSidebarTab({
      id: TAB_ID,
      icon: TAB_ICON,
      title: "Warp Studio",
      tooltip: "Your models: gallery, updates and renaming",
      type: "custom",
      render: (el) => renderTab(el),
    });
    window.addEventListener("warpstudio:open-gallery", (ev) => openGallery(ev?.detail));
    // Refresh Node Definitions dropped the shared library: a Gallery on screen
    // reads it again, so a LoRA added since shows up without a page reload.
    window.addEventListener("warpstudio:library-changed", () => {
      if (state.galleryLoaded) state.galleryView?.refresh().catch(() => {});
    });
  },
});

async function renderTab(el) {
  el.innerHTML = "";
  if (state.noticeTimer) {
    clearTimeout(state.noticeTimer);
    state.noticeTimer = null;
    if (state.jobText === state.noticeText) state.jobText = "";
    state.noticeText = "";
  }
  const root = document.createElement("div");
  root.className = "cu-root";
  root.innerHTML = `
    <header class="cu-hero">
      <div class="cu-hero-title">Warp Studio</div>
      <button type="button" id="cu-gear" class="cu-gear" title="Settings" aria-label="Settings" aria-controls="cu-settings" aria-expanded="${state.settingsOpen}">⚙︎</button>
    </header>

    <section id="cu-settings" class="cu-card cu-settings-card"${state.settingsOpen ? "" : " hidden"}>
      <div class="cu-settings-body">
        <div class="cu-label">Model Scope</div>
        <div class="cu-row">${MODEL_TYPES.map((t) => `<label class="cu-chip" title="Include ${t} in jobs"><input type="checkbox" data-type="${t}"${state.scopeTypes.includes(t) ? " checked" : ""}><span>${t.charAt(0).toUpperCase() + t.slice(1)}</span></label>`).join("")}</div>
        <div class="cu-label">Options</div>
        <label class="cu-option">
          <input id="cu-rehash" type="checkbox"${state.forceRehash ? " checked" : ""}>
          <span>Force rehash</span>
          <span class="cu-info-trigger cu-tooltip" data-tooltip="Re-identify every model by recomputing its SHA256 hash. Use this after manually replacing model files — the cache won't know the file changed otherwise.">ⓘ</span>
        </label>
        <label class="cu-option">
          <input id="cu-refetch" type="checkbox"${state.refetchMetadata ? " checked" : ""}>
          <span>Refetch metadata that already exists</span>
          <span class="cu-info-trigger cu-tooltip" data-tooltip="Makes Fetch Missing Metadata re-pull info from Civitai for models that already have a .civitai.info file, instead of skipping them. Uses sidecar version IDs, so no re-hashing.">ⓘ</span>
        </label>
        <div class="cu-divider"></div>
        <div class="cu-head">
          <div class="cu-label">Resolved Roots</div>
          <button id="cu-roots-toggle" class="cu-text-btn">Show</button>
        </div>
        <div id="cu-roots-summary" class="cu-roots-sum"></div>
        <div id="cu-roots" class="cu-roots" style="display:none"></div>
      </div>
    </section>

    <section class="cu-card">
      <div id="cu-view-switch" class="cu-view-switch" role="tablist" aria-label="Results view"></div>
      <div id="cu-loading-bar" class="cu-loading-bar" aria-hidden="true"></div>
      <div id="cu-job-bar" class="cu-job-bar" hidden>
        <div id="cu-progress-wrap" class="cu-progress-wrap">
          <div class="cu-progress"><div id="cu-progress-fill" class="cu-progress-fill"></div></div>
          <span id="cu-progress-text" class="cu-progress-pct"></span>
          <span id="cu-progress-eta" class="cu-progress-eta"></span>
        </div>
        <div class="cu-job-row">
          <span id="cu-job-text" class="cu-job-text"></span>
          <button type="button" id="cu-notice-act" class="cu-btn cu-btn-sm" hidden>Check again</button>
          <div id="cu-job-controls" class="cu-job-controls" style="display:none">
            <button id="cu-pause" class="cu-btn cu-btn-sm" title="Pause or resume the current job">Pause</button>
            <button id="cu-stop" class="cu-btn cu-btn-sm cu-btn-danger" title="Abort the current job">Stop</button>
          </div>
        </div>
      </div>
      <div id="cu-status" class="cu-status" role="alert"></div>
      <div id="cu-gallery-tab">
        <div id="cu-tb-gallery" data-tb-tab="gallery"></div>
        <div id="cu-gallery-pane"></div>
      </div>
      <div id="cu-rename-tab" style="display:none">
        <div id="cu-tb-rename" data-tb-tab="rename"></div>
        <div id="cu-rename-pane"></div>
      </div>
      <div id="cu-updates-pane">
        <div id="cu-tb-updates" data-tb-tab="updates"></div>
        <div class="cu-action-bar">
          <button id="cu-check" class="cu-btn cu-btn-primary cu-tooltip" data-tooltip="Scan local files and compare with Civitai to check for newer versions.">Check for Updates</button>
          <div class="cu-more">
            <button id="cu-more" type="button" class="cu-btn cu-btn-ghost" aria-label="More" aria-haspopup="true" aria-expanded="false">⋯</button>
            <div id="cu-more-menu" class="cu-tb-menu cu-more-menu" hidden></div>
          </div>
        </div>
        <div id="cu-sidecar-warnings" class="cu-sidecar-warnings"></div>
        <div id="cu-scan-report" class="cu-scan-report"></div>
        <div id="cu-results" class="cu-results"></div>
        <div class="cu-pagination">
          <button id="cu-prev" class="cu-page-btn" disabled title="Previous page">&lsaquo;</button>
          <span id="cu-page" class="cu-page-info">1 / 1</span>
          <button id="cu-next" class="cu-page-btn" disabled title="Next page">&rsaquo;</button>
        </div>
      </div>
    </section>
  `;

  state.rootEl = root;
  state.sidecarWarningsEl = root.querySelector("#cu-sidecar-warnings");
  state.statusEl = root.querySelector("#cu-status");
  state.jobBarEl = root.querySelector("#cu-job-bar");
  state.jobTextEl = root.querySelector("#cu-job-text");
  state.gearEl = root.querySelector("#cu-gear");
  state.settingsEl = root.querySelector("#cu-settings");
  state.progressWrapEl = root.querySelector("#cu-progress-wrap");
  state.progressFillEl = root.querySelector("#cu-progress-fill");
  state.progressTextEl = root.querySelector("#cu-progress-text");
  state.progressEtaEl = root.querySelector("#cu-progress-eta");
  state.scanReportEl = root.querySelector("#cu-scan-report");
  state.viewSwitchEl = root.querySelector("#cu-view-switch");
  state.loadingBarEl = root.querySelector("#cu-loading-bar");
  state.resultsEl = root.querySelector("#cu-results");
  state.pageInfoEl = root.querySelector("#cu-page");
  state.prevEl = root.querySelector("#cu-prev");
  state.nextEl = root.querySelector("#cu-next");
  state.rootsSummaryEl = root.querySelector("#cu-roots-summary");
  state.rootsDetailsEl = root.querySelector("#cu-roots");
  state.pauseEl = root.querySelector("#cu-pause");
  state.stopEl = root.querySelector("#cu-stop");
  state.jobControlsEl = root.querySelector("#cu-job-controls");
  state.galleryTabEl = root.querySelector("#cu-gallery-tab");
  state.galleryPaneEl = root.querySelector("#cu-gallery-pane");
  state.renameTabEl = root.querySelector("#cu-rename-tab");
  state.renamePaneEl = root.querySelector("#cu-rename-pane");
  state.updatesPaneEl = root.querySelector("#cu-updates-pane");
  state.renameView = renderRenameView(state.renamePaneEl, {
    store: state.renameStore,
    getJson,
    postJson,
    escapeHtml,
    startJob: startPanelJob,
    activeJob: () => (state.currentJobId ? { jobId: state.currentJobId, type: state.currentJobType, status: state.currentJobStatus } : null),
    setStatus,
    adoptNamingModel,
    setNamingModels,
    reloadResults: reloadCheckResults,
    matureMode: () => state.matureMode,
    openPreview: openLightbox,
    copyOrReveal,
    copyPathTitle,
    filter: sharedFilter,
    onFacets: onRenameFacets,
    onCount: () => renderViewSwitch(),
    setLoading,
  });
  state.galleryLoaded = false;
  state.galleryView = renderGalleryView(state.galleryPaneEl, {
    escapeHtml,
    filter: sharedFilter,
    onFacets: onGalleryFacets,
    onCount: () => {
      renderViewSwitch();
      renderToolbars(); // "Filter · n" follows the downloadable count
    },
    app,
    query: () => state.galleryQuery,
    arrange: () => state.galleryArrange,
    stats: () => state.modelStats,
    includeDownloadable: () => state.includeDownloadable,
    downloads: () => state.downloads,
    canTrash: () => state.canTrash,
    onDownload: (name) => startDownload({ source: "gallery", name }, galleryKey(name)),
    onStop: (requestId) => stopDownload(requestId),
    onDelete: (name, kind) => deleteModel(name, kind),
  });
  bindEvents(root);
  setupToolbars();
  paintStatus();
  renderCacheInfo();
  renderRoots();
  renderSidecarWarnings();
  renderScanReport();
  renderFilters();
  renderResults();
  updateControlButtons();
  el.appendChild(root);
  const pending = state.pendingGallery;
  state.pendingGallery = null;
  if (pending) state.resultMode = "gallery";
  applyResultMode();
  if (pending) openedGallery(pending);
  // The saved dry run loads in the background, so the Rename tab shows its count.
  if (state.resultMode !== "rename") state.renameView.refresh().catch(() => {});

  if (!state.currentJobId && !state.checkJobId) {
    loadCachedResults();
  }
  // A reloaded panel finds a running download job and paints every card's state.
  loadDownloads()
    .then((jobId) => {
      if (jobId && !state.currentJobId) {
        attachToRunningJob({ jobId, type: MODEL_DOWNLOAD_JOB, status: "running" });
      }
      paintDownloads();
    })
    .catch(() => {});
}

/* After the renamer moved files: the check results (patched to the new paths on
   the server) are fetched again, so no row shows a file under its old name. */
async function reloadCheckResults() {
  if (state.currentJobId) return;
  if (state.checkJobId) await loadResultPage(true);
  else await loadCachedResults();
}

async function loadCachedResults() {
  // /last-check walks every model root and can take seconds. A job started
  // meanwhile owns the results view; binding to the cached record after that
  // would pin the list to the previous check's results for the whole run.
  // A download job does not: it leaves the last check on screen.
  const superseded = () =>
    Boolean((state.currentJobId && state.currentJobType !== MODEL_DOWNLOAD_JOB) || state.checkJobId);
  // Until the last check is back, the list shows that it is loading, not that it is empty.
  state.startupLoading = true;
  setLoading("startup", true);
  renderCacheInfo();
  renderResults();
  try {
    // Reconnect to any job that is still running server-side (scan or
    // check) so a page reload keeps progress and the pause/stop controls.
    const activeResp = await getJson("/warp-studio/updater/jobs/active").catch(() => ({ job: null }));
    if (superseded()) return;
    if (activeResp.job) {
      attachToRunningJob(activeResp.job);
      // A download job leaves the last check on screen; any other job owns the view.
      if (activeResp.job.type !== MODEL_DOWNLOAD_JOB) return;
    }

    const resp = await getJson("/warp-studio/updater/last-check");
    if (superseded()) return;
    state.sidecarWarnings = Array.isArray(resp.sidecarWarnings) ? resp.sidecarWarnings : [];
    renderSidecarWarnings();
    if (!resp.data) {
      if (resp.cacheInvalid) {
        setStatus("Cached results are from an older format. Run Check for Updates again.", "error");
      }
      return;
    }

    if (resp.data.inProgress) {
      // The active-job request failed but a check is still running: attach
      // to it through the id the last-check endpoint reports.
      attachToRunningJob({
        jobId: resp.data.jobId,
        type: "check-updates",
        status: "running",
        itemCount: resp.data.itemCount || 0,
        message: "running",
      });
      return;
    }

    state.cachedJobId = resp.data.jobId;
    state.checkJobId = resp.data.jobId;
    state.checkSummary = resp.data.summary || null;
    state.archiveInfo = resp.data.archive || null;
    state.archiveError = "";
    state.cachedAt = resp.data.checkedAt || null;
    state.cacheFilesChanged = resp.data.filesChanged
      ? { added: resp.data.filesAdded || 0, removed: resp.data.filesRemoved || 0 }
      : null;
    renderCacheInfo();
    state.pageOffset = 0;
    state.checkFacets = { modelTypes: [], baseModels: [] };
    applyFacets(mergedFacets());
    await loadResultPage(true);
  } catch (_) {
    // cache load is best-effort
  } finally {
    state.startupLoading = false;
    setLoading("startup", false);
    // No forced redraw: cards already drawn stay, so their entry animation
    // plays out. The loading note cleared the signature itself, so it is
    // still replaced.
    renderCacheInfo();
    renderResults();
  }
}

function attachToRunningJob(job) {
  state.currentJobId = job.jobId;
  state.currentJobType = job.type;
  state.currentJobStatus = job.status || "running";
  state.currentProgress = job.progress || 0;
  state.currentTotal = job.total || 0;
  state.currentItemCount = job.itemCount || 0;
  if (job.type === "check-updates") {
    state.checkJobId = job.jobId;
  }
  updateProgress(state.currentProgress, state.currentTotal, true);
  updateControlButtons();
  setStatus(`Reconnected \u2014 ${job.message || "running"}`);
  pollJob(job.jobId);
}

function clearCurrentJob() {
  state.currentJobId = null;
  state.currentJobType = null;
  state.currentJobStatus = null;
  state.currentSummary = null;
  state.currentProgress = 0;
  state.currentTotal = 0;
  state.currentItemCount = 0;
  state.stopRequested = false;
}

function isLostJobError(error) {
  return /job not found/i.test(String(error?.message || ""));
}

/* The server no longer knows the job the panel is showing: ComfyUI was
   restarted, or the record was pruned. Without this the panel keeps asking
   for a dead id and every request fails until the page is reloaded. Drop
   the stale ids and fall back to the on-disk cache, as a fresh load would. */
async function recoverFromLostJob() {
  if (state.recoveringJob) return;
  state.recoveringJob = true;
  try {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    clearCurrentJob();
    resetEta();
    state.checkJobId = null;
    state.cachedJobId = null;
    state.checkSummary = null;
    state.cachedAt = null;
    state.cacheFilesChanged = null;
    state.resultItems = [];
    state.resultTotal = 0;
    state.resultOffset = 0;
    state.pageOffset = 0;
    updateProgress(0, 0, false);
    updateControlButtons();
    renderCacheInfo();
    renderResults();
    setStatus("The server was restarted \u2014 reloading the last saved results.", "error");
    // A dry run the panel was following is gone too: unlock the Rename view.
    if (state.renameView) state.renameView.refresh();
    await loadCachedResults();
  } finally {
    state.recoveringJob = false;
  }
}

const SCAN_TOOLTIP =
  "Download missing .civitai.info sidecars and preview images. Files Civitai can't serve are looked up on the sources set under Preview Fallback in the settings. Files that already have metadata and a preview are skipped, and nothing is compared for updates.";

/* The ⋯ menu next to Check for Updates. It is filled each time it opens, so
   the items follow the archive folder setting and whether a job is running. */
function bindMoreMenu(root) {
  const button = root.querySelector("#cu-more");
  const menu = root.querySelector("#cu-more-menu");
  const doc = root.ownerDocument;
  const close = (refocus) => {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (refocus) button.focus();
  };
  const open = () => {
    const items = moreMenuItems({
      archiveFolder: state.savedArchiveFolder,
      busy: Boolean(state.currentJobId),
    });
    menu.innerHTML =
      `<div class="cu-filter-panel cu-more-panel">` +
      items
        .map((item) => {
          const title = item.key === "scan" ? ` title="${escapeHtml(SCAN_TOOLTIP)}"` : "";
          return `<button type="button" class="cu-more-item" data-more="${item.key}"${title}${item.disabled ? " disabled" : ""}>${escapeHtml(item.label)}</button>`;
        })
        .join("") +
      `</div>`;
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
  };
  button.addEventListener("click", () => (menu.hidden ? open() : close(false)));
  menu.addEventListener("click", (event) => {
    const item = event.target.closest && event.target.closest("[data-more]");
    if (!item || item.disabled) return;
    close(true);
    if (item.dataset.more === "scan") startJob("/warp-studio/updater/jobs/scan", "scan");
    else startJob("/warp-studio/updater/jobs/check-updates", "check-updates", { includeArchive: true });
  });
  const onDocClick = (event) => {
    if (!root.isConnected) return doc.removeEventListener("click", onDocClick);
    if (!menu.hidden && !event.target.closest("#cu-more, #cu-more-menu")) close(false);
  };
  const onKey = (event) => {
    if (!root.isConnected) return doc.removeEventListener("keydown", onKey);
    if (event.key === "Escape" && !menu.hidden) close(true);
  };
  doc.addEventListener("click", onDocClick);
  doc.addEventListener("keydown", onKey);
}

function bindEvents(root) {
  root.querySelector("#cu-check").addEventListener("click", async () => startJob("/warp-studio/updater/jobs/check-updates", "check-updates"));
  bindMoreMenu(root);
  root.querySelector("#cu-pause").addEventListener("click", async () => togglePauseResume());
  root.querySelector("#cu-stop").addEventListener("click", async () => stopCurrentJob());
  root.querySelector("#cu-roots-toggle").addEventListener("click", () => {
    state.rootsExpanded = !state.rootsExpanded;
    renderRoots();
  });
  // ComfyUI rebuilds the tab each time it is opened, so the job options are
  // kept in state rather than read back from the checkboxes.
  root.addEventListener("change", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLInputElement)) return;
    // A choice in a toolbar menu (Filter, Sort).
    const menu = target.closest("[data-tb-menu]");
    if (menu) {
      const tab = menu.closest("[data-tb-tab]")?.dataset.tbTab;
      if (tab) await onToolbarChange(tab, target);
      return;
    }
    if (target.dataset.type) {
      const type = target.dataset.type;
      state.scopeTypes = MODEL_TYPES.filter((t) => (t === type ? target.checked : state.scopeTypes.includes(t)));
    } else if (target.id === "cu-rehash") {
      state.forceRehash = target.checked;
    } else if (target.id === "cu-refetch") {
      state.refetchMetadata = target.checked;
    }
  });
  root.querySelector("#cu-prev").addEventListener("click", async () => {
    if (state.pageOffset <= 0) return;
    state.pageOffset = Math.max(0, state.pageOffset - state.pageSize);
    await loadResultPage(true);
  });
  root.querySelector("#cu-next").addEventListener("click", async () => {
    if (state.pageOffset + state.pageSize >= state.resultTotal) return;
    state.pageOffset += state.pageSize;
    await loadResultPage(true);
  });
  root.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest("#cu-notice-act")) {
      hideCachedNotice();
      state.forceNextRecheck = true;
      startJob("/warp-studio/updater/jobs/check-updates", "check-updates");
      return;
    }
    if (target.closest("#cu-gear")) {
      toggleSettings();
      return;
    }
    const selectAll = target.closest("[data-tb-all], [data-tb-none]");
    if (selectAll) {
      await onToolbarSelectAll(selectAll);
      return;
    }
    const toolbarAction = target.closest("[data-tb-act]");
    if (toolbarAction) {
      onToolbarAction(toolbarAction);
      return;
    }
    const viewTab = target.closest("[data-view]");
    if (viewTab) {
      const mode = viewTab.dataset.view;
      if (!mode || mode === state.resultMode) return;
      state.resultMode = mode;
      applyResultMode();
      if (mode === "rename" || mode === "gallery") return;
      // The filters stay: they serve every view.
      state.pageOffset = 0;
      await loadResultPage(true);
      restoreScroll(mode);
      return;
    }
    const dlButton = target.closest("[data-dl-action]");
    if (dlButton && dlButton.closest(".cu-dl")) {
      const slot = dlButton.closest(".cu-dl");
      const action = dlButton.dataset.dlAction;
      if (action === "stop") {
        await stopDownload(dlButton.dataset.requestId || "");
      } else if (action === "download" || action === "replace") {
        await startDownload(
          {
            source: "updates",
            jobId: state.checkJobId,
            modelId: slot.dataset.modelId,
            versionId: slot.dataset.versionId,
            modelPath: slot.dataset.modelPath,
            replace: action === "replace",
          },
          slot.dataset.dlKey,
        );
      }
      return;
    }
    const rowDelete = target.closest('[data-row-action="delete"]');
    if (rowDelete) {
      await onRowDelete(rowDelete);
      return;
    }
    if (target.dataset.archiveAction) {
      const modelId = target.dataset.modelId || "";
      const versionId = target.dataset.versionId || "";
      if (!modelId || !versionId) return;
      await toggleArchivedUpdate(modelId, versionId, target.dataset.archiveAction === "restore");
      return;
    }
    const groupHead = target.closest("[data-group-key]");
    if (groupHead) {
      const key = groupHead.dataset.groupKey || "";
      if (!key) return;
      if (state.collapsed.has(key)) state.collapsed.delete(key);
      else state.collapsed.add(key);
      state.pageOffset = 0;
      await loadResultPage(true);
    }
  });
}

/* Check for Updates with fresh results shows them without a check. Say so, for a
   few seconds, in the job bar's row, with a way to check anyway. */
function showCachedNotice() {
  const bar = state.jobBarEl;
  if (!bar || state.currentJobId) return;
  hideCachedNotice();
  bar.hidden = false;
  if (state.progressWrapEl) state.progressWrapEl.hidden = true;
  const text = `Results from ${timeAgo(state.cachedAt)}`;
  state.noticeText = text;
  state.jobText = text;
  paintStatus();
  const act = bar.querySelector("#cu-notice-act");
  if (act) act.hidden = false;
  state.noticeTimer = setTimeout(hideCachedNotice, 5000);
}

function hideCachedNotice() {
  if (state.noticeTimer) clearTimeout(state.noticeTimer);
  const wasShown = Boolean(state.noticeTimer);
  state.noticeTimer = null;
  if (!wasShown) return;
  const act = state.jobBarEl?.querySelector("#cu-notice-act");
  if (act) act.hidden = true;
  if (state.progressWrapEl) state.progressWrapEl.hidden = false;
  // Only the notice's own text goes; a status set since then stays.
  if (state.jobText === state.noticeText) {
    state.jobText = "";
    paintStatus();
  }
  state.noticeText = "";
  if (state.jobBarEl && !state.currentJobId) state.jobBarEl.hidden = true;
}

/* The ⚙ button: the settings open and close above the tabs. */
function toggleSettings() {
  state.settingsOpen = !state.settingsOpen;
  if (state.settingsEl) state.settingsEl.hidden = !state.settingsOpen;
  state.gearEl?.setAttribute("aria-expanded", String(state.settingsOpen));
}

/* ---- Toolbars: one for the Gallery, one shared by Updates and Issues, one for Rename ---- */

const TOOLBAR_TABS = ["gallery", "updates", "rename"];

// Filters that narrow what a tab shows. Types and Bases are one selection for every tab.
function isNarrowed(selection, options) {
  return options.length > 0 && Array.isArray(selection) && !selectsAll(selection, options);
}

function filterCount(tab) {
  let count = 0;
  // The Gallery shows LoRAs, models and embeddings alike: Types only narrow the other tabs.
  if (tab !== "gallery" && isNarrowed(state.filterTypes, state.facets.modelTypes)) count += 1;
  if (isNarrowed(state.filterBases, state.facets.baseModels)) count += 1;
  if (tab === "updates" && state.showHidden) count += 1;
  if (tab === "gallery" && state.includeDownloadable && downloadableCount() > 0) count += 1;
  return count;
}

const downloadableCount = () => state.galleryView?.downloadableCount?.() || 0;

function sortLabel(tab) {
  if (tab === "gallery") {
    return GALLERY_SORTS.find((o) => o.value === state.galleryArrange.sort)?.label || "Name";
  }
  if (tab === "rename") return null;
  return SORT_OPTIONS.find((o) => o.value === state.sortOrder)?.label || "Name";
}

function toolbarProps(tab) {
  return {
    query: tab === "gallery" ? state.galleryQuery : "",
    filtered: filterCount(tab),
    sortLabel: sortLabel(tab),
    info: tab === "updates",
    search: tab === "gallery",
  };
}

/* Draws each tab's toolbar once; later changes patch its labels in place
   (renderToolbars), so typing in the search field never rebuilds anything. */
function setupToolbars() {
  for (const wiring of Object.values(state.toolbarWiring)) wiring.destroy();
  state.toolbarWiring = {};
  for (const tab of TOOLBAR_TABS) {
    const root = state.rootEl?.querySelector(`#cu-tb-${tab}`);
    if (!root) continue;
    state.toolbarEls[tab] = root;
    root.innerHTML = toolbarHtml(toolbarProps(tab), escapeHtml);
    state.toolbarWiring[tab] = wireToolbar(root, {
      onQuery: (value) => onToolbarQuery(tab, value),
      onOpen: (name) => fillToolbarMenu(tab, name),
    });
  }
}

function renderToolbars() {
  for (const tab of TOOLBAR_TABS) {
    const root = state.toolbarEls[tab];
    if (root) updateToolbar(root, toolbarProps(tab), escapeHtml);
  }
  refreshOpenMenus();
}

function onToolbarQuery(tab, value) {
  if (tab !== "gallery") return;
  state.galleryQuery = String(value || "").trim();
  state.galleryView?.setQuery(state.galleryQuery);
  renderToolbars();
}

function typeOptions() {
  const selected = state.filterTypes || [];
  return state.facets.modelTypes.map((value) => ({ value, label: capitalize(value), checked: selected.includes(value) }));
}

function baseOptions() {
  const selected = state.filterBases || [];
  return state.facets.baseModels.map((value) => ({ value, label: value, checked: selected.includes(value) }));
}

function menuSections(tab, name) {
  if (name === "filter") {
    const types = { kind: "checks", key: "types", title: "Types", options: typeOptions(), actions: true };
    const bases = { kind: "checks", key: "bases", title: "Bases", options: baseOptions(), actions: true };
    if (tab === "gallery") {
      const sections = [bases];
      if (downloadableCount() > 0) {
        sections.push({ kind: "toggle", key: "includeDownloadable", label: "Include downloadable", checked: state.includeDownloadable });
      }
      return sections;
    }
    if (tab === "rename") return [types, bases];
    return [types, bases, { kind: "toggle", key: "showHidden", label: "Show hidden", checked: state.showHidden }];
  }
  if (tab === "gallery") {
    return [
      { kind: "choice", key: "groupBy", title: "Group by", options: GALLERY_GROUPS, value: state.galleryArrange.groupBy },
      { kind: "choice", key: "sort", title: "Sort", options: GALLERY_SORTS, value: state.galleryArrange.sort },
    ];
  }
  const sections = [{ kind: "choice", key: "groupBy", title: "Group", options: GROUP_OPTIONS, value: state.groupBy }];
  if (state.groupBy !== "none") {
    // The same grouping twice means nothing, so the first choice is not offered again.
    sections.push({ kind: "choice", key: "thenBy", title: "Then", options: GROUP_OPTIONS.filter((o) => o.value !== state.groupBy), value: state.thenBy });
  }
  sections.push({ kind: "choice", key: "sort", title: "Sort", options: SORT_OPTIONS, value: state.sortOrder });
  sections.push({ kind: "choice", key: "pageSize", title: "Per page", options: PAGE_SIZES.map((v) => ({ value: String(v), label: String(v) })), value: String(state.pageSize) });
  return sections;
}

/* What the ⓘ popover says: when the last check ran, what it found, whether files changed. */
function infoLines() {
  if (state.startupLoading && !state.cachedAt) return ["Loading the last check…"];
  if (!state.checkJobId) return ["No update check has run yet."];
  const lines = [];
  if (state.cachedAt) lines.push(`Last checked ${timeAgo(state.cachedAt)}`);
  if (state.noticeTimer && state.cachedAt) lines.push(`Showing the saved results from ${timeAgo(state.cachedAt)}`);
  if (state.checkSummary) {
    lines.push(summaryLine(state.checkSummary));
  } else if (state.currentJobType === "check-updates") {
    lines.push(state.resultItems.length
      ? "Check in progress — previous results are shown until each model is re-checked."
      : "Check in progress…");
  }
  // An archive that could not be checked keeps its last results: both are said.
  if (state.archiveError) lines.push(state.archiveError);
  if (state.archiveInfo?.checkedAt) {
    const found = state.archiveInfo.withUpdates || 0;
    lines.push(`Archive checked ${timeAgo(state.archiveInfo.checkedAt)} · ${found} with new versions`);
  }
  const dirty = state.cacheFilesChanged;
  if (dirty) {
    const parts = [];
    if (typeof dirty === "object") {
      if (dirty.added) parts.push(`${dirty.added} added`);
      if (dirty.removed) parts.push(`${dirty.removed} removed`);
    }
    lines.push(parts.length ? `Changes detected (${parts.join(", ")})` : "Changes detected");
  }
  return lines;
}

/* A check that included the archive reports how it went in its summary (see
   archiveResult). A plain check says nothing new, so the last result stays. */
function takeArchiveResult(summary) {
  const next = archiveResult(summary, { info: state.archiveInfo, error: state.archiveError });
  state.archiveInfo = next.info;
  state.archiveError = next.error;
}

function infoPopoverHtml() {
  const action = state.checkJobId && !state.startupLoading
    ? { label: "Check again", key: "recheck", disabled: Boolean(state.currentJobId) }
    : null;
  return infoHtml(infoLines(), action, escapeHtml);
}

/* Fills one menu of one toolbar. A menu whose content did not change is left
   alone, so a poll tick never replaces the control the reader is on; when it
   did change, focus goes back to the same place in it. */
function fillToolbarMenu(tab, name) {
  const slot = state.toolbarEls[tab]?.querySelector(`[data-tb-menu="${name}"]`);
  if (!slot) return;
  const html = name === "info" ? infoPopoverHtml() : menuHtml(menuSections(tab, name), escapeHtml);
  // Radio names differ on every call; they say nothing about the content.
  const signature = html.replace(/ name="[^"]*"/g, "");
  if (slot.dataset.sig === signature) return;
  const controls = () => [...slot.querySelectorAll("input, button")];
  const at = slot.contains(document.activeElement) ? controls().indexOf(document.activeElement) : -1;
  slot.innerHTML = html;
  slot.dataset.sig = signature;
  if (at >= 0) controls()[at]?.focus({ preventScroll: true });
}

function refreshOpenMenus() {
  for (const tab of TOOLBAR_TABS) {
    const root = state.toolbarEls[tab];
    if (!root) continue;
    for (const slot of root.querySelectorAll("[data-tb-menu]")) {
      if (!slot.hidden) fillToolbarMenu(tab, slot.dataset.tbMenu);
    }
  }
}

/* A change in a Filter or Sort menu. */
async function onToolbarChange(tab, input) {
  const key = input.dataset.key || "";
  const value = input.dataset.value ?? "";
  if (key === "types" || key === "bases") {
    const selection = key === "types" ? "filterTypes" : "filterBases";
    const selected = new Set(state[selection] || []);
    if (input.checked) selected.add(value);
    else selected.delete(value);
    state[selection] = [...selected];
    await onFiltersChanged();
  } else if (key === "showHidden") {
    state.showHidden = Boolean(input.checked);
    state.pageOffset = 0;
    renderToolbars();
    await loadResultPage(true);
  } else if (key === "includeDownloadable") {
    state.includeDownloadable = Boolean(input.checked);
    renderToolbars();
    state.galleryView?.rerender();
  } else if (tab === "gallery") {
    if (key !== "groupBy" && key !== "sort") return;
    state.galleryArrange = { ...state.galleryArrange, [key]: value };
    writeGalleryArrange(state.galleryArrange);
    renderToolbars();
    state.galleryView?.render();
  } else if (key === "pageSize") {
    const next = Number(value || PAGE_SIZES[0]);
    state.pageSize = PAGE_SIZES.includes(next) ? next : PAGE_SIZES[0];
    state.pageOffset = 0;
    renderToolbars();
    await loadResultPage(true);
  } else if (key === "sort" || key === "groupBy" || key === "thenBy") {
    if (key === "sort") {
      state.sortOrder = value;
    } else if (key === "groupBy") {
      state.groupBy = value;
      if (state.thenBy === state.groupBy) state.thenBy = "none";
    } else {
      state.thenBy = value;
    }
    state.pageOffset = 0;
    state.collapsed.clear();
    renderArrange();
    await loadResultPage(true);
  }
}

/* Select all / Deselect all in the Types or Bases menu. */
async function onToolbarSelectAll(button) {
  const all = button.dataset.tbAll !== undefined;
  const key = all ? button.dataset.tbAll : button.dataset.tbNone;
  const selection = key === "types" ? "filterTypes" : "filterBases";
  const facet = key === "types" ? "modelTypes" : "baseModels";
  state[selection] = all ? [...(state.facets[facet] || [])] : [];
  await onFiltersChanged();
}

/* A button inside the ⓘ popover. */
function onToolbarAction(button) {
  if (button.dataset.tbAct !== "recheck") return;
  state.toolbarWiring.updates?.close();
  state.forceNextRecheck = true;
  startJob("/warp-studio/updater/jobs/check-updates", "check-updates");
}

async function startJob(endpoint, type, { includeArchive = false } = {}) {
  if (state.currentJobId) {
    setStatus("A job is already running.", "error");
    return;
  }
  const modelTypes = selectedTypes();
  if (!modelTypes.length) {
    setStatus("Select at least one model type in the \u2699 settings.", "error");
    return;
  }
  const forceRehash = state.forceRehash;
  const payload = {
    modelTypes,
    refetchMetadata: state.refetchMetadata,
    forceRehash,
  };

  if (includeArchive) payload.includeArchive = true;

  if (type === "check-updates" && !includeArchive && !state.forceNextRecheck && !state.cacheFilesChanged && !forceRehash && state.cachedAt
      && coversSameScope(modelTypes)) {
    const ttl = Number(getSetting(SETTINGS.cacheTtlMinutes, 240)) * 60 * 1000;
    const age = Date.now() - new Date(state.cachedAt).getTime();
    if (ttl > 0 && age < ttl && state.cachedJobId) {
      // Cache is still fresh — load the cached results without re-running.
      state.checkJobId = state.cachedJobId;
      state.pageOffset = 0;
      state.checkFacets = { modelTypes: [], baseModels: [] };
      state.facets = mergedFacets();
      renderFilters();
      await loadResultPage(true);
      renderCacheInfo();
      showCachedNotice();
      return;
    }
  }
  state.forceNextRecheck = false;
  clearError();
  hideCachedNotice();

  try {
    const data = await postJson(endpoint, payload);
    state.currentJobId = data.jobId;
    state.currentJobType = type;
    state.currentJobStatus = "queued";
    state.currentSummary = null;
    state.currentProgress = 0;
    state.currentTotal = 0;
    state.currentItemCount = 0;
    state.lastStatus = "";
    state.lastItemCount = -1;
    resetEta();
    if (type === "check-updates") {
      state.checkJobId = data.jobId;
      state.cachedJobId = data.jobId;
      state.checkSummary = null;
      state.freshPaths.clear();
      state.pageOffset = 0;
      state.resultItems = [];
      state.resultTotal = 0;
      state.resultOffset = 0;
      state.checkFacets = { modelTypes: [], baseModels: [] };
      state.facets = mergedFacets();
      renderFilters();
    }
    renderResults();
    updateProgress(0, 0, true);
    updateControlButtons();
    setStatus(type === "check-updates" ? "Scanning files and checking updates\u2026" : "Scanning files and refreshing metadata\u2026");
    pollJob(data.jobId);
    if (type === "check-updates") {
      // The job is seeded with the previous results \u2014 show them right away.
      await loadResultPage(true);
    }
  } catch (error) {
    setStatus(`Failed to start job: ${error.message}`, "error");
  }
}

/* Saved results answer a check only for the model types that check covered:
   narrowing or widening Model Scope asks for a new one. */
function coversSameScope(modelTypes) {
  const lastTypes = Array.isArray(state.checkSummary?.modelTypes) ? state.checkSummary.modelTypes : MODEL_TYPES;
  return lastTypes.length === modelTypes.length && modelTypes.every((type) => lastTypes.includes(type));
}

async function togglePauseResume() {
  if (!state.currentJobId) return;
  try {
    if (state.currentJobStatus === "paused") {
      await postJson(`/warp-studio/updater/jobs/${state.currentJobId}/resume`, {});
      setStatus("Job resumed.");
    } else {
      await postJson(`/warp-studio/updater/jobs/${state.currentJobId}/pause`, {});
      setStatus("Job paused.");
    }
  } catch (error) {
    setStatus(`Failed to pause/resume: ${error.message}`, "error");
  }
}

async function stopCurrentJob() {
  if (!state.currentJobId) return;
  try {
    state.stopRequested = true;
    await postJson(`/warp-studio/updater/jobs/${state.currentJobId}/stop`, {});
    setStatus("Stopping\u2026");
  } catch (error) {
    state.stopRequested = false;
    setStatus(`Failed to stop: ${error.message}`, "error");
  }
}

function pollJob(jobId) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  // Download records seen as done, so the list reloads once per finished download.
  let seenDone = null;
  let consecutiveFailures = 0;
  // A slow tick must not overlap the next one: two ticks can both see the
  // job finish and run the completion path twice.
  let tickInFlight = false;
  state.pollTimer = setInterval(async () => {
    if (tickInFlight) return;
    tickInFlight = true;
    try {
      const job = await getJson(`/warp-studio/updater/jobs/${jobId}`);
      // A new job was bound while this tick waited: it owns the state now.
      if (state.currentJobId !== jobId) return;
      if (consecutiveFailures) clearError(); // the "Reconnecting" notice is over
      consecutiveFailures = 0;
      const status = job.status || "running";
      const progress = Number(job.progress || 0);
      const total = Number(job.total || 0);
      const itemCount = Number(job.itemCount || 0);

      state.currentJobStatus = status;
      state.currentSummary = job.summary || null;
      state.currentMessage = String(job.message || "");
      if (Array.isArray(job.summary?.sidecarWarnings)) {
        state.sidecarWarnings = job.summary.sidecarWarnings;
        renderSidecarWarnings();
      }
      state.currentProgress = progress;
      state.currentTotal = total;
      state.currentItemCount = itemCount;
      sampleEta(progress, total, status);
      updateProgress(progress, total, true);
      renderProgressCounts();
      updateControlButtons();

      const statusChanged = status !== state.lastStatus;
      const countChanged = itemCount !== state.lastItemCount;

      if (state.currentJobType === "check-updates") {
        if (job.summary && Object.keys(job.summary).length > 0) state.checkSummary = job.summary;
        takeArchiveResult(job.summary);
        if (countChanged) {
          for (const item of state.resultItems) {
            const path = (item.localVersions || [])[0]?.modelPath || "";
            if (path && item.isProvisional) state.freshPaths.add(String(path).toLowerCase());
          }
        }
        // A quiet, in-place reload, and only when something changed. A re-checked
        // card keeps the count, so an unchanged count still refreshes now and then.
        const onPage1 = state.pageOffset === 0;
        const running = status === "running" || status === "queued" || status === "paused";
        const stale = Date.now() - (state.lastQuietReloadAt || 0) >= QUIET_RELOAD_MS;
        if ((statusChanged || countChanged || (running && onPage1 && stale)) && isTabVisible()) {
          state.lastQuietReloadAt = Date.now();
          await loadResultPage(false, { quiet: true });
        }
      }

      if (state.currentJobType === "scan" && job.summary && Object.keys(job.summary).length > 0) {
        state.scanSummary = job.summary;
        state.scanHint = "Run Check for Updates to see available updates.";
        renderScanReport();
      }

      state.lastStatus = status;
      state.lastItemCount = itemCount;

      const renameJob = RENAME_JOB_TYPES.includes(state.currentJobType);
      const scanJob = state.currentJobType === "scan";
      const downloadJob = state.currentJobType === MODEL_DOWNLOAD_JOB;
      const finished = ["completed", "failed", "cancelled"].includes(status);
      if (downloadJob && !finished) {
        // Each tick paints the cards in place; the list reloads only when a download finished.
        await loadDownloads();
        paintDownloads();
        const done = new Set([...state.downloads.values()].filter((r) => r.state === "done").map((r) => r.id));
        const newlyDone = seenDone !== null && [...done].some((id) => !seenDone.has(id));
        seenDone = done;
        if (newlyDone) await refreshAfterAction();
      }
      if (renameJob && !finished && state.renameView) state.renameView.onJob(job, false);

      if (finished) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        if (state.currentJobType === "check-updates") {
          // The finished job is the source of truth for the list, whatever
          // the view was bound to while it ran.
          state.checkJobId = jobId;
          state.cachedJobId = jobId;
          // A stopped or failed check holds partial results: the next click
          // must run a check instead of serving them as the cache.
          if (status !== "completed") state.forceNextRecheck = true;
          if (job.summary && Object.keys(job.summary).length > 0) state.checkSummary = job.summary;
          takeArchiveResult(job.summary);
          await loadResultPage(true);
          loadModelStats();
        }
        if (state.currentJobType === "scan") {
          if (job.summary && Object.keys(job.summary).length > 0) state.scanSummary = job.summary;
          // Cached check results predate the pictures this scan found: the next click runs a check.
          if (job.summary?.previewsFound > 0) state.forceNextRecheck = true;
          state.scanHint = "Run Check for Updates to see available updates.";
          renderScanReport();
          renderResults();
        }
        if (status === "failed") {
          const msg = Array.isArray(job.errors) && job.errors.length ? job.errors[0] : "Unknown error";
          setStatus(`Failed: ${msg}`, "error");
        } else if (status === "cancelled") {
          setStatus("Job cancelled");
        } else if (renameJob) {
          setStatus("Done");
        } else if (downloadJob) {
          setStatus(finishedText(job.summary));
        } else if (state.currentJobType === "check-updates") {
          state.cachedAt = new Date().toISOString();
          state.cacheFilesChanged = null;
          renderCacheInfo();
          // An archive that could not be checked is said here too, not only in the info popover.
          for (const [text, level] of checkDoneStatus(job.summary)) setStatus(text, level);
        } else {
          setStatus("Scan complete");
        }
        resetEta();
        updateProgress(progress, total, false);
        clearCurrentJob();
        updateControlButtons();
        // After the job is cleared, so the view sees no active job while it reloads.
        if (renameJob && state.renameView) await state.renameView.onJob(job, true);
        // A rename or a scan can change the library: the gallery reads it again, once it has loaded.
        if ((renameJob || scanJob) && state.galleryLoaded) state.galleryView?.refresh();
        if (downloadJob) {
          await loadDownloads().catch(() => {});
          paintDownloads();
          await refreshAfterAction();
        }
      }
    } catch (error) {
      if (isLostJobError(error)) {
        // A 404 for the job means the server came back without it.
        await recoverFromLostJob();
        return;
      }
      consecutiveFailures++;
      if (consecutiveFailures < 5) {
        setStatus(`Reconnecting... (Attempt ${consecutiveFailures}/5)`, "error");
        return;
      }
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      clearCurrentJob();
      // Forget the result ids too, so reopening the tab reloads from the
      // cache instead of asking a restarted server for a job it never had.
      state.checkJobId = null;
      state.cachedJobId = null;
      resetEta();
      updateProgress(0, 0, false);
      updateControlButtons();
      setStatus(`Lost contact with the server (${error.message}). Reopen the panel to reload results.`, "error");
    } finally {
      tickInFlight = false;
    }
  }, POLL_MS);
}

async function loadResultPage(force, { quiet = false } = {}) {
  const requestSeq = ++state.resultRequestSeq;
  // The Rename and Gallery views have no result pages; switching back loads
  // them. The Updates and Issues counts (and their filter options) still load.
  if (state.resultMode === "rename" || state.resultMode === "gallery") {
    await loadModeCounts();
    return;
  }
  if (!state.checkJobId) {
    renderResults();
    return;
  }
  if (!isTabVisible() && !force) return;
  if ((state.facets.modelTypes.length && Array.isArray(state.filterTypes) && state.filterTypes.length === 0) || (state.facets.baseModels.length && Array.isArray(state.filterBases) && state.filterBases.length === 0)) {
    state.resultItems = [];
    state.resultTotal = 0;
    state.resultOffset = 0;
    renderResults();
    return;
  }
  // A refresh during a check is quiet: the thin bar stays off, so nothing twitches.
  if (!quiet) setLoading("results", true);
  try {
    const query = new URLSearchParams({
      offset: String(state.pageOffset),
      limit: String(state.pageSize),
      mode: state.resultMode,
      sort: state.sortOrder || "name",
      showHidden: state.showHidden ? "1" : "0",
      groupBy: state.groupBy || "none",
      thenBy: state.thenBy || "none",
      mature: state.matureMode || "show",
    });
    const filters = filterParams();
    for (const [key, value] of filters) query.append(key, value);
    for (const key of state.collapsed) query.append("collapsed", key);
    const data = await getJson(`/warp-studio/updater/jobs/${state.checkJobId}/items?${query.toString()}`);
    if (requestSeq !== state.resultRequestSeq) return;
    state.resultItems = Array.isArray(data.items) ? data.items : [];
    state.resultTotal = Number(data.totalItems || 0);
    state.resultOffset = Number(data.offset || 0);
    state.pageOffset = state.resultOffset;
    state.groups = Array.isArray(data.groups) ? data.groups : [];
    state.startsMidPrimary = Boolean(data.startsMidPrimary);
    state.startsMidSecondary = Boolean(data.startsMidSecondary);
    state.matureHidden = Number(data.matureHidden || 0);
    if (data.modeCounts) state.modeCounts = data.modeCounts;
    if (data.facets) {
      state.checkFacets = data.facets;
      applyFacets(mergedFacets());
      renderFilters();
    }
    // When every selected type or base drops out of the options (say, the
    // last model with it was hidden), the selection falls back to all of
    // them. This page was fetched under the old filter and comes back empty,
    // so fetch it again under the one the menus now show.
    if (JSON.stringify(filterParams()) !== JSON.stringify(filters)) {
      state.pageOffset = 0;
      await loadResultPage(true, { quiet });
      return;
    }
    if (state.resultTotal > 0 && state.resultOffset >= state.resultTotal) {
      state.pageOffset = Math.max(0, Math.floor((state.resultTotal - 1) / state.pageSize) * state.pageSize);
      await loadResultPage(true, { quiet });
      return;
    }
    renderResults();
  } catch (error) {
    if (isLostJobError(error)) {
      await recoverFromLostJob();
      return;
    }
    setStatus(`Failed to fetch result page: ${error.message}`, "error");
  } finally {
    if (!quiet) setLoading("results", false);
  }
}

function summaryLine(s) {
  // Only counts that carry information: a zero for hidden/not-found/errors is
  // noise, and dropping it keeps the line on one row in a narrow sidebar.
  const parts = [`${s.total || 0} checked`, `${s.withUpdates || 0} updates`];
  if (s.hiddenUpdates) parts.push(`${s.hiddenUpdates} hidden`);
  if (s.notFound) parts.push(`${s.notFound} not found`);
  if (s.errors) parts.push(`${s.errors} errors`);
  return parts.join(" \u00b7 ");
}

function renderProgressCounts() {
  if (state.stopRequested && state.currentJobId) {
    setStatus("Stopping\u2026");
    return;
  }
  if (RENAME_JOB_TYPES.includes(state.currentJobType) || state.currentJobType === MODEL_DOWNLOAD_JOB) {
    // Dry runs, applies and the download report their step as the job message.
    setStatus(state.currentMessage || `Processing ${state.currentProgress} of ${state.currentTotal}`);
    return;
  }
  const s = state.currentSummary;
  if (!s || !s.mode) {
    if (state.currentJobId) {
      setStatus(`Processing ${state.currentProgress} of ${state.currentTotal}`);
    }
    return;
  }
  if (s.mode === "scan") {
    setStatus(`Scan: ${scanCounts(s)}`);
    return;
  }
  setStatus(summaryLine(s));
}

function scanCounts(s) {
  const found = s.previewsFound > 0 ? ` \u00b7 ${s.previewsFound} found elsewhere` : "";
  return `${s.total || 0} total \u00b7 ${s.refreshed || 0} refreshed \u00b7 ${s.skipped || 0} skipped \u00b7 ${s.notFound || 0} not found \u00b7 ${s.errors || 0} errors${found}`;
}

/* The ⓘ popover of Updates and Issues: when it was last checked, what it found,
   whether files changed. Filled when it opens; kept current while it is open. */
function renderCacheInfo() {
  const slot = state.toolbarEls.updates?.querySelector('[data-tb-menu="info"]');
  if (slot && !slot.hidden) fillToolbarMenu("updates", "info");
}

function renderSidecarWarnings() {
  if (!state.sidecarWarningsEl) return;
  const warnings = Array.isArray(state.sidecarWarnings) ? state.sidecarWarnings : [];
  if (!warnings.length) {
    state.sidecarWarningsEl.innerHTML = "";
    state.sidecarWarningsEl.style.display = "none";
    return;
  }

  const label = `${warnings.length} invalid sidecar${warnings.length === 1 ? "" : "s"} ignored`;
  const rows = warnings.map((warning) => {
    const path = String(warning?.path || "");
    const name = extractFilename(path) || "Unknown sidecar";
    const message = String(warning?.message || "This sidecar is invalid and was ignored.");
    return `
      <div class="cu-sidecar-warning-row">
        <div class="cu-sidecar-warning-name">${escapeHtml(name)}</div>
        <div class="cu-sidecar-warning-message">${escapeHtml(message)}</div>
        <code class="cu-sidecar-warning-path">${escapeHtml(path)}</code>
      </div>
    `;
  }).join("");

  state.sidecarWarningsEl.innerHTML = `
    <details class="cu-warning-panel">
      <summary><span aria-hidden="true">⚠</span> ${escapeHtml(label)}</summary>
      <div class="cu-sidecar-warning-list">${rows}</div>
    </details>
  `;
  state.sidecarWarningsEl.style.display = "";
}

function renderScanReport() {
  if (!state.scanReportEl) return;
  if (!state.scanSummary) {
    state.scanReportEl.innerHTML = "";
    state.scanReportEl.classList.remove("is-visible");
    return;
  }
  state.scanReportEl.classList.add("is-visible");
  const s = state.scanSummary;
  state.scanReportEl.innerHTML = `<div class="cu-small"><strong>Last Scan</strong> \u00b7 ${scanCounts(s)}<br>${escapeHtml(state.scanHint || "")}</div>`;
}

function buildResultCard(item, cardIndex) {
  const card = document.createElement("article");
  card.className = "cu-item";
  card.style.setProperty("--cu-i", String(Math.min(cardIndex, 12)));
  if (item.nsfw && state.matureMode === "blur") card.dataset.mature = "blur";
  const path = String((item.localVersions || [])[0]?.modelPath || "").toLowerCase();
  // Settle only once the card is final; a card still waiting for its
  // re-check stays in the set until it is.
  if (path && !item.isProvisional && state.freshPaths.has(path)) {
    card.classList.add("cu-settled");
    state.freshPaths.delete(path);
  }
  const issuesView = state.resultMode === "issues";
  const localVersions = item.localVersions || [];
  const newVersions = issuesView ? [] : (item.newVersions || []);
  const hiddenVersions = !issuesView && state.showHidden ? (item.hiddenNewVersions || []) : [];
  const firstPath = localVersions.length ? localVersions[0].modelPath : "";
  const displayName = item.modelName ? escapeHtml(item.modelName) : escapeHtml(extractFilename(firstPath || "unknown"));

  const typePill = item.modelType ? `<span class="cu-type-pill" data-type="${escapeHtml(item.modelType)}">${escapeHtml(capitalize(item.modelType))}</span>` : "";
  const creatorHtml = item.creatorName ? `<span class="cu-creator">by ${escapeHtml(item.creatorName)}</span>` : "";
  const provisionalHtml = item.isProvisional ? `<span class="cu-provisional">Provisional</span>` : "";

  // One list, newest on top, in the order the server sends. A server not yet
  // restarted sends none; its releases are all newer than its saved files.
  const lists = { saved: localVersions, new: newVersions, hidden: hiddenVersions };
  const order = (item.versionOrder || [
    ...newVersions.map((_, i) => ["new", i]),
    ...hiddenVersions.map((_, i) => ["hidden", i]),
    ...localVersions.map((_, i) => ["saved", i]),
  ]).filter(([kind, index]) => lists[kind]?.[index]);
  const versionRows = order.map(([kind, index], position) => {
    const v = lists[kind][index];
    if (kind !== "saved") return renderRemoteVersionRow(item, v, kind === "hidden");
    // Files of one model usually fail for one reason; print it once, under the
    // last row of each run that shares it.
    const [nextKind, nextIndex] = order[position + 1] || [];
    const next = nextKind === "saved" ? localVersions[nextIndex] : null;
    const showMessage = !next || !rowIssue(next) || issueMessage(next) !== issueMessage(v);
    return renderLocalVersionRow(v, showMessage);
  }).join("");

  const localPath = localVersions.find((v) => v.hasLocalPreview)?.modelPath || "";
  let thumbHtml;
  if (item.previewUrl && item.previewType === "video") {
    thumbHtml = `<video src="${escapeHtml(item.previewUrl)}#t=0.5" preload="metadata" muted playsinline></video>`;
  } else if (item.previewUrl) {
    thumbHtml = `<img src="${escapeHtml(item.previewUrl)}" alt="" loading="lazy">`;
  } else if (localPath) {
    thumbHtml = `<img alt="">`;
  } else {
    thumbHtml = `<div class="cu-thumb-empty">No preview</div>`;
  }

  card.innerHTML = `
    <div class="cu-thumb">${thumbHtml}</div>
    <div class="cu-item-body">
      <div class="cu-item-header">
        <div class="cu-ver-row cu-ver-row-model">
          <span class="cu-ver-label cu-ver-label-model">${typePill || "<span></span>"}</span>
          <span class="cu-ver-date"></span>
          <span class="cu-ver-base"></span>
          <div class="cu-ver-main">
            <h4 title="${escapeHtml(firstPath)}">${displayName}</h4>
            ${creatorHtml}
            ${provisionalHtml}
          </div>
        </div>
      </div>
      <div class="cu-versions">
        ${versionRows}
      </div>
    </div>`;
  for (const el of card.querySelectorAll(".cu-copy-path")) {
    el.addEventListener("click", (e) => copyOrReveal(e.currentTarget, e.currentTarget.dataset.path || "", state.checkJobId));
  }
  card.querySelector(".cu-thumb").addEventListener("click", () => openLightbox(item));
  // A removed model's preview URL is dead too; show the empty tile instead of
  // the browser's broken-image icon.
  const thumbImg = card.querySelector(".cu-thumb img");
  if (thumbImg) wireThumbImage(thumbImg, localPath, !thumbImg.getAttribute("src"));
  // A dead online video falls back the same way, to a picture.
  const thumbVideo = card.querySelector(".cu-thumb video");
  if (thumbVideo) {
    thumbVideo.addEventListener("error", () => {
      const img = Object.assign(document.createElement("img"), { alt: "" });
      thumbVideo.replaceWith(img);
      wireThumbImage(img, localPath, true);
    }, { once: true });
  }
  return card;
}

// The local copy is the last resort: try it once, then show "No preview".
// `loadNow` skips the online picture (there is none, or it already failed).
function wireThumbImage(img, localPath, loadNow) {
  const showNoPreview = () => img.replaceWith(Object.assign(document.createElement("div"), {
    className: "cu-thumb-empty",
    textContent: "No preview",
  }));
  let triedLocal = false;
  const loadLocal = async () => {
    triedLocal = true;
    const url = await fetchLocalPreview(localPath);
    if (url) img.src = url;
    else showNoPreview();
  };
  img.addEventListener("error", () => {
    if (localPath && !triedLocal) loadLocal();
    else showNoPreview();
  });
  if (!loadNow) return;
  if (localPath) loadLocal();
  else showNoPreview();
}

function renderLocalVersionRow(v, showMessage = true) {
  // A D row whose same version is also in the archive is an issue of its own;
  // an archived row never is (see check_results.js).
  const issue = rowIssue(v);
  const date = v.publishedAt ? shortDate(v.publishedAt) : "";
  const { role, label } = rowLabel(v);
  const filePath = v.filePath || v.modelPath || "";
  // A file Civitai never identified has no version name; its file name is
  // what the user will recognize.
  const name = v.versionName || extractFilename(filePath) || "?";
  const message = issue && showMessage ? `<div class="cu-issue-msg">${escapeHtml(issueMessage(v))}</div>` : "";
  // Delete sits where a new version's Download and Hide do.
  const del = rowDeleteHtml(v, rowDeleteUi(v.modelPath), escapeHtml);
  return `
      <div class="cu-ver-row${del ? " cu-ver-row-acts" : ""}">
        <span class="cu-ver-label" data-role="${role}">${label}</span>
        <span class="cu-ver-date">${escapeHtml(date || "\u2014")}</span>
        <span class="cu-ver-base">${escapeHtml(v.baseModel || "\u2014")}</span>
        <span class="cu-ver-main">
          <span class="cu-ver-link cu-copy-path" data-path="${escapeHtml(filePath)}" title="${escapeHtml(copyPathTitle(filePath))}">${escapeHtml(name)}</span>
        </span>${del ? `\n        <span class="cu-ver-acts">${del}</span>` : ""}
      </div>${message}`;
}

// Delete on a card's version row, as in the Gallery: the first click asks, the ask
// lapses on its own, the second click deletes. Keyed by the row's modelPath, so a
// redraw while asking keeps the question.
const rowAsking = new Map(); // modelPath -> "confirming" | "deleting"
const rowAskTimers = new Map();

function rowDeleteUi(path) {
  const ask = rowAsking.get(path);
  return { canTrash: state.canTrash, confirming: ask === "confirming", deleting: ask === "deleting" };
}

function setRowAsking(path, ask) {
  clearTimeout(rowAskTimers.get(path));
  rowAskTimers.delete(path);
  if (ask) rowAsking.set(path, ask);
  else rowAsking.delete(path);
  for (const button of state.resultsEl?.querySelectorAll('[data-row-action="delete"]') || []) {
    if (button.dataset.path === path) button.outerHTML = rowDeleteHtml({ modelPath: path }, rowDeleteUi(path), escapeHtml);
  }
}

async function onRowDelete(button) {
  const path = button.dataset.path || "";
  const ask = rowAsking.get(path);
  if (!path || ask === "deleting") return;
  if (ask !== "confirming") {
    setRowAsking(path, "confirming");
    rowAskTimers.set(
      path,
      setTimeout(() => setRowAsking(path, null), CONFIRM_FOR),
    );
    return;
  }
  setRowAsking(path, "deleting");
  let failure = "";
  try {
    const data = await postJson("/warp-studio/updater/delete-file", { jobId: state.checkJobId, path });
    if (data.error) failure = String(data.error);
  } catch (error) {
    failure = String(error?.message || error);
  }
  if (failure) {
    setStatus(failure, "error");
    setRowAsking(path, null);
    return;
  }
  // Success: the refresh redraws the card without this row. Never repaint Delete under the cursor
  // in between; only a row the server kept turns back afterwards.
  rowAsking.delete(path);
  await refreshAfterAction();
  setRowAsking(path, null);
}

function resultsSignature() {
  // Everything that changes what the list looks like, and nothing that does not.
  return JSON.stringify([
    state.checkJobId, state.resultMode, state.resultOffset, state.resultTotal, state.pageSize,
    state.groupBy, state.thenBy, state.sortOrder, state.matureMode,
    state.showHidden, state.matureHidden,
    [...state.collapsed].sort(),
    (state.groups || []).map((g) => [g.key, g.models, g.releases,
      (g.subgroups || []).map((sub) => [sub.key, sub.models, sub.releases])]),
    state.resultItems.map(cardSignature),
  ]);
}

// Everything one card is drawn from. A re-checked card keeps its count and
// changes its content, so the signature reads the whole item.
function cardSignature(item) {
  return JSON.stringify(item);
}

// A card's identity across reloads. A model Civitai never matched has no id: its first file stands in.
function cardKey(item) {
  const firstPath = item.modelId ? "" : String((item.localVersions || [])[0]?.modelPath || "");
  return `c:${item.modelId ?? ""}|${item.groupPathKey || ""}|${firstPath}`;
}

function resultsPageKey() {
  // Identity of *which* page this is, so a mere content refresh does not
  // replay the entry animation.
  // Collapsing is deliberately absent: folding one band away should not make
  // every remaining card animate in again.
  return [state.checkJobId, state.resultMode, state.resultOffset, state.pageSize, state.groupBy,
    state.thenBy, state.sortOrder, state.matureMode, state.showHidden,
    (state.filterTypes || []).join(","), (state.filterBases || []).join(",")].join("|");
}

function renderResults() {
  if (!state.resultsEl) return;
  renderViewSwitch();
  renderCacheInfo();
  if (state.startupLoading && !state.resultItems.length) {
    state.lastRenderSignature = null;
    state.renderedEntries = null;
    state.resultsEl.innerHTML = `<div class="cu-loading-note"><span class="cu-spinner" aria-hidden="true"></span>Loading results…</div>`;
    return;
  }
  // A check job polls every 800ms; without this the whole list is torn down
  // and rebuilt each tick, which replays the entry animation and makes the
  // panel look like it is flickering.
  const signature = resultsSignature();
  const pageKey = resultsPageKey();
  if (signature === state.lastRenderSignature && state.resultsEl.childElementCount) {
    renderPagination();
    return;
  }
  const samePage = pageKey === state.lastPageKey;
  state.lastRenderSignature = signature;
  state.lastPageKey = pageKey;
  state.resultsEl.classList.toggle("cu-quiet", samePage);

  syncLocalPreviewCache();
  if (!state.checkJobId) {
    state.renderedEntries = null;
    state.resultsEl.innerHTML = "";
    appendEmpty("Run Check for Updates to see results.");
    renderPagination();
    return;
  }
  if (!state.resultItems.length) {
    const noTypeSelection = Array.isArray(state.filterTypes) && state.facets.modelTypes.length && state.filterTypes.length === 0;
    const noBaseSelection = Array.isArray(state.filterBases) && state.facets.baseModels.length && state.filterBases.length === 0;
    if (noTypeSelection || noBaseSelection) {
      state.renderedEntries = null;
      state.resultsEl.innerHTML = "";
      appendEmpty("No filter options selected.");
      renderPagination();
      return;
    }
    // With every band collapsed there are no cards, but the headers are
    // still the only way to expand them again.
    if (!hasCollapsedBand()) {
      const none = state.resultMode === "issues" ? "No issues: every model was matched and checked." : "No updates found.";
      state.renderedEntries = null;
      state.resultsEl.innerHTML = "";
      appendEmpty(state.resultTotal === 0 ? none : "No items on this page.");
      renderPagination();
      return;
    }
  }
  // Bands are rendered from the outline rather than from the items, so a
  // collapsed band keeps its header - and with it, the way back.
  const byPrimary = new Map();
  for (const item of state.resultItems) {
    const primaryKey = item.groupPrimaryKey || "";
    const pathKey = item.groupPathKey || "";
    if (!byPrimary.has(primaryKey)) byPrimary.set(primaryKey, new Map());
    const buckets = byPrimary.get(primaryKey);
    if (!buckets.has(pathKey)) buckets.set(pathKey, []);
    buckets.get(pathKey).push(item);
  }

  // The list as entries: a key, a signature and a way to build the element.
  // Only entries that are new or changed are built (see applyResultEntries).
  const entries = [];
  let cardIndex = 0;
  const appendCard = (item) => {
    cardIndex += 1;
    const index = cardIndex;
    entries.push({ key: cardKey(item), sig: cardSignature(item), build: () => buildResultCard(item, index) });
  };

  if (state.groupBy === "none") {
    for (const item of state.resultItems) appendCard(item);
  } else {
    let firstBand = true;
    for (const group of state.groups || []) {
      const collapsed = state.collapsed.has(group.key);
      const buckets = byPrimary.get(group.key);
      const hasCards = Boolean(buckets && buckets.size);
      const hasCollapsedSub = (group.subgroups || []).some((sub) => state.collapsed.has(sub.key));
      // A band whose cards all sit on another page is not on this one, unless
      // it holds a collapsed header that must stay reachable.
      if (!hasCards && !collapsed && !hasCollapsedSub) continue;

      const continued = firstBand && hasCards && state.startsMidPrimary;
      entries.push({
        key: `g:${group.key}`,
        sig: JSON.stringify([group.label, group.models, group.releases, collapsed, continued]),
        build: () => renderGroupHead(group, collapsed, continued),
      });
      firstBand = false;
      if (collapsed) continue;

      if (state.thenBy === "none") {
        for (const item of (buckets && buckets.get(group.key)) || []) appendCard(item);
        continue;
      }

      let firstSub = true;
      for (const sub of group.subgroups || []) {
        const subCollapsed = state.collapsed.has(sub.key);
        const list = (buckets && buckets.get(sub.key)) || [];
        if (!list.length && !subCollapsed) continue;
        const subContinued = firstSub && list.length > 0 && state.startsMidSecondary;
        entries.push({
          key: `s:${sub.key}`,
          sig: JSON.stringify([sub.label, sub.models, sub.releases, subCollapsed, subContinued]),
          build: () => renderSubgroupHead(sub, subCollapsed, subContinued),
        });
        firstSub = false;
        if (subCollapsed) continue;
        for (const item of list) appendCard(item);
      }
    }
  }

  const notice = matureNoticeText();
  if (notice) {
    entries.push({ key: "note", sig: notice, build: () => renderMatureNotice(notice) });
  }
  applyResultEntries(entries, samePage);
  renderPagination();
}

/* Replaces an element and, if keyboard focus was on it or inside it, puts focus
   on the matching control of the replacement (the same place among its
   controls), so a quiet update never drops the reader's place. */
function replaceKeepingFocus(oldElement, newElement) {
  const controls = "button, a[href], input, select, textarea, [tabindex]";
  const active = document.activeElement;
  let index = -1;
  let wasFocused = false;
  if (active && oldElement.contains(active)) {
    wasFocused = true;
    index = active === oldElement ? -1 : [...oldElement.querySelectorAll(controls)].indexOf(active);
  }
  oldElement.replaceWith(newElement);
  if (!wasFocused) return;
  const target = index >= 0
    ? newElement.querySelectorAll(controls)[index] || newElement.querySelector(controls)
    : newElement.matches(controls) ? newElement : newElement.querySelector(controls);
  if (target) target.focus({ preventScroll: true });
}

/* Puts the entries on the page. On the same page and arrangement, an entry
   whose signature is unchanged keeps its element (no flicker, no entry
   animation, no picture reloaded), a changed one is replaced where it stands,
   and new and removed ones come and go; the order then follows the list.
   Otherwise the list is rebuilt. The scroll position is not touched. */
function applyResultEntries(entries, inPlace) {
  const root = state.resultsEl;
  const old = state.renderedEntries;
  const keyed = new Map();
  for (const entry of entries) {
    // Two entries must never share a key, or one would take the other's element.
    let key = entry.key;
    for (let n = 2; keyed.has(key); n += 1) key = `${entry.key}#${n}`;
    keyed.set(key, entry);
  }
  const next = new Map();
  if (!inPlace || !old || !root.childElementCount) {
    root.innerHTML = "";
    for (const [key, entry] of keyed) {
      const element = entry.build();
      root.appendChild(element);
      next.set(key, { sig: entry.sig, element });
    }
    state.renderedEntries = next;
    return;
  }
  for (const [key, entry] of keyed) {
    const had = old.get(key);
    if (had && had.sig === entry.sig) {
      next.set(key, had);
      continue;
    }
    const element = entry.build();
    if (had) replaceKeepingFocus(had.element, element);
    next.set(key, { sig: entry.sig, element });
  }
  for (const [key, had] of old) {
    if (!keyed.has(key)) had.element.remove();
  }
  let cursor = root.firstElementChild;
  for (const { element } of next.values()) {
    if (element === cursor) cursor = cursor.nextElementSibling;
    else root.insertBefore(element, cursor);
  }
  state.renderedEntries = next;
}

function hasCollapsedBand() {
  if (state.groupBy === "none") return false;
  return (state.groups || []).some((group) => state.collapsed.has(group.key)
    || (group.subgroups || []).some((sub) => state.collapsed.has(sub.key)));
}

/* Rename is always there; Updates and Issues need a check to list anything.
   Before a check, an Updates tab without a count leads back from Rename. */
function renderViewSwitch() {
  if (!state.viewSwitchEl) return;
  // Issues has no tab without a check; fall back to the tab that stays.
  if (!state.checkJobId && state.resultMode === "issues") state.resultMode = "updates";
  const counts = state.modeCounts || {};
  const tab = (mode, label, hint, withCount = true) => {
    const selected = state.resultMode === mode ? "true" : "false";
    return `<button class="cu-view-tab" type="button" role="tab" data-view="${mode}" aria-selected="${selected}" title="${hint}">`
      + `<span>${label}</span>${withCount ? `<span class="cu-view-count">${Number(counts[mode] || 0)}</span>` : ""}</button>`;
  };
  const galleryCount = state.galleryView?.count?.();
  counts.gallery = galleryCount;
  const galleryTab = tab("gallery", "Gallery", "Your LoRAs, ready to add to a prompt", galleryCount != null);
  const checkTabs = state.checkJobId
    ? tab("updates", "Updates", "Models with a newer release") + tab("issues", "Issues", "Models Civitai could not match or check")
    : tab("updates", "Updates", "Run Check for Updates to list newer releases", false);
  // Rename counts the models with a new name to apply, under the same filters.
  const renameCount = state.renameView?.count?.();
  counts.rename = renameCount;
  state.viewSwitchEl.innerHTML = galleryTab + checkTabs
    + tab("rename", "Rename", "Rename your model files after their Civitai releases", renameCount != null);
}

/* Show the selected view's pane; the Rename view loads its state when shown.
   The Gallery loads on its first show; later shows only redraw it and follow
   the target, and reorder when the target's base changed. */
function applyResultMode() {
  const mode = state.resultMode;
  const scroller = state.rootEl?.isConnected ? findScroller(state.rootEl, getComputedStyle) : null;
  if (scroller && state.shownMode) state.scrollMemory.save(state.shownMode, scroller.scrollTop);
  state.shownMode = mode;
  const rename = mode === "rename";
  const gallery = mode === "gallery";
  if (state.galleryTabEl) state.galleryTabEl.style.display = gallery ? "" : "none";
  if (state.renameTabEl) state.renameTabEl.style.display = rename ? "" : "none";
  if (state.updatesPaneEl) state.updatesPaneEl.style.display = rename || gallery ? "none" : "";
  // A menu left open in a tab that is no longer shown would reappear with it.
  for (const wiring of Object.values(state.toolbarWiring)) wiring.close();
  renderViewSwitch();
  if (rename && state.renameView) state.renameView.refresh();
  if (gallery) showGallery();
  restoreScroll(mode);
}

/* Back at the offset this view was left at, once its content is in. */
function restoreScroll(mode) {
  requestAnimationFrame(() => {
    if (state.resultMode !== mode || !state.rootEl?.isConnected) return;
    const scroller = findScroller(state.rootEl, getComputedStyle);
    if (scroller) scroller.scrollTop = state.scrollMemory.get(mode);
  });
}

/* Civitai's numbers for the stat sorts. A failed request keeps what is there
   and says nothing; the sorts then fall back to name. */
async function loadModelStats() {
  try {
    const data = await getJson("/warp-studio/updater/model-stats");
    const next = new Map(Object.entries(data?.stats || {}));
    state.modelStats = next;
    if (state.galleryLoaded && STAT_SORTS.includes(state.galleryArrange.sort)) {
      state.galleryView?.render();
    }
  } catch (_) {
    // no numbers; the sorts fall back to name
  }
}

function showGallery() {
  const view = state.galleryView;
  if (!view) return;
  if (!state.galleryLoaded) {
    state.galleryLoaded = true;
    loadModelStats();
    view.refresh().catch(() => {});
    return;
  }
  view.render();
  view.updateTarget().then(({ baseChanged }) => {
    if (baseChanged && state.galleryView === view) view.render();
  }).catch(() => {});
}

/* A Prompt + LoRAs node asked for the gallery: note it as used, show the
   Gallery (opening the sidebar if needed) and reorder for that node.
   request is the event's detail: { node, nodeId }. */
function openGallery(request) {
  const sidebar = app.extensionManager?.sidebarTab;
  const drawn = Boolean(state.rootEl?.isConnected && state.galleryView);
  if (!drawn) state.pendingGallery = { node: request?.node ?? null, nodeId: request?.nodeId };
  if (sidebar && sidebar.activeSidebarTabId !== TAB_ID) sidebar.toggleSidebarTab(TAB_ID);
  if (!drawn) return; // renderTab applies the request when it draws
  state.pendingGallery = null;
  state.resultMode = "gallery";
  applyResultMode();
  openedGallery(request);
}

/* The node itself when the event carries it; by id, in the graph on screen,
   only for a sender that gives an id alone. */
function openedGallery(request) {
  const node = request?.node
    ?? (request?.nodeId == null ? null : promptNodesOf(app).find((n) => String(n.id) === String(request.nodeId)));
  if (node) noteUsed(node);
  const view = state.galleryView;
  if (!view) return;
  view.updateTarget().then(() => {
    if (state.galleryView === view) view.render(); // opened on purpose: bands follow the target
  }).catch(() => {});
}

/* Start a job for the Rename view through the shared progress and pause/stop
   controls. Returns the route's reply; a reply without a jobId started no job
   (the naming model was already there). */
async function startPanelJob(endpoint, type, payload, statusText) {
  if (state.currentJobId) throw new Error("A job is already running.");
  const data = await postJson(endpoint, payload || {});
  if (!data || !data.jobId) return data || {};
  bindJob(data.jobId, type, statusText);
  return data;
}

/* Follow a job that was just started: the shared progress bar, controls and poll. */
function bindJob(jobId, type, statusText) {
  state.currentJobId = jobId;
  state.currentJobType = type;
  state.currentJobStatus = "queued";
  state.currentSummary = null;
  state.currentMessage = "";
  state.currentProgress = 0;
  state.currentTotal = 0;
  state.currentItemCount = 0;
  state.lastStatus = "";
  state.lastItemCount = -1;
  resetEta();
  clearError();
  updateProgress(0, 0, true);
  updateControlButtons();
  setStatus(statusText || "Working\u2026");
  pollJob(jobId);
}

/* ---- Model downloads (Updates cards) ---- */

async function loadDownloads() {
  const data = await getJson("/warp-studio/updater/downloads");
  state.downloads = latestRecords(data.requests);
  state.canTrash = Boolean(data.canTrash);
  watchDownloads();
  return data.jobId || null;
}

/* While a request waits behind another job or downloads, the records are read
   every second: a waiting card then shows what it waits for, and the job bar
   binds to the download job when it starts. A bound download job's own poll
   already does this, so the watch skips those ticks. */
function watchDownloads() {
  const busy = [...state.downloads.values()].some((r) => r.state === "waiting" || r.state === "downloading");
  if (!busy) {
    if (state.downloadWatch) clearInterval(state.downloadWatch);
    state.downloadWatch = null;
    return;
  }
  if (state.downloadWatch) return;
  let inFlight = false;
  state.downloadWatch = setInterval(async () => {
    if (inFlight) return;
    if (state.currentJobType === MODEL_DOWNLOAD_JOB && state.pollTimer) return;
    inFlight = true;
    try {
      const wasDone = new Set([...state.downloads.values()].filter((r) => r.state === "done").map((r) => r.id));
      const jobId = await loadDownloads();
      paintDownloads();
      if (jobId && !state.currentJobId) {
        attachToRunningJob({ jobId, type: MODEL_DOWNLOAD_JOB, status: "running" });
      } else if ([...state.downloads.values()].some((r) => r.state === "done" && !wasDone.has(r.id))) {
        await refreshAfterAction();
      }
    } catch (error) {
      // A missed tick is not an error; the next one reads again.
    } finally {
      inFlight = false;
    }
  }, 1000);
}

// Paint each card's download slot in place: only what changed is written, so nothing jumps.
function paintDownloads() {
  const root = state.resultsEl;
  if (root) {
    for (const slot of root.querySelectorAll(".cu-dl[data-dl-key]")) {
      const record = state.downloads.get(slot.dataset.dlKey);
      // canReplace is worked out each time: /downloads may answer after the cards were drawn.
      const canReplace = state.canTrash && slot.dataset.hasLocal === "1";
      slot.dataset.canReplace = canReplace ? "1" : "0";
      const html = versionActionsHtml(
        { paid: slot.dataset.paid === "1", canReplace },
        record,
        escapeHtml,
      );
      if (slot.innerHTML !== html) slot.innerHTML = html;
    }
    for (const msg of root.querySelectorAll("[data-dl-msg]")) {
      const text = recordMessage(state.downloads.get(msg.dataset.dlMsg));
      if (msg.textContent !== text) msg.textContent = text;
    }
  }
  state.galleryView?.paintDownloads?.();
}

async function startDownload(payload, key) {
  try {
    const data = await postJson("/warp-studio/updater/downloads", payload);
    if (data.request) state.downloads.set(data.request.key || key, data.request);
    paintDownloads();
    // Held behind another job, the request has no job yet: the watch shows it and binds when it starts.
    watchDownloads();
    if (data.jobId && data.jobId !== state.currentJobId) {
      bindJob(data.jobId, MODEL_DOWNLOAD_JOB, "Downloading\u2026");
    }
  } catch (error) {
    state.downloads.set(key, { key, state: "error", error: String(error?.message || error) });
    paintDownloads();
  }
}

async function stopDownload(requestId) {
  try {
    const data = await postJson(`/warp-studio/updater/downloads/${encodeURIComponent(requestId)}/stop`, {});
    if (data.request) state.downloads.set(data.request.key, data.request);
  } catch (error) {
    setStatus(`Could not stop the download: ${error.message}`, "error");
  }
  paintDownloads();
}

// Sends one model file and its sidecars to the Recycle Bin. Resolves "" on success, else the error.
async function deleteModel(name, kind) {
  try {
    const data = await postJson("/warp-studio/updater/delete-model", { name, kind });
    if (data.error) return String(data.error);
    await refreshAfterAction();
    return "";
  } catch (error) {
    return String(error?.message || error);
  }
}

// After a download finished: the check results and the gallery read the library again.
async function refreshAfterAction() {
  if (state.checkJobId) await loadResultPage(true);
  // Forced: a load already in flight may predate what just changed. Resolves once the cards are drawn.
  if (state.galleryLoaded) await state.galleryView?.refresh(true);
}

function setNamingModels(list) {
  const names = Array.isArray(list) ? list.map(String) : [];
  const current = String(getSetting(SETTINGS.namingModel, "") || "");
  if (current && !names.includes(current)) names.push(current);
  state.namingModels = names;
}

function namingModelOptions(value) {
  const options = [{ value: "", text: "None" }];
  const names = [...state.namingModels];
  if (value && !names.includes(String(value))) names.push(String(value));
  for (const name of names) options.push({ value: name, text: name });
  return options;
}

/* The backend chose the naming model (a download, or an existing copy found):
   take it into the setting, or the next settings save would post the old value. */
function adoptNamingModel(name) {
  const value = String(name || "");
  if (!value) return;
  if (!state.namingModels.includes(value)) state.namingModels.push(value);
  state.syncedNamingModel = value;
  setSetting(SETTINGS.namingModel, value);
  state.namingModelDirty = false;
}

function countLabel(models, releases) {
  const modelWord = models === 1 ? "model" : "models";
  if (state.resultMode === "issues") return `${models} ${modelWord}`;
  return `${models} ${modelWord} \u00b7 <b>${releases} new</b>`;
}

function renderGroupHead(group, collapsed, continued) {
  const wrap = document.createElement("div");
  wrap.className = "cu-group-head-wrap";
  wrap.innerHTML = `
    <button class="cu-group-head" data-group-key="${escapeHtml(group.key)}"
            data-collapsed="${collapsed ? "true" : "false"}" aria-expanded="${collapsed ? "false" : "true"}">
      <span class="cu-group-caret" aria-hidden="true">\u25be</span>
      <span class="cu-group-name">${escapeHtml(group.label || "Ungrouped")}</span>
      ${continued ? '<span class="cu-group-cont">cont.</span>' : ""}
      <span class="cu-group-count">${countLabel(group.models, group.releases)}</span>
    </button>`;
  return wrap;
}

function renderSubgroupHead(sub, collapsed, continued) {
  const wrap = document.createElement("div");
  wrap.className = "cu-subgroup-head-wrap";
  wrap.innerHTML = `
    <button class="cu-subgroup-head" data-group-key="${escapeHtml(sub.key)}"
            data-collapsed="${collapsed ? "true" : "false"}" aria-expanded="${collapsed ? "false" : "true"}">
      <span class="cu-group-caret" aria-hidden="true">\u25be</span>
      <span class="cu-subgroup-name">${escapeHtml(sub.label || "Unknown")}</span>
      ${continued ? '<span class="cu-group-cont">cont.</span>' : ""}
      <span class="cu-subgroup-count">${countLabel(sub.models, sub.releases)}</span>
    </button>`;
  return wrap;
}

function matureNoticeText() {
  if (state.matureMode !== "hide" || !state.matureHidden) return "";
  const word = state.matureHidden === 1 ? "model" : "models";
  return `${state.matureHidden} mature ${word} hidden — change this in Settings → Warp Studio → Civitai General.`;
}

function renderMatureNotice(text) {
  const note = document.createElement("div");
  note.className = "cu-mature-note";
  note.textContent = text;
  return note;
}

/* Group, Then, Sort and Per page live in the Sort menu; this brings the toolbar's label up to date. */
function renderArrange() {
  renderToolbars();
}

function onMatureModeChanged() {
  const next = String(getSetting(SETTINGS.matureMode, "show") || "show");
  if (next === state.matureMode) return;
  state.matureMode = next;
  state.pageOffset = 0;
  loadResultPage(true);
  state.renameView?.render();
}

/* A thin moving bar under the tabs while results load (Updates, Issues or Rename). */
const loadingNow = new Set();

function setLoading(kind, on) {
  if (on) loadingNow.add(kind);
  else loadingNow.delete(kind);
  state.loadingBarEl?.classList.toggle("is-on", loadingNow.size > 0);
}

/* The filter menus serve every view: the check's models, the dry run's and the gallery's.
   The gallery files "ZImageTurbo" under "ZImage", and ticking the check's spelling
   shows those LoRAs too, so a gallery base the others already name is not listed twice. */
function mergedFacets() {
  const check = state.checkFacets || {};
  const rename = state.renameFacets || {};
  const gallery = state.galleryFacets || {};
  const union = (...lists) => [...new Set(lists.flatMap((list) => list || []))].sort();
  const others = union(check.baseModels, rename.baseModels);
  return {
    modelTypes: union(check.modelTypes, rename.modelTypes, gallery.modelTypes),
    baseModels: union(others, galleryOnlyBases(gallery.baseModels, others)),
  };
}

function onGalleryFacets(facets) {
  state.galleryFacets = {
    modelTypes: Array.isArray(facets?.modelTypes) ? facets.modelTypes : [],
    baseModels: Array.isArray(facets?.baseModels) ? facets.baseModels : [],
  };
  applyFacets(mergedFacets());
  renderFilters();
}

function onRenameFacets(facets) {
  state.renameFacets = {
    modelTypes: Array.isArray(facets?.modelTypes) ? facets.modelTypes : [],
    baseModels: Array.isArray(facets?.baseModels) ? facets.baseModels : [],
  };
  applyFacets(mergedFacets());
  renderFilters();
}

/* The Rename and Gallery views' filter: null for a menu that shows everything. */
function sharedFilter() {
  const pick = (selection, options) => (selection === null || selectsAll(selection, options) ? null : [...(selection || [])]);
  return { types: pick(state.filterTypes, state.facets.modelTypes), bases: pick(state.filterBases, state.facets.baseModels) };
}

async function onFiltersChanged() {
  state.pageOffset = 0;
  renderFilters();
  // The Rename list (and so its tab count) follows the filters even while hidden.
  state.renameView?.render();
  if (state.galleryLoaded) state.galleryView?.render();
  if (state.resultMode === "rename" || state.resultMode === "gallery") await loadModeCounts();
  else await loadResultPage(true);
}

/* The Updates and Issues counts under the filters, while Rename is shown. */
async function loadModeCounts() {
  if (!state.checkJobId) return;
  const query = new URLSearchParams({
    offset: "0", limit: "1", mode: "updates",
    showHidden: state.showHidden ? "1" : "0", mature: state.matureMode || "show",
  });
  for (const [key, value] of filterParams()) query.append(key, value);
  try {
    const data = await getJson(`/warp-studio/updater/jobs/${state.checkJobId}/items?${query.toString()}`);
    if (data.modeCounts) state.modeCounts = data.modeCounts;
    if (data.facets) {
      state.checkFacets = data.facets;
      applyFacets(mergedFacets());
      renderFilters();
    }
  } catch (_) {
    // the counts stay as they were
  }
  renderViewSwitch();
}

function renderFilters() {
  syncFacetSelection("filterTypes", "modelTypes");
  syncFacetSelection("filterBases", "baseModels");
  renderToolbars();
}

function applyFacets(nextFacets) {
  const previous = state.facets || { modelTypes: [], baseModels: [] };
  const normalized = {
    modelTypes: Array.isArray(nextFacets?.modelTypes) ? nextFacets.modelTypes : [],
    baseModels: Array.isArray(nextFacets?.baseModels) ? nextFacets.baseModels : [],
  };

  carryAllSelectionForward("filterTypes", previous.modelTypes || [], normalized.modelTypes);
  carryAllSelectionForward("filterBases", previous.baseModels || [], normalized.baseModels);
  state.facets = normalized;
}

function carryAllSelectionForward(selectionKey, previousOptions, nextOptions) {
  if (state[selectionKey] === null || !previousOptions.length || !nextOptions.length) return;

  const selected = state[selectionKey] || [];
  const hadAllPreviousOptions = previousOptions.every((value) => selected.includes(value));
  if (hadAllPreviousOptions) {
    state[selectionKey] = [...nextOptions];
  }
}

function renderRemoteVersionRow(item, version, hidden) {
  const modelId = item.modelId;
  const date = version.versionDate ? shortDate(version.versionDate) : "—";
  const name = escapeHtml(version.versionName || "?");
  const link = version.versionUrl
    ? `<a class="cu-ver-link" href="${escapeHtml(version.versionUrl)}" target="_blank" rel="noreferrer noopener" title="${name}">${name}</a>`
    : `<span class="cu-ver-link" title="${name}">${name}</span>`;
  const action = hidden
    ? `<button class="cu-inline-btn" data-archive-action="restore" data-model-id="${escapeHtml(modelId || "")}" data-version-id="${escapeHtml(version.versionId || "")}">Unhide</button>`
    : `<button class="cu-inline-btn" data-archive-action="archive" data-model-id="${escapeHtml(modelId || "")}" data-version-id="${escapeHtml(version.versionId || "")}">Hide</button>`;
  const accessBadge = renderPaidBadge(version) || renderAvailabilityBadge(version.availability);
  const hiddenClass = hidden ? " is-hidden" : "";
  // The download slot: painted in place while a download runs, so the row keeps its size.
  const current = (item.localVersions || [])[0];
  const hasLocal = Boolean(current) && !current.metadataOnly;
  const canReplace = state.canTrash && hasLocal;
  const key = versionKey(version.versionId);
  const record = state.downloads.get(key);
  const paid = isPaid(version);
  const slot = versionActionsHtml({ paid, canReplace }, record, escapeHtml);
  const dlSlot = `<span class="cu-dl" data-dl-key="${escapeHtml(key)}" data-model-id="${escapeHtml(modelId || "")}" data-version-id="${escapeHtml(version.versionId || "")}" data-model-path="${escapeHtml(current?.modelPath || "")}" data-paid="${paid ? 1 : 0}" data-has-local="${hasLocal ? 1 : 0}" data-can-replace="${canReplace ? 1 : 0}">${slot}</span>`;
  return `
    <div class="cu-ver-row cu-ver-row-acts${hiddenClass}">
      <span class="cu-ver-label" data-role="${hidden ? "hidden" : "new"}">${hidden ? "Hidden" : "New"}</span>
      <span class="cu-ver-date">${escapeHtml(date)}</span>
      <span class="cu-ver-base">${escapeHtml(version.baseModel || "—")}</span>
      <span class="cu-ver-main">
        ${link}
        ${accessBadge}
      </span>
      <span class="cu-ver-acts">
        ${dlSlot}
        ${action}
      </span>
    </div>
    <div class="cu-issue-msg cu-dl-msg" data-dl-msg="${escapeHtml(key)}">${escapeHtml(recordMessage(record))}</div>`;
}

/* Buzz: downloading the release costs Buzz. Early access is free from its end
   date (dashed badge); a permanent paywall has none (solid badge). Civitai's
   public API gives no price. */
function renderPaidBadge(version) {
  if (version.paid === "permanent") {
    return `<span class="cu-buzz" data-paid="permanent" title="Costs Buzz to download, with no free date.">Buzz \u00b7 always</span>`;
  }
  if (version.paid !== "early") return "";
  const ends = version.paidUntil ? new Date(version.paidUntil) : null;
  if (!ends || Number.isNaN(ends.getTime())) {
    return `<span class="cu-buzz" data-paid="early" title="Early access: costs Buzz to download for now. Civitai gives no end date.">Buzz \u00b7 early</span>`;
  }
  const days = Math.max(1, Math.ceil((ends.getTime() - Date.now()) / 86400000));
  const title = `Early access: costs Buzz to download until ${shortDate(version.paidUntil)}, free after that.`;
  return `<span class="cu-buzz" data-paid="early" title="${escapeHtml(title)}">Buzz \u00b7 ${days}d left</span>`;
}

const AVAILABILITY_SHORT = { EarlyAccess: "Early", Private: "Private", Unsearchable: "Unlisted" };

function renderAvailabilityBadge(availability) {
  if (!availability || availability === "Public") return "";
  const label = formatAvailability(availability);
  const short = AVAILABILITY_SHORT[availability] || label;
  return `<span class="cu-access-badge" data-availability="${escapeHtml(availability)}" title="${escapeHtml(label)}">${escapeHtml(short)}</span>`;
}

function formatAvailability(value) {
  if (value === "EarlyAccess") return "Early access";
  return String(value).replace(/([a-z])([A-Z])/g, "$1 $2");
}

function filterParams() {
  // Listing every option would still drop cards that have no value for it
  // (a version without a base model), so a full selection sends no filter.
  const params = [];
  if (!selectsAll(state.filterTypes, state.facets.modelTypes)) {
    for (const modelType of state.filterTypes || []) params.push(["modelType", modelType]);
  }
  if (!selectsAll(state.filterBases, state.facets.baseModels)) {
    for (const baseModel of state.filterBases || []) params.push(["baseModel", baseModel]);
  }
  return params;
}

function selectsAll(selection, options) {
  return Array.isArray(selection) && options.length > 0 && options.every((value) => selection.includes(value));
}

function syncFacetSelection(selectionKey, facetKey) {
  const available = state.facets[facetKey] || [];
  const current = state[selectionKey] || [];
  if (!available.length) {
    state[selectionKey] = null;
    return;
  }
  if (state[selectionKey] === null) {
    state[selectionKey] = [...available];
    return;
  }
  const next = current.filter((value) => available.includes(value));
  state[selectionKey] = current.length > 0 && next.length === 0 ? [...available] : next;
}

async function toggleArchivedUpdate(modelId, versionId, restore) {
  const endpoint = restore ? "/warp-studio/updater/archived-updates/restore" : "/warp-studio/updater/archived-updates";
  try {
    await postJson(endpoint, { modelId, versionIds: [versionId] });
    await loadResultPage(true);
  } catch (error) {
    setStatus(`Failed to update hidden versions: ${error.message}`, "error");
  }
}

function renderPagination() {
  if (!state.pageInfoEl || !state.prevEl || !state.nextEl) return;
  const total = Math.max(0, state.resultTotal);
  const size = Math.max(1, state.pageSize);
  const page = total === 0 ? 1 : Math.floor(state.resultOffset / size) + 1;
  const pages = Math.max(1, Math.ceil(total / size));
  state.pageInfoEl.textContent = `${page} / ${pages}`;
  state.prevEl.disabled = state.resultOffset <= 0;
  state.nextEl.disabled = state.resultOffset + size >= total;
}

function appendEmpty(text) {
  const el = document.createElement("div");
  el.className = "cu-empty";
  el.textContent = text;
  state.resultsEl.appendChild(el);
}

function renderRoots() {
  if (!state.rootsSummaryEl || !state.rootsDetailsEl || !state.rootEl) return;
  const counts = MODEL_TYPES.map((t) => `${t}: ${(state.roots[t] || []).length}`);
  state.rootsSummaryEl.textContent = counts.join(" \u00b7 ");
  const toggle = state.rootEl.querySelector("#cu-roots-toggle");
  if (toggle) toggle.textContent = state.rootsExpanded ? "Hide" : "Show";
  state.rootsDetailsEl.style.display = state.rootsExpanded ? "block" : "none";
  state.rootsDetailsEl.innerHTML = "";
  for (const type of MODEL_TYPES) {
    const row = document.createElement("div");
    row.className = "cu-root-row";
    const items = state.roots[type] || [];
    row.innerHTML = `<span class="cu-root-type">${type}</span><span>${escapeHtml(items.length ? items.join("\n") : "(none)").replaceAll("\n", "<br>")}</span>`;
    state.rootsDetailsEl.appendChild(row);
  }
}

function updateControlButtons() {
  if (!state.pauseEl || !state.stopEl || !state.jobControlsEl) return;
  const active = Boolean(state.currentJobId);
  state.pauseEl.textContent = active && state.currentJobStatus === "paused" ? "Resume" : "Pause";
  // The server refuses to pause the naming-model download (it cannot resume).
  state.pauseEl.style.display =
    state.currentJobType === "naming-model-download" || state.currentJobType === MODEL_DOWNLOAD_JOB ? "none" : "";
  state.jobControlsEl.style.display = active ? "flex" : "none";
}

function selectedTypes() {
  return [...state.scopeTypes];
}

function resetEta() {
  state.etaSecPerItem = null;
  state.etaLastSample = null;
}

/* Track a smoothed seconds-per-file rate. Per-file cost swings hard \u2014 a cached
   sidecar is instant, hashing a 6GB checkpoint is not \u2014 so the raw rate is fed
   through an EWMA instead of being used directly. */
function sampleEta(current, total, status) {
  if (status === "paused") {
    // Drop the anchor so the paused stretch is never counted as work time.
    state.etaLastSample = null;
    return;
  }
  if (!total || current >= total) return;

  const now = Date.now();
  const prev = state.etaLastSample;
  if (!prev) {
    state.etaLastSample = { at: now, done: current };
    return;
  }
  const finished = current - prev.done;
  if (finished <= 0) return; // Still on the same file \u2014 keep the current estimate.

  const secPerItem = (now - prev.at) / 1000 / finished;
  state.etaLastSample = { at: now, done: current };
  state.etaSecPerItem =
    state.etaSecPerItem === null
      ? secPerItem
      : ETA_SMOOTHING * secPerItem + (1 - ETA_SMOOTHING) * state.etaSecPerItem;
}

function etaSecondsRemaining(current, total) {
  if (state.etaSecPerItem === null || !total || current >= total) return null;
  const budget = state.etaSecPerItem * (total - current);
  // Subtract time already burned on the in-flight file so the estimate ticks down
  // between completions instead of sitting still.
  const inFlight = state.etaLastSample ? (Date.now() - state.etaLastSample.at) / 1000 : 0;
  return Math.max(0, budget - inFlight);
}

function formatEta(seconds) {
  if (seconds === null) return "";
  const s = Math.round(seconds);
  if (s < 10) return "finishing up";
  if (s < 60) return `~${s}s left`;
  const minutes = Math.floor(s / 60);
  if (minutes < 10) return s % 60 ? `~${minutes}m ${s % 60}s left` : `~${minutes}m left`;
  if (minutes < 60) return `~${minutes}m left`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `~${hours}h ${minutes % 60}m left` : `~${hours}h left`;
}

function updateProgress(current, total, visible) {
  if (!state.progressFillEl || !state.progressTextEl) return;
  if (visible) hideCachedNotice();
  if (state.jobBarEl) state.jobBarEl.hidden = !visible;
  const safeTotal = Math.max(0, Number(total || 0));
  const safeCurrent = Math.max(0, Number(current || 0));
  const pct = safeTotal > 0 ? Math.min(100, Math.round((safeCurrent / safeTotal) * 100)) : 0;
  state.progressFillEl.style.width = `${pct}%`;
  state.progressTextEl.textContent = safeTotal > 0 ? `${pct}%` : "";

  if (!state.progressEtaEl) return;
  if (!visible || safeTotal <= 0) {
    state.progressEtaEl.textContent = "";
  } else if (state.currentJobStatus === "paused") {
    state.progressEtaEl.textContent = "paused";
  } else {
    if (state.currentJobType === MODEL_DOWNLOAD_JOB) {
      state.progressEtaEl.textContent = "";
      return;
    }
    const remaining = etaSecondsRemaining(safeCurrent, safeTotal);
    state.progressEtaEl.textContent = remaining === null ? "estimating\u2026" : formatEta(remaining);
  }
}

/* An error stays on screen under the job bar until the next job starts. Routine
   text (progress, "Done", "Showing cached results") is the job bar's label while
   a job runs; the last check's facts are behind the ⓘ. */
function setStatus(message, level = "info") {
  state.statusText = message;
  if (level === "error") state.errorText = message;
  else state.jobText = message;
  paintStatus();
}

function paintStatus() {
  if (state.statusEl && state.statusEl.textContent !== state.errorText) state.statusEl.textContent = state.errorText;
  if (state.jobTextEl && state.jobTextEl.textContent !== state.jobText) {
    state.jobTextEl.textContent = state.jobText;
    state.jobTextEl.title = state.jobText;
  }
}

function clearError() {
  if (!state.errorText) return;
  state.errorText = "";
  paintStatus();
}

function extractFilename(path) {
  const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return i >= 0 ? path.substring(i + 1) : path;
}

function getSetting(id, fallback) {
  const value = app.extensionManager.setting.get(id, fallback);
  return value ?? fallback;
}

function setSetting(id, value) {
  app.extensionManager.setting.set(id, value);
}

async function hydrateSettingsFromBackend() {
  try {
    const data = await getJson("/warp-studio/updater/config");
    const cfg = data.config || {};
    if (cfg.hasApiKey) {
      setSetting(SETTINGS.apiKey, "***HIDDEN***");
    } else {
      setSetting(SETTINGS.apiKey, "");
    }
    setSetting(SETTINGS.cacheTtlMinutes, Number(cfg.cacheTtlMinutes ?? 240));
    setSetting(SETTINGS.requestTimeoutSeconds, Number(cfg.requestTimeoutSeconds ?? 30));
    setSetting(SETTINGS.maxRetries, Number(cfg.maxRetries ?? 4));
    setSetting(SETTINGS.requestDelayMs, Number(cfg.requestDelayMs ?? 120));
    state.treatSidecarsAsInstalled = Boolean(cfg.treatSidecarsAsInstalled ?? true);
    setSetting(SETTINGS.treatSidecarsAsInstalled, state.treatSidecarsAsInstalled);
    setSetting(SETTINGS.useComfyPaths, Boolean(cfg.useComfyPaths ?? true));
    setSetting(SETTINGS.useExtraModelPaths, Boolean(cfg.useExtraModelPaths ?? true));
    setSetting(SETTINGS.useCustomPaths, Boolean(cfg.useCustomPaths ?? true));
    setSetting(SETTINGS.archiveFolder, String(cfg.archiveFolder || ""));
    state.savedArchiveFolder = savedArchiveFolder(data);
    const custom = cfg.customPaths || {};
    setSetting(SETTINGS.customCheckpoint, listToSettingString(custom.checkpoint));
    setSetting(SETTINGS.customLora, listToSettingString(custom.lora));
    setSetting(SETTINGS.customVae, listToSettingString(custom.vae));
    setSetting(SETTINGS.customUnet, listToSettingString(custom.unet));
    setSetting(SETTINGS.customEmbedding, listToSettingString(custom.embedding));
    setSetting(SETTINGS.fallbackCivarchive, Boolean(cfg.fallbackCivarchive ?? false));
    setSetting(SETTINGS.fallbackHuggingFace, listToSettingString(cfg.fallbackHuggingFace));
    setSetting(SETTINGS.fallbackAiToolkitFolders, listToSettingString(cfg.fallbackAiToolkitFolders));
    setSetting(SETTINGS.fallbackOwnCreator, String(cfg.fallbackOwnCreator || ""));
    state.matureMode = String(cfg.matureMode || "show");
    setSetting(SETTINGS.matureMode, state.matureMode);
    state.syncedNamingModel = String(cfg.namingModel || "");
    if (state.syncedNamingModel && !state.namingModels.includes(state.syncedNamingModel)) state.namingModels.push(state.syncedNamingModel);
    setSetting(SETTINGS.namingModel, state.syncedNamingModel);
    state.namingModelDirty = false;
    state.roots = data.effectiveRoots || {};
    state.suspendSettingsSync = false;
    return true;
  } catch (error) {
    console.warn("Civitai updater: failed to load settings; changes will not be saved until the page is reloaded", error);
    return false;
  }
}

function scheduleSettingsSync(immediate = false) {
  if (state.suspendSettingsSync) return;
  if (state.settingsSyncTimer) clearTimeout(state.settingsSyncTimer);
  state.settingsSyncTimer = setTimeout(() => syncSettingsToBackend(), immediate ? 0 : 300);
}

async function syncSettingsToBackend() {
  const apiKeyVal = String(getSetting(SETTINGS.apiKey, "") || "");
  const treatSidecarsAsInstalled = Boolean(getSetting(SETTINGS.treatSidecarsAsInstalled, true));
  const sidecarSettingChanged = state.treatSidecarsAsInstalled !== null
    && state.treatSidecarsAsInstalled !== treatSidecarsAsInstalled;
  const payload = {
    cacheTtlMinutes: Number(getSetting(SETTINGS.cacheTtlMinutes, 240)),
    requestTimeoutSeconds: Number(getSetting(SETTINGS.requestTimeoutSeconds, 30)),
    maxRetries: Number(getSetting(SETTINGS.maxRetries, 4)),
    requestDelayMs: Number(getSetting(SETTINGS.requestDelayMs, 120)),
    treatSidecarsAsInstalled,
    matureMode: String(getSetting(SETTINGS.matureMode, "show") || "show"),
    useComfyPaths: Boolean(getSetting(SETTINGS.useComfyPaths, true)),
    useExtraModelPaths: Boolean(getSetting(SETTINGS.useExtraModelPaths, true)),
    useCustomPaths: Boolean(getSetting(SETTINGS.useCustomPaths, true)),
    fallbackCivarchive: Boolean(getSetting(SETTINGS.fallbackCivarchive, false)),
    fallbackHuggingFace: parsePathSetting(getSetting(SETTINGS.fallbackHuggingFace, "malcolmrey")),
    fallbackAiToolkitFolders: parsePathSetting(getSetting(SETTINGS.fallbackAiToolkitFolders, "")),
    fallbackOwnCreator: String(getSetting(SETTINGS.fallbackOwnCreator, "") || "").trim(),
    archiveFolder: String(getSetting(SETTINGS.archiveFolder, "") || "").trim(),
    customPaths: {
      checkpoint: parsePathSetting(getSetting(SETTINGS.customCheckpoint, "")),
      lora: parsePathSetting(getSetting(SETTINGS.customLora, "")),
      vae: parsePathSetting(getSetting(SETTINGS.customVae, "")),
      unet: parsePathSetting(getSetting(SETTINGS.customUnet, "")),
      embedding: parsePathSetting(getSetting(SETTINGS.customEmbedding, "")),
    },
  };
  if (apiKeyVal !== "***HIDDEN***") {
    payload.apiKey = apiKeyVal;
  }
  if (state.namingModelDirty) {
    payload.namingModel = String(getSetting(SETTINGS.namingModel, "") || "");
  }
  try {
    const data = await postJson("/warp-studio/updater/config", payload);
    state.treatSidecarsAsInstalled = treatSidecarsAsInstalled;
    if ("namingModel" in payload) {
      // A pick made while this save was in flight still needs its own save.
      if (String(getSetting(SETTINGS.namingModel, "") || "") === payload.namingModel) state.namingModelDirty = false;
      state.syncedNamingModel = payload.namingModel;
      // The Rename view says whether the new naming model is usable.
      if (state.renameView && state.resultMode === "rename") state.renameView.refresh();
    }
    if (sidecarSettingChanged) {
      state.cacheFilesChanged = true;
    }
    state.roots = data.effectiveRoots || state.roots;
    // A refused folder is not in the answer: the ⋯ menu stops offering it.
    state.savedArchiveFolder = savedArchiveFolder(data);
    const pathWarnings = Array.isArray(data.pathWarnings) ? data.pathWarnings : [];
    if (pathWarnings.length) {
      console.warn("Civitai updater:", pathWarnings.join(" "));
      setStatus(pathWarnings.join(" "), "error");
    }
    if (state.rootEl) {
      renderRoots();
      renderCacheInfo();
    }
  } catch (error) {
    console.warn("Civitai updater: failed syncing settings", error);
  }
}

function parsePathSetting(value) {
  if (!value) return [];
  return String(value)
    .replace(/;/g, "\n")
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
}

function listToSettingString(value) {
  return Array.isArray(value) ? value.join("; ") : "";
}

function capitalize(str) {
  if (!str) return "";
  return str.charAt(0).toUpperCase() + str.slice(1);
}

function shortDate(isoString) {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  } catch (_) {
    return "";
  }
}

function timeAgo(isoString) {
  const ms = Date.now() - new Date(isoString).getTime();
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/* navigator.clipboard exists only in secure contexts, and ComfyUI is often
   served over plain HTTP on a LAN address, where it is undefined and the
   call would throw. Fall back to the selection-based copy there. */
async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // fall through to the legacy path
    }
  }
  try {
    const scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    const copied = document.execCommand("copy");
    scratch.remove();
    return copied;
  } catch (_) {
    return false;
  }
}

const COPIED_MS = 2500;

function copyPathTitle(path) {
  return `${path}\nClick to copy the path; click again to show it in its folder.`;
}

/* A file name link: the first click copies its path ("Copied ✓"); a second
   click while that shows opens the folder with the file selected. */
async function copyOrReveal(target, path, jobId) {
  if (!target || !path || target.dataset.cuPending) return; // a copy or an open is on its way
  // The link's own text, kept once: the temporary texts never replace it.
  if (target.dataset.cuName === undefined) target.dataset.cuName = target.textContent;
  const name = target.dataset.cuName;
  const show = (text, state) => {
    clearTimeout(target._cuCopiedTimer);
    target.textContent = text;
    target.dataset.cuCopied = state;
    target._cuCopiedTimer = setTimeout(() => {
      target.textContent = name;
      delete target.dataset.cuCopied;
    }, COPIED_MS);
  };
  const again = Boolean(target.dataset.cuCopied); // "Copied ✓" (or "Copy failed") still shows
  clearTimeout(target._cuCopiedTimer);
  target.textContent = name;
  delete target.dataset.cuCopied;
  target.dataset.cuPending = "1";
  try {
    if (again) {
      if (jobId) await postJson("/warp-studio/updater/reveal", { jobId, path });
    } else {
      const copied = await copyToClipboard(path);
      show(copied ? "Copied ✓" : "Copy failed", copied ? "1" : "error");
    }
  } catch (error) {
    setStatus(error?.message || String(error), "error");
    show("Can't open the folder", "error");
  } finally {
    delete target.dataset.cuPending;
  }
}

function isTabVisible() {
  return Boolean(state.rootEl && state.rootEl.offsetParent !== null);
}

function openLightbox(item) {
  closeLightbox();
  const localVersions = item.localVersions || [];
  const visibleNewVersions = item.newVersions || [];
  const hiddenNewVersions = item.hiddenNewVersions || [];
  const primaryNewVersion = visibleNewVersions[0] || hiddenNewVersions[0] || null;
  const hasComparison = localVersions.some((v) => v.previewUrl) && primaryNewVersion?.previewUrl;

  let sections = "";
  // Same order as the card: the online preview, then the local one, then "No preview".
  // `localFills[i]` is the file whose local preview the element marked `i` falls back to.
  const localFills = [];
  for (const v of localVersions) {
    if (!v.previewUrl && !v.hasLocalPreview) continue;
    const base = v.baseModel ? ` <span class="cu-lb-base">${escapeHtml(v.baseModel)}</span>` : "";
    const local = v.hasLocalPreview ? ` data-local-index="${localFills.length}"` : "";
    if (v.hasLocalPreview) localFills.push(v.modelPath);
    let media;
    if (!v.previewUrl) media = `<img${local} alt="">`;
    else if (v.previewType === "video") media = `<video src="${escapeHtml(v.previewUrl)}"${local} preload="auto" muted playsinline controls></video>`;
    else media = `<img src="${escapeHtml(v.previewUrl)}"${local} alt="">`;
    sections += `<div class="cu-lb-card"><div class="cu-lb-label">Local</div><div class="cu-lb-vname">${escapeHtml(v.versionName || "?")}${base}</div>${media}</div>`;
  }
  if (primaryNewVersion?.previewUrl) {
    const latestBase = primaryNewVersion.baseModel ? ` <span class="cu-lb-base">${escapeHtml(primaryNewVersion.baseModel)}</span>` : "";
    const media = primaryNewVersion.previewType === "video"
      ? `<video src="${escapeHtml(primaryNewVersion.previewUrl)}" preload="auto" muted playsinline controls></video>`
      : `<img src="${escapeHtml(primaryNewVersion.previewUrl)}" alt="">`;
    sections += `<div class="cu-lb-card"><div class="cu-lb-label" data-role="new">Newest release</div><div class="cu-lb-vname">${escapeHtml(primaryNewVersion.versionName || "?")}${latestBase}${renderPaidBadge(primaryNewVersion)}</div>${media}</div>`;
  }

  const overlay = document.createElement("div");
  overlay.className = "cu-lightbox";
  overlay.id = "cu-lightbox";
  overlay.innerHTML = `
    <div class="cu-lb-backdrop"></div>
    <div class="cu-lb-container">
      <div class="cu-lb-header">
        <span class="cu-lb-title">${escapeHtml(item.modelName || "Preview")}${item.creatorName ? ` <span class="cu-creator">by ${escapeHtml(item.creatorName)}</span>` : ""}</span>
        <button class="cu-lb-close" title="Close">&times;</button>
      </div>
      <div class="cu-lb-body ${hasComparison ? "cu-lb-compare" : ""}">
        ${sections || '<div style="color:#5a6a85;font-style:italic;padding:20px">No previews available</div>'}
      </div>
    </div>`;

  document.body.appendChild(overlay);
  const ownUrls = new Set();
  state.lightboxLocalUrls = ownUrls;
  const showNoPreview = (el) => el.replaceWith(Object.assign(document.createElement("div"), {
    className: "cu-lb-empty",
    textContent: "No preview",
  }));
  const loadLocal = (el, modelPath) => {
    fetchLocalPreview(modelPath, ownUrls).then((url) => {
      if (state.lightboxLocalUrls !== ownUrls) {
        // Closed while the request was in flight.
        if (url) URL.revokeObjectURL(url);
        return;
      }
      if (!url) {
        showNoPreview(el);
        return;
      }
      const img = Object.assign(document.createElement("img"), { alt: "" });
      img.addEventListener("error", () => showNoPreview(img), { once: true });
      img.src = url;
      el.replaceWith(img);
    });
  };
  localFills.forEach((modelPath, index) => {
    const el = overlay.querySelector(`[data-local-index="${index}"]`);
    if (!el.getAttribute("src")) loadLocal(el, modelPath);
    else el.addEventListener("error", () => loadLocal(el, modelPath), { once: true });
  });
  requestAnimationFrame(() => overlay.classList.add("cu-lb-open"));

  overlay.querySelector(".cu-lb-backdrop").addEventListener("click", closeLightbox);
  overlay.querySelector(".cu-lb-close").addEventListener("click", closeLightbox);

  const onKey = (e) => { if (e.key === "Escape") closeLightbox(); };
  document.addEventListener("keydown", onKey);
  state.lightboxKeyHandler = onKey;
}

function closeLightbox() {
  const el = document.getElementById("cu-lightbox");
  if (el) el.remove();
  if (state.lightboxLocalUrls) {
    revokeLocalPreviews(state.lightboxLocalUrls);
    state.lightboxLocalUrls = null;
  }
  // Every way of closing must drop the key handler, or one accumulates per
  // open and they all fire on the next Escape.
  if (state.lightboxKeyHandler) {
    document.removeEventListener("keydown", state.lightboxKeyHandler);
    state.lightboxKeyHandler = null;
  }
}

async function getJson(path) {
  const response = await api.fetchApi(path, { headers: guardHeaders() });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

async function postJson(path, payload) {
  const response = await api.fetchApi(path, {
    method: "POST",
    headers: guardHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

function revokeLocalPreviews(urls) {
  for (const url of urls) URL.revokeObjectURL(url);
  urls.clear();
}

// The shared previews belong to one job's results: drop them when it changes
// (or the results are cleared), so a poll's re-render reuses them instead.
function syncLocalPreviewCache() {
  if (state.localPreviewJobId === state.checkJobId) return;
  revokeLocalPreviews(state.localPreviewUrls);
  state.localPreviewCache.clear();
  state.localPreviewJobId = state.checkJobId;
}

// A listed file's preview that lives next to it on disk. Resolves to an object
// URL, or null when the server has none. Pass a set to own the URL yourself.
function fetchLocalPreview(path, urls = state.localPreviewUrls) {
  if (!state.checkJobId || !path) return Promise.resolve(null);
  const shared = urls === state.localPreviewUrls;
  if (shared) syncLocalPreviewCache();
  if (shared && state.localPreviewCache.has(path)) return state.localPreviewCache.get(path);
  const jobId = state.checkJobId;
  const request = (async () => {
    try {
      const response = await api.fetchApi("/warp-studio/updater/local-preview", {
        method: "POST",
        headers: guardHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ jobId, path }),
      });
      if (!response.ok) return null;
      const url = URL.createObjectURL(await response.blob());
      // Its job's previews were dropped while this was in flight.
      if (shared && state.localPreviewJobId !== jobId) {
        URL.revokeObjectURL(url);
        return null;
      }
      urls.add(url);
      return url;
    } catch (error) {
      return null;
    }
  })();
  if (shared) state.localPreviewCache.set(path, request);
  return request;
}

async function errorMessage(response) {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string" && data.error) return data.error;
  } catch (_) {
    // fall through to the generic status message
  }
  return `HTTP ${response.status}`;
}

function injectStyles() {
  if (document.getElementById("ws-updater-styles")) return;
  const style = document.createElement("style");
  style.id = "ws-updater-styles";
  style.textContent = `
    @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Space+Grotesk:wght@500;700&display=swap");

    .${TAB_ICON_CLASS} {
      display: inline-block;
      width: 1em;
      height: 1em;
      vertical-align: middle;
    }

    @supports ((mask: url("")) or (-webkit-mask: url(""))) {
      .${TAB_ICON_CLASS} {
        background-color: currentColor;
        -webkit-mask: url("${TAB_ICON_URL}") center / contain no-repeat;
        mask: url("${TAB_ICON_URL}") center / contain no-repeat;
      }

      .${TAB_ICON_CLASS}::before {
        content: none !important;
      }
    }

    /* ================================================================
       Civitai Updater — "release ledger" theme.
       Flat warm-ink surfaces, hairline seams, mono data columns.
       Red is reserved for one meaning: a new release exists.
       ================================================================ */

    .sidebar-content-container:has(> .cu-root),
    .sidebar-content-container:has(.cu-root) {
      scrollbar-gutter: stable;
    }

    .cu-root {
      --cu-ink: #131316;
      --cu-slate: #1b1b20;
      --cu-raise: #232329;
      --cu-seam: #2e2e36;
      --cu-seam-strong: #40404b;
      --cu-bone: #eceae4;
      --cu-ash: #96939c;
      --cu-dust: #6a6772;
      --cu-red: #ff4b3e;
      --cu-red-soft: #f2857b;
      --cu-red-dim: rgba(255, 75, 62, 0.12);
      --cu-moss: #93b578;
      --cu-amber: #d2a24c;
      --cu-h-ctl: 26px;
      --cu-h-btn: 30px;
      --cu-radius: 5px;
      --cu-mono: "IBM Plex Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
      --cu-disp: "Space Grotesk", "Segoe UI", system-ui, sans-serif;
      --cu-body: "Segoe UI", system-ui, -apple-system, sans-serif;
      font-family: var(--cu-body);
      color: var(--cu-bone);
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px;
      font-size: 12px;
      line-height: 1.45;
      background: var(--cu-ink);
      container: cu-root / inline-size;
    }

    .cu-root ::selection {
      background: var(--cu-red-dim);
    }

    /* ---- Masthead ---- */

    .cu-hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 2px 10px;
      border-bottom: 1px solid var(--cu-seam);
    }

    .cu-hero-title {
      font-family: var(--cu-disp);
      font-size: 14px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--cu-bone);
      margin: 0;
    }

    .cu-hero-title::after {
      content: ".";
      color: var(--cu-red);
      letter-spacing: 0;
    }

    /* ---- Cards ---- */

    .cu-card {
      border: 1px solid var(--cu-seam);
      border-radius: 6px;
      padding: 11px;
      background: var(--cu-slate);
    }

    .cu-card > h3,
    .cu-head h3,
    .cu-label {
      margin: 0;
      font-family: var(--cu-mono);
      color: var(--cu-dust);
      font-size: 9.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .cu-card > h3 {
      margin-bottom: 8px;
    }

    .cu-label {
      margin: 10px 0 5px 0;
    }

    .cu-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      min-height: var(--cu-h-ctl);
      margin-bottom: 9px;
    }

    .cu-head .cu-label {
      margin: 0;
    }

    .cu-small {
      font-size: 11px;
      color: var(--cu-ash);
    }

    .cu-small strong {
      color: var(--cu-bone);
      font-weight: 600;
    }

    .cu-divider {
      border: none;
      border-top: 1px solid var(--cu-seam);
      margin: 10px 0;
    }

    .cu-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      align-items: center;
    }

    /* ---- Buttons ---- */

    .cu-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: var(--cu-h-btn);
      padding: 0 12px;
      font-family: var(--cu-disp);
      font-weight: 600;
      font-size: 11.5px;
      letter-spacing: 0.015em;
      line-height: 1;
      border: 1px solid var(--cu-seam-strong);
      border-radius: var(--cu-radius);
      background: var(--cu-raise);
      color: var(--cu-bone);
      cursor: pointer;
      white-space: nowrap;
      transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
    }

    .cu-btn:hover {
      border-color: var(--cu-dust);
      background: #2a2a31;
    }

    .cu-btn:disabled {
      opacity: 0.35;
      cursor: default;
      pointer-events: none;
    }

    .cu-btn-primary {
      border-color: #e63d31;
      background: linear-gradient(180deg, #ff5749 0%, #f0392c 100%);
      color: #1a0705;
      font-weight: 700;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.24), 0 1px 2px rgba(0, 0, 0, 0.45);
    }

    .cu-btn-primary:hover {
      border-color: #ff6558;
      background: linear-gradient(180deg, #ff6659 0%, #f8483a 100%);
    }

    .cu-btn-primary:active {
      background: linear-gradient(180deg, #ef3e31 0%, #e5342a 100%);
      box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.35);
    }

    /* Quiet utility action, deliberately subordinate to the lead button. */
    .cu-btn-ghost {
      height: var(--cu-h-ctl);
      padding: 0 10px;
      font-size: 11px;
      font-weight: 500;
      background: transparent;
      border-color: var(--cu-seam);
      color: var(--cu-ash);
    }

    .cu-btn-ghost:hover {
      background: var(--cu-raise);
      border-color: var(--cu-seam-strong);
      color: var(--cu-bone);
    }

    .cu-btn-danger {
      background: transparent;
      border-color: rgba(255, 75, 62, 0.35);
      color: var(--cu-red-soft);
    }

    .cu-btn-danger:hover {
      background: var(--cu-red-dim);
      border-color: var(--cu-red);
      color: var(--cu-red-soft);
    }

    .cu-btn-sm {
      height: var(--cu-h-ctl);
      padding: 0 10px;
      font-size: 11px;
    }

    .cu-text-btn {
      background: none;
      border: none;
      color: var(--cu-ash);
      cursor: pointer;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 0;
      transition: color 0.15s ease;
    }

    a.cu-text-btn {
      text-decoration: none;
    }

    .cu-text-btn:hover {
      color: var(--cu-bone);
      text-decoration: underline;
    }

    /* ---- Header, settings, job bar ---- */

    .cu-root [hidden] {
      display: none !important;
    }

    .cu-gear {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      width: var(--cu-h-ctl);
      height: var(--cu-h-ctl);
      margin: 0;
      padding: 0;
      border: 1px solid var(--cu-seam-strong);
      border-radius: var(--cu-radius);
      background: var(--cu-raise);
      color: var(--cu-ash);
      font-size: 14px;
      line-height: 1;
      cursor: pointer;
      transition: border-color 0.15s ease, color 0.15s ease;
    }

    .cu-gear:hover,
    .cu-gear[aria-expanded="true"] {
      border-color: var(--cu-dust);
      color: var(--cu-bone);
    }

    .cu-settings-card {
      margin: 0;
    }

    /* Only while a job runs, in every tab. */
    .cu-job-bar {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-bottom: 9px;
    }

    .cu-job-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 22px;
    }

    .cu-job-text {
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-family: var(--cu-mono);
      font-size: 10.5px;
      color: var(--cu-ash);
    }

    /* Both buttons the size of the toolbar's controls: Check for Updates keeps
       its colour, not a bigger size. */
    .cu-action-bar .cu-btn {
      display: block;
      min-width: 0;
      height: var(--cu-h-ctl);
      padding: 0 8px;
      overflow: hidden;
      font-size: 11px;
      text-align: center;
      text-overflow: ellipsis;
      line-height: calc(var(--cu-h-ctl) - 2px);
    }

    .cu-action-bar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 6px;
      margin-bottom: 9px;
    }

    /* The ⋯ button and its menu, which opens under it like the toolbar's. */
    .cu-more {
      position: relative;
    }

    .cu-more .cu-btn {
      min-width: var(--cu-h-ctl);
    }

    .cu-more-menu .cu-filter-panel {
      left: auto;
      right: 0;
      width: max-content;
      max-width: 240px;
      padding: 4px;
    }

    .cu-more-item {
      display: block;
      width: 100%;
      padding: 6px 8px;
      border: 0;
      border-radius: 4px;
      background: none;
      color: var(--cu-bone);
      font: inherit;
      font-size: 11px;
      text-align: left;
      cursor: pointer;
    }

    .cu-more-item:hover:not(:disabled),
    .cu-more-item:focus-visible {
      background: var(--cu-seam);
    }

    .cu-more-item:disabled {
      color: var(--cu-dust);
      cursor: default;
    }

    .cu-job-controls {
      flex: 0 0 auto;
      gap: 6px;
      align-items: center;
    }

    /* ---- Progress ---- */

    .cu-progress-wrap {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .cu-progress {
      flex: 1;
      height: 3px;
      background: var(--cu-seam);
      overflow: hidden;
    }

    .cu-progress-fill {
      height: 100%;
      width: 0;
      background: var(--cu-red);
      transition: width 200ms linear;
    }

    .cu-progress-pct {
      font-family: var(--cu-mono);
      font-size: 10px;
      color: var(--cu-ash);
      min-width: 30px;
      text-align: right;
    }

    .cu-progress-eta {
      font-family: var(--cu-mono);
      font-size: 10px;
      color: var(--cu-ash);
      opacity: 0.75;
      min-width: 84px;
      text-align: right;
      white-space: nowrap;
    }

    /* ---- Sidecar warnings ---- */

    .cu-sidecar-warnings {
      display: none;
      margin: 2px 0 8px;
    }

    .cu-warning-panel {
      border: 1px solid rgba(210, 162, 76, 0.4);
      border-radius: 5px;
      background: rgba(210, 162, 76, 0.06);
      overflow: hidden;
    }

    .cu-warning-panel > summary {
      padding: 7px 9px;
      color: var(--cu-amber);
      cursor: pointer;
      font-size: 11px;
      font-weight: 600;
      user-select: none;
      list-style: none;
    }

    .cu-warning-panel > summary::-webkit-details-marker {
      display: none;
    }

    .cu-sidecar-warning-list {
      display: flex;
      flex-direction: column;
      gap: 7px;
      padding: 0 9px 9px;
    }

    .cu-sidecar-warning-row {
      border-top: 1px solid rgba(210, 162, 76, 0.2);
      padding-top: 7px;
    }

    .cu-sidecar-warning-name {
      color: var(--cu-bone);
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    .cu-sidecar-warning-message {
      margin-top: 2px;
      color: var(--cu-ash);
      font-size: 11px;
    }

    .cu-sidecar-warning-path {
      display: block;
      margin-top: 4px;
      color: var(--cu-dust);
      font-family: var(--cu-mono);
      font-size: 9px;
      white-space: normal;
      overflow-wrap: anywhere;
    }

    /* ---- Tooltips ---- */

    .cu-tooltip {
      position: relative;
    }

    .cu-tooltip::after {
      content: attr(data-tooltip);
      position: absolute;
      bottom: 130%;
      left: 50%;
      transform: translateX(-50%);
      background: #26262c;
      border: 1px solid var(--cu-seam-strong);
      color: var(--cu-bone);
      padding: 6px 9px;
      border-radius: 4px;
      font-family: var(--cu-body);
      font-size: 10.5px;
      font-weight: 400;
      letter-spacing: 0;
      white-space: normal;
      width: max-content;
      max-width: 220px;
      z-index: 99999;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.12s ease;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.55);
      line-height: 1.35;
      text-transform: none;
      text-align: left;
    }

    .cu-tooltip:hover::after {
      opacity: 1;
    }

    .cu-info-trigger {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: var(--cu-raise);
      color: var(--cu-dust);
      font-size: 9px;
      cursor: help;
      margin-left: 6px;
      vertical-align: middle;
      transition: color 0.15s ease, background-color 0.15s ease;
    }

    .cu-info-trigger:hover {
      background: var(--cu-seam-strong);
      color: var(--cu-bone);
    }

    /* ---- Status line ---- */

    .cu-status:empty {
      display: none;
    }

    .cu-status {
      margin-bottom: 9px;
      padding: 6px 9px;
      border-left: 2px solid var(--cu-amber);
      color: var(--cu-amber);
      font-size: 11px;
      overflow-wrap: anywhere;
    }

    /* ---- Scan report ---- */

    .cu-scan-report {
      display: none;
      margin-bottom: 8px;
      border: 1px solid var(--cu-seam);
      border-radius: 5px;
      background: var(--cu-ink);
      padding: 8px 10px;
    }

    .cu-scan-report.is-visible {
      display: block;
    }

    /* ---- Results header ---- */

    .cu-summary {
      font-family: var(--cu-mono);
      font-size: 10.5px;
      color: var(--cu-ash);
      margin-bottom: 8px;
    }

    /* ---- Loading: a bar sweeping under the tabs ---- */

    .cu-loading-bar {
      position: relative;
      height: 2px;
      margin: -6px 0 6px;
      overflow: hidden;
      border-radius: 1px;
      visibility: hidden;
    }

    .cu-loading-bar.is-on {
      visibility: visible;
    }

    .cu-loading-bar::after {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 35%;
      background: linear-gradient(90deg, transparent, var(--cu-red-soft), transparent);
    }

    /* Only while it shows: a hidden bar would keep sweeping for nothing. */
    .cu-loading-bar.is-on::after {
      animation: cu-sweep 2.6s cubic-bezier(0.45, 0, 0.55, 1) infinite;
    }

    @keyframes cu-sweep {
      from { transform: translateX(-100%); }
      to { transform: translateX(300%); }
    }

    .cu-loading-note {
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--cu-mono);
      font-size: 10.5px;
      color: var(--cu-ash);
      padding: 10px 0;
    }

    .cu-spinner {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      border: 2px solid var(--cu-seam-strong);
      border-top-color: var(--cu-red-soft);
      animation: cu-spin 1.6s linear infinite;
    }

    @keyframes cu-spin {
      to { transform: rotate(360deg); }
    }

    /* ---- Updates / Issues switch ---- */

    .cu-view-switch {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: 1fr;
      margin-bottom: 8px;
      border: 1px solid var(--cu-seam-strong);
      border-radius: var(--cu-radius);
      overflow: hidden;
    }

    .cu-view-tab {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      height: var(--cu-h-ctl);
      margin: 0;
      padding: 0 8px;
      border: 0;
      background: transparent;
      color: var(--cu-ash);
      font-family: var(--cu-body);
      font-size: 11px;
      cursor: pointer;
      transition: background-color 120ms ease, color 120ms ease;
    }

    .cu-view-tab + .cu-view-tab {
      border-left: 1px solid var(--cu-seam-strong);
    }

    .cu-view-tab:hover {
      color: var(--cu-bone);
    }

    .cu-view-tab[aria-selected="true"] {
      background: var(--cu-raise);
      color: var(--cu-bone);
    }

    .cu-view-count {
      font-family: var(--cu-mono);
      font-size: 9.5px;
      font-variant-numeric: tabular-nums;
      color: var(--cu-dust);
    }

    .cu-view-tab[data-view="updates"][aria-selected="true"] .cu-view-count {
      color: var(--cu-red-soft);
    }

    .cu-view-tab[data-view="issues"][aria-selected="true"] .cu-view-count {
      color: var(--cu-amber);
    }

    /* Native select chrome differs per platform; draw our own caret so a
       select and a filter menu are the same object visually. */
    /* ---- Checkbox: drawn, not inherited from the platform ---- */

    .cu-root input[type="checkbox"] {
      -webkit-appearance: none;
      appearance: none;
      display: inline-grid;
      place-content: center;
      box-sizing: border-box;
      width: 13px;
      height: 13px;
      margin: 0;
      flex: 0 0 auto;
      border: 1px solid var(--cu-seam-strong);
      border-radius: 3px;
      background: var(--cu-ink);
      cursor: pointer;
      transition: background-color 120ms ease, border-color 120ms ease;
    }

    .cu-root input[type="checkbox"]:hover {
      border-color: var(--cu-dust);
    }

    .cu-root input[type="checkbox"]:checked {
      background: #b7b4bd;
      border-color: #b7b4bd;
    }

    .cu-root input[type="checkbox"]::after {
      content: "";
      width: 3px;
      height: 6px;
      margin-top: -2px;
      border: solid var(--cu-ink);
      border-width: 0 1.6px 1.6px 0;
      transform: rotate(45deg) scale(0);
      transition: transform 120ms ease;
    }

    .cu-root input[type="checkbox"]:checked::after {
      transform: rotate(45deg) scale(1);
    }

    /* ---- Toolbar: 🔍, Filter, Sort, ⓘ ---- */

    .cu-tb {
      position: relative;
      margin-bottom: 9px;
    }

    .cu-tb-row {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .cu-tb-end {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-left: auto;
    }

    .cu-tb-btn {
      display: inline-flex;
      align-items: center;
      flex: 0 1 auto;
      box-sizing: border-box;
      height: var(--cu-h-ctl);
      min-width: 0;
      margin: 0;
      padding: 0 9px 0 8px;
      border: 1px solid var(--cu-seam-strong);
      border-radius: var(--cu-radius);
      background: var(--cu-raise);
      color: var(--cu-bone);
      font-family: var(--cu-body);
      font-size: 11px;
      line-height: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      cursor: pointer;
      transition: border-color 0.15s ease;
    }

    .cu-tb-btn:hover {
      border-color: var(--cu-dust);
    }

    .cu-tb-btn[aria-expanded="true"] {
      border-color: var(--cu-dust);
      background: #26262d;
    }

    /* A search that is hiding things stays lit, with its text. */
    .cu-tb-btn[data-active] {
      border-color: rgba(147, 181, 120, 0.55);
      color: var(--cu-moss);
    }

    .cu-tb-query {
      margin-left: 6px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: var(--cu-mono);
      font-size: 10px;
    }

    .cu-tb-info {
      flex: 0 0 auto;
      justify-content: center;
      width: var(--cu-h-ctl);
      margin-left: auto;
      padding: 0;
      color: var(--cu-ash);
    }

    .cu-tb-field {
      display: block;
      box-sizing: border-box;
      width: 100%;
      height: var(--cu-h-ctl);
      margin: 6px 0 0;
      padding: 0 9px 0 8px;
      border: 1px solid var(--cu-seam-strong);
      border-radius: var(--cu-radius);
      background: var(--cu-raise);
      color: var(--cu-bone);
      font-family: var(--cu-body);
      font-size: 11px;
      transition: border-color 0.15s ease;
    }

    .cu-tb-field::placeholder {
      color: var(--cu-dust);
    }

    .cu-tb-field:hover,
    .cu-root .cu-tb-field:focus {
      outline: none;
      border-color: var(--cu-dust);
    }

    .cu-tb-title {
      margin: 0 0 5px;
      font-family: var(--cu-mono);
      color: var(--cu-dust);
      font-size: 9.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .cu-tb-section + .cu-tb-section {
      margin-top: 9px;
      padding-top: 9px;
      border-top: 1px solid var(--cu-seam);
    }

    .cu-tb-menu .cu-filter-panel {
      max-height: min(70vh, 440px);
      overflow: auto;
    }

    .cu-tb-menu[data-tb-menu="info"] .cu-filter-panel {
      display: flex;
      flex-direction: column;
      gap: 6px;
      left: auto;
      right: 0;
      width: max-content;
    }

    .cu-tb-line {
      font-family: var(--cu-mono);
      font-size: 10.5px;
      color: var(--cu-ash);
      overflow-wrap: anywhere;
    }

    .cu-tb-act {
      align-self: flex-start;
      margin-top: 2px;
    }

    .cu-root input[type="radio"] {
      -webkit-appearance: none;
      appearance: none;
      display: inline-grid;
      place-content: center;
      box-sizing: border-box;
      width: 13px;
      height: 13px;
      margin: 0;
      flex: 0 0 auto;
      border: 1px solid var(--cu-seam-strong);
      border-radius: 50%;
      background: var(--cu-ink);
      cursor: pointer;
      transition: border-color 120ms ease;
    }

    .cu-root input[type="radio"]:hover {
      border-color: var(--cu-dust);
    }

    .cu-root input[type="radio"]:checked {
      border-color: #b7b4bd;
    }

    .cu-root input[type="radio"]::after {
      content: "";
      width: 5px;
      height: 5px;
      border-radius: 50%;
      background: #b7b4bd;
      transform: scale(0);
      transition: transform 120ms ease;
    }

    .cu-root input[type="radio"]:checked::after {
      transform: scale(1);
    }

    .cu-filter-panel {
      position: absolute;
      z-index: 5;
      top: calc(100% + 4px);
      left: 0;
      min-width: 180px;
      max-width: 260px;
      border: 1px solid var(--cu-seam-strong);
      border-radius: 6px;
      background: #202026;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
      padding: 8px;
      animation: cu-fade-in 0.12s ease-out;
    }

    @keyframes cu-fade-in {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    .cu-filter-actions {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }

    .cu-filter-options {
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-height: 220px;
      overflow: auto;
    }

    .cu-filter-option {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--cu-bone);
      cursor: pointer;
    }

    .cu-filter-empty {
      font-size: 10.5px;
      color: var(--cu-dust);
    }

    /* ---- Results list ---- */

    .cu-results {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .cu-empty {
      color: var(--cu-dust);
      font-size: 11.5px;
      padding: 4px 0;
    }

    /* ---- Result cards ---- */

    .cu-item {
      display: grid;
      grid-template-columns: 56px 1fr;
      gap: 10px;
      border: 1px solid var(--cu-seam);
      border-radius: 5px;
      padding: 8px;
      background: rgba(0, 0, 0, 0.18);
      transition: border-color 0.15s ease;
    }

    .cu-item:hover {
      border-color: var(--cu-seam-strong);
    }

    .cu-thumb {
      width: 56px;
      height: 56px;
      border-radius: 4px;
      overflow: hidden;
      background: var(--cu-raise);
      border: 1px solid var(--cu-seam);
      flex-shrink: 0;
      cursor: zoom-in;
      position: relative;
    }

    .cu-thumb::after {
      content: "";
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.35);
      opacity: 0;
      transition: opacity 150ms;
      pointer-events: none;
    }

    .cu-thumb:hover::after {
      opacity: 1;
    }

    .cu-thumb img,
    .cu-thumb video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .cu-thumb-empty {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--cu-dust);
      font-family: var(--cu-mono);
      font-size: 8.5px;
      text-align: center;
    }

    .cu-item-body {
      min-width: 0;
    }

    /* Model header: name owns the full row, type sits at the right edge. */

    .cu-item-header {
      margin-bottom: 5px;
    }

    .cu-ver-row.cu-ver-row-model {
      display: flex;
      align-items: baseline;
      gap: 8px;
    }

    .cu-ver-row-model .cu-ver-date,
    .cu-ver-row-model .cu-ver-base {
      display: none;
    }

    .cu-ver-label.cu-ver-label-model {
      order: 2;
      margin-left: auto;
      flex-shrink: 0;
      padding: 0;
      background: transparent;
      border: 0;
      overflow: visible;
    }

    .cu-ver-label.cu-ver-label-model::before {
      content: none;
    }

    .cu-ver-row-model .cu-ver-main {
      order: 1;
      min-width: 0;
      flex: 1 1 auto;
    }

    .cu-item-header h4 {
      flex: 1 1 auto;
      margin: 0;
      font-family: var(--cu-disp);
      font-size: 12.5px;
      font-weight: 500;
      letter-spacing: 0.01em;
      color: var(--cu-bone);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }

    .cu-type-pill {
      display: inline-block;
      font-family: var(--cu-mono);
      font-size: 8.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--cu-dust);
      border: 1px solid var(--cu-seam);
      border-radius: 3px;
      padding: 2px 5px;
      line-height: 1.2;
    }

    .cu-creator {
      font-size: 10.5px;
      color: var(--cu-dust);
      font-weight: 400;
      white-space: nowrap;
      flex-shrink: 0;
    }

    /* In the card header the model name wins the fight for space. */

    .cu-ver-row-model .cu-creator {
      flex: 0 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .cu-provisional {
      font-family: var(--cu-mono);
      font-size: 8.5px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--cu-dust);
      border: 1px dashed var(--cu-seam-strong);
      border-radius: 3px;
      padding: 1px 5px;
      white-space: nowrap;
    }

    /* ---- The release rail ----
       Each version row is a dot on a shared rail:
       hollow = saved locally, red = new release,
       dust ring = metadata-only, filled gray = hidden. */

    .cu-versions {
      container: cu-vers / inline-size;
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 3px;
      font-size: 11px;
    }

    .cu-versions::before {
      content: "";
      position: absolute;
      left: 3px;
      top: 9px;
      bottom: 9px;
      width: 1px;
      background: var(--cu-seam);
    }

    /* The label column fits the longest label, "Downloaded", on every row alike. */
    .cu-ver-row {
      display: grid;
      grid-template-columns: 76px 60px minmax(0, 72px) minmax(0, 1fr);
      align-items: center;
      gap: 6px;
    }

    /* A new version's row is one line: label, date, base, the name, then the download slot and Hide.
       --lead holds the columns before the name. The actions get what is left after the name's
       90px; below the width where that is too little, they move under the name, right-aligned. */
    .cu-versions .cu-ver-row.cu-ver-row-acts {
      --lead: 76px 60px minmax(0, 72px);
      grid-template-columns: var(--lead) minmax(0, 1fr) fit-content(max(0px, calc(100cqw - 322px)));
    }

    /* The sidebar is not the viewport: adapt columns to the panel width. */

    @container cu-root (max-width: 430px) {
      .cu-versions .cu-ver-row {
        grid-template-columns: 76px 60px minmax(0, 1fr);
      }

      .cu-versions .cu-ver-row.cu-ver-row-acts {
        --lead: 76px 60px;
      }

      .cu-versions .cu-ver-base {
        display: none;
      }
    }

    @container cu-root (max-width: 330px) {
      .cu-versions .cu-ver-row {
        grid-template-columns: 76px minmax(0, 1fr);
      }

      .cu-versions .cu-ver-row.cu-ver-row-acts {
        --lead: 76px;
      }

      .cu-versions .cu-ver-date {
        display: none;
      }
    }

    @container cu-vers (max-width: 467px) {
      .cu-versions .cu-ver-row.cu-ver-row-acts {
        grid-template-columns: var(--lead) minmax(0, 1fr);
        row-gap: 3px;
      }

      .cu-versions .cu-ver-row-acts .cu-ver-acts {
        grid-column: 1 / -1;
        grid-row: 2;
        justify-self: end;
        max-width: 100%;
      }
    }

    .cu-ver-label {
      position: relative;
      display: flex;
      align-items: center;
      font-family: var(--cu-mono);
      font-size: 9px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      white-space: nowrap;
      overflow: hidden;
    }

    .cu-ver-label::before {
      content: "";
      flex-shrink: 0;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      margin-right: 7px;
      background: var(--cu-slate);
      border: 1.5px solid var(--cu-dust);
      box-sizing: border-box;
    }

    .cu-ver-label[data-role="saved"] {
      color: var(--cu-ash);
    }

    .cu-ver-label[data-role="saved"]::before {
      border-color: #b7b4bd;
    }

    .cu-ver-label[data-role="metadata"] {
      color: var(--cu-dust);
    }

    .cu-ver-label[data-role="metadata"]::before {
      border-color: var(--cu-dust);
    }

    /* Downloaded since the check: the card stays in Updates until the next one. */
    .cu-ver-label[data-role="downloaded"] {
      color: var(--cu-moss);
    }

    .cu-ver-label[data-role="downloaded"]::before {
      background: var(--cu-moss);
      border-color: var(--cu-moss);
    }

    .cu-ver-label[data-role="archived"] {
      color: var(--cu-ash);
    }

    .cu-ver-label[data-role="archived"]::before {
      border-style: dashed;
      border-color: var(--cu-ash);
    }

    .cu-ver-label[data-role="duplicate"] {
      color: var(--cu-amber);
    }

    .cu-ver-label[data-role="duplicate"]::before {
      background: var(--cu-amber);
      border-color: var(--cu-amber);
    }

    /* An info file alone, its model in the archive: an issue, with a hollow dot (no weights). */
    .cu-ver-label[data-role="infoonly"] {
      color: var(--cu-amber);
    }

    .cu-ver-label[data-role="infoonly"]::before {
      border-style: dashed;
      border-color: var(--cu-amber);
    }

    .cu-ver-label[data-role="new"] {
      color: var(--cu-red-soft);
    }

    .cu-ver-label[data-role="new"]::before {
      background: var(--cu-red);
      border-color: var(--cu-red);
      box-shadow: 0 0 6px rgba(255, 75, 62, 0.55);
    }

    .cu-ver-label[data-role="hidden"] {
      color: var(--cu-dust);
    }

    .cu-ver-label[data-role="hidden"]::before {
      background: var(--cu-seam-strong);
      border-color: var(--cu-seam-strong);
    }

    /* Issues are amber, never red: red means a new release exists. */

    .cu-ver-label[data-role="error"] {
      color: var(--cu-amber);
    }

    .cu-ver-label[data-role="error"]::before {
      background: var(--cu-amber);
      border-color: var(--cu-amber);
    }

    .cu-ver-label[data-role="nomatch"] {
      color: var(--cu-ash);
    }

    .cu-ver-label[data-role="nomatch"]::before {
      border-style: dashed;
      border-color: var(--cu-ash);
    }

    /* The reason sits under its row, aligned with the columns after the label. */

    .cu-issue-msg {
      margin: -3px 0 1px 78px;
      font-family: var(--cu-mono);
      font-size: 9.5px;
      line-height: 1.4;
      color: var(--cu-dust);
      overflow-wrap: anywhere;
    }

    .cu-ver-date,
    .cu-ver-base {
      font-family: var(--cu-mono);
      font-variant-numeric: tabular-nums;
      font-size: 9.5px;
      color: var(--cu-dust);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .cu-ver-main {
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 6px;
      overflow: hidden;
    }

    .cu-ver-link {
      color: var(--cu-red-soft);
      cursor: pointer;
      text-decoration: none;
      transition: color 100ms;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
      flex: 1 1 auto;
    }

    .cu-ver-link:hover {
      color: var(--cu-red);
      text-decoration: underline;
    }

    /* Saved rows: the name copies a file path, it doesn't open Civitai. */

    .cu-copy-path {
      color: var(--cu-ash);
      border-bottom: 1px dotted var(--cu-seam-strong);
    }

    .cu-copy-path:hover {
      color: var(--cu-bone);
      text-decoration: none;
      border-bottom-color: var(--cu-dust);
    }

    .cu-access-badge {
      font-family: var(--cu-mono);
      font-size: 8.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--cu-amber);
      background: rgba(210, 162, 76, 0.08);
      border: 1px solid rgba(210, 162, 76, 0.3);
      border-radius: 3px;
      padding: 1px 5px;
      white-space: nowrap;
      flex: 0 0 auto;
    }

    /* Self-contained (no panel tokens) so it also renders in the lightbox. */

    .cu-buzz {
      font-family: "IBM Plex Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
      font-size: 8.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #d2a24c;
      background: rgba(210, 162, 76, 0.07);
      border: 1px dashed rgba(210, 162, 76, 0.5);
      border-radius: 3px;
      padding: 1px 5px;
      white-space: nowrap;
      flex: 0 0 auto;
    }

    .cu-buzz[data-paid="permanent"] {
      border-style: solid;
      background: rgba(210, 162, 76, 0.16);
    }

    .cu-lb-vname .cu-buzz {
      margin-left: 6px;
      vertical-align: 1px;
    }

    .cu-inline-btn {
      border: 1px solid var(--cu-seam-strong);
      background: transparent;
      color: var(--cu-dust);
      border-radius: 4px;
      font-family: var(--cu-body);
      font-size: 10px;
      padding: 1px 7px;
      cursor: pointer;
      white-space: nowrap;
      flex: 0 0 auto;
      transition: color 0.15s ease, border-color 0.15s ease;
    }

    /* Download/Replace and Hide sit at the right end of the row; the name gives way to them. */
    .cu-ver-acts {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
      height: 20px;
      min-width: 0;
      flex: 0 1 auto;
    }

    /* The actions never take more than 46% of the row (fit-content above): a long
       note gives way with an ellipsis, so the version name keeps some characters. */
    .cu-dl {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      flex: 0 1 auto;
    }

    .cu-dl-note {
      font-family: var(--cu-mono);
      font-size: 9.5px;
      color: var(--cu-dust);
      white-space: nowrap;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .cu-dl-msg:empty {
      display: none;
    }

    .cu-inline-btn:hover {
      border-color: var(--cu-dust);
      color: var(--cu-bone);
    }

    .cu-ver-row.is-hidden {
      opacity: 0.6;
    }

    /* ---- Pagination ---- */

    .cu-pagination {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      margin-top: 8px;
    }

    .cu-page-btn {
      background: var(--cu-raise);
      border: 1px solid var(--cu-seam-strong);
      border-radius: 4px;
      color: var(--cu-bone);
      cursor: pointer;
      font-size: 13px;
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      line-height: 1;
      transition: border-color 0.15s ease;
    }

    .cu-page-btn:hover {
      border-color: var(--cu-dust);
    }

    .cu-page-btn:disabled {
      opacity: 0.3;
      cursor: default;
      pointer-events: none;
    }

    .cu-page-info {
      font-family: var(--cu-mono);
      font-size: 10.5px;
      color: var(--cu-ash);
    }

    /* ---- Settings: scope chips + options ---- */

    .cu-chip {
      border: 1px solid var(--cu-seam-strong);
      border-radius: 4px;
      padding: 3px 7px;
      display: inline-flex;
      gap: 5px;
      align-items: center;
      background: var(--cu-raise);
      color: var(--cu-ash);
      font-size: 11px;
      cursor: pointer;
      transition: border-color 0.15s ease, color 0.15s ease;
    }

    .cu-chip:hover {
      border-color: var(--cu-dust);
      color: var(--cu-bone);
    }

    .cu-option {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--cu-ash);
      font-size: 11.5px;
      cursor: pointer;
      margin: 4px 0;
    }

    .cu-option-hint {
      margin: -2px 0 4px 0;
      padding-left: 22px;
      font-size: 10.5px;
      color: var(--cu-dust);
      line-height: 1.3;
    }

    .cu-settings-body {
      margin-top: 8px;
    }

    /* ---- Resolved roots ---- */

    .cu-roots-sum {
      font-family: var(--cu-mono);
      font-size: 9.5px;
      color: var(--cu-dust);
    }

    .cu-roots {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-top: 6px;
      font-family: var(--cu-mono);
      font-size: 9.5px;
      color: var(--cu-ash);
    }

    .cu-root-row {
      display: grid;
      grid-template-columns: 80px 1fr;
      gap: 6px;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--cu-seam);
      overflow-wrap: anywhere;
    }

    .cu-root-type {
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--cu-dust);
      font-size: 9px;
    }


    /* ---- Group bands ----
       Hierarchy is carried by weight and colour, not indentation: a sidebar
       has no horizontal room to spend on nesting. */

    .cu-group-head-wrap {
      margin: 13px 0 1px;
    }

    .cu-results > .cu-group-head-wrap:first-child {
      margin-top: 1px;
    }

    .cu-group-head,
    .cu-subgroup-head {
      display: flex;
      align-items: center;
      gap: 7px;
      width: 100%;
      padding: 0 0 5px 0;
      background: none;
      border: none;
      cursor: pointer;
      text-align: left;
      color: inherit;
    }

    .cu-group-head {
      border-bottom: 1px solid var(--cu-seam-strong);
    }

    .cu-group-caret {
      flex: 0 0 auto;
      font-size: 8px;
      line-height: 1;
      color: var(--cu-dust);
      transition: transform 140ms ease;
    }

    [data-collapsed="true"] .cu-group-caret {
      transform: rotate(-90deg);
    }

    .cu-group-name {
      font-family: var(--cu-mono);
      font-size: 10px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--cu-bone);
    }

    .cu-group-count,
    .cu-subgroup-count {
      margin-left: auto;
      font-family: var(--cu-mono);
      font-size: 9.5px;
      color: var(--cu-dust);
      white-space: nowrap;
    }

    /* The count of waiting releases keeps the release colour. */
    .cu-group-count b {
      font-weight: 500;
      color: var(--cu-red-soft);
    }

    .cu-group-cont {
      font-family: var(--cu-mono);
      font-size: 8.5px;
      letter-spacing: 0.08em;
      color: var(--cu-dust);
      border: 1px solid var(--cu-seam-strong);
      border-radius: 3px;
      padding: 0 4px;
      line-height: 1.5;
    }

    .cu-subgroup-head-wrap {
      margin: 9px 0 0;
      padding-left: 9px;
      border-left: 1px solid var(--cu-seam);
    }

    .cu-subgroup-name {
      font-family: var(--cu-mono);
      font-size: 9.5px;
      letter-spacing: 0.08em;
      color: var(--cu-ash);
    }

    .cu-group-head:hover .cu-group-name,
    .cu-subgroup-head:hover .cu-subgroup-name {
      color: #ffffff;
    }

    .cu-mature-note {
      margin-top: 10px;
      font-family: var(--cu-mono);
      font-size: 9.5px;
      color: var(--cu-dust);
    }

    /* ---- Mature previews: blur the picture, never the information ---- */

    .cu-item[data-mature="blur"] .cu-thumb img,
    .cu-item[data-mature="blur"] .cu-thumb video {
      filter: blur(10px) saturate(0.6);
      transform: scale(1.06);
    }

    .cu-item[data-mature="blur"] .cu-thumb::before {
      content: "MATURE";
      position: absolute;
      inset: auto 0 4px 0;
      z-index: 1;
      text-align: center;
      font-family: var(--cu-mono);
      font-size: 7.5px;
      letter-spacing: 0.09em;
      color: var(--cu-bone);
      text-shadow: 0 1px 3px rgba(0, 0, 0, 0.9);
      pointer-events: none;
    }

    /* ---- Gallery ----
       The Updates look: the summary line, one control, the group bands. Every
       box has a fixed size, so a card without a preview or a creator is as tall
       as the rest, and a thumbnail arriving late moves nothing. */

    .cu-g-top {
      margin-bottom: 9px;
    }

    /* The type switch, the note, the marks, the version chip and its list, folding. */
    .cu-g-types { display: flex; gap: 14px; margin-bottom: 9px; }
    .cu-g-type { padding: 0 0 3px; background: none; border: none; border-bottom: 1px solid transparent; color: var(--cu-dust); font-family: var(--cu-mono); font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer; }
    .cu-g-type:hover { color: var(--cu-bone); }
    .cu-g-type[aria-pressed="true"] { color: var(--cu-bone); border-bottom-color: var(--cu-seam-strong); }
    .cu-g-note { margin-bottom: 9px; }
    .cu-g-note:empty { display: none; }
    .cu-g .cu-g-band-head { cursor: pointer; }
    .cu-g .cu-g-band-head:hover .cu-group-name { color: #ffffff; }
    .cu-g-band:not([data-workflow="true"]) .cu-g-fit { visibility: hidden; }
    .cu-g-band[data-folded] .cu-g-grid { display: none; }
    .cu-g-mark { position: absolute; top: 34px; right: 6px; box-sizing: border-box; display: none; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 10px; border: 1px solid rgba(147, 181, 120, 0.55); background: rgba(19, 19, 22, 0.88); color: var(--cu-moss); font-size: 11px; line-height: 1; pointer-events: none; }
    .cu-g-mark[data-mark] { display: flex; }
    .cu-g-mark[data-mark="parked"] { border-color: var(--cu-seam-strong); color: var(--cu-dust); }
    .cu-g-card[data-mark="on"] .cu-g-pick { border-color: rgba(147, 181, 120, 0.55); }
    .cu-g-layer { position: absolute; inset: 0 0 auto 0; aspect-ratio: 3 / 4; pointer-events: none; }
    .cu-g-vers { position: absolute; right: 6px; bottom: 6px; pointer-events: auto; }
    .cu-g-vers { box-sizing: border-box; height: 16px; padding: 0 5px; border-radius: 3px; border: 1px solid var(--cu-seam-strong); background: rgba(19, 19, 22, 0.8); color: var(--cu-bone); font-family: var(--cu-mono); font-size: 9px; line-height: 1; cursor: pointer; }
    .cu-g-vers:hover, .cu-g-vers:focus-visible { border-color: var(--cu-dust); }
    .cu-g-vmenu { position: absolute; z-index: 3; inset: 0 0 auto 0; aspect-ratio: 3 / 4; box-sizing: border-box; display: flex; flex-direction: column; gap: 2px; padding: 4px; overflow: auto; border: 1px solid var(--cu-seam-strong); border-radius: 5px; background: rgba(19, 19, 22, 0.97); }
    .cu-g-vitem { display: flex; justify-content: space-between; gap: 6px; padding: 4px 5px; border: 1px solid transparent; border-radius: 3px; background: none; color: var(--cu-bone); font-family: var(--cu-mono); font-size: 9.5px; text-align: left; cursor: pointer; }
    .cu-g-vitem:hover, .cu-g-vitem:focus-visible { border-color: var(--cu-seam-strong); }
    .cu-g-vitem[aria-current="true"] { color: var(--cu-moss); }
    .cu-g-vname { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cu-g-vdate { flex: 0 0 auto; color: var(--cu-dust); }

    .cu-g-error {
      padding: 6px 9px;
      border-left: 2px solid var(--cu-amber);
      color: var(--cu-amber);
      font-size: 11px;
      overflow-wrap: anywhere;
    }

    .cu-g-band {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .cu-g-bands > .cu-g-band:first-child > .cu-group-head-wrap {
      margin-top: 1px;
    }

    .cu-g .cu-g-band-head {
      cursor: pointer;
    }

    .cu-g .cu-g-band-head .cu-group-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--cu-bone);
    }

    /* With a connected model, the bases that don't fit it step back. */
    .cu-g .cu-g-band[data-quiet] .cu-group-name {
      color: var(--cu-ash);
    }

    .cu-g-fit {
      flex: 0 0 auto;
      font-family: var(--cu-mono);
      font-size: 8.5px;
      letter-spacing: 0.08em;
      color: var(--cu-moss);
      border: 1px solid rgba(147, 181, 120, 0.35);
      border-radius: 3px;
      background: rgba(147, 181, 120, 0.07);
      padding: 0 4px;
      line-height: 1.5;
    }

    .cu-g-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 6px;
      align-items: start;
    }

    .cu-g-card {
      position: relative;
      min-width: 0;
    }

    /* The whole face is the button that puts the model in the workflow. */
    .cu-g-pick {
      display: flex;
      flex-direction: column;
      box-sizing: border-box;
      width: 100%;
      margin: 0;
      padding: 0;
      border: 1px solid var(--cu-seam);
      border-radius: 5px;
      background: rgba(0, 0, 0, 0.18);
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
      overflow: hidden;
      transition: border-color 0.15s ease;
    }

    .cu-g-pick:hover {
      border-color: var(--cu-seam-strong);
    }

    .cu-g-pick[aria-disabled="true"] {
      cursor: default;
    }

    .cu-g-pick[aria-disabled="true"]:hover {
      border-color: var(--cu-seam);
    }

    /* Not on this drive: dimmed, and it can't be added to a prompt. */
    .cu-g-card[data-missing] .cu-g-pick {
      opacity: 0.45;
      cursor: default;
    }

    .cu-g-thumb {
      position: relative;
      display: block;
      aspect-ratio: 3 / 4;
      overflow: hidden;
      background: var(--cu-raise);
      border-bottom: 1px solid var(--cu-seam);
    }

    .cu-g-thumb img {
      position: absolute;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .cu-g-noimg {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      padding: 10px;
      overflow: hidden;
      color: var(--cu-dust);
      font-family: var(--cu-mono);
      font-size: 9px;
      line-height: 1.4;
      text-align: center;
      overflow-wrap: anywhere;
    }

    /* Green, as trigger words are in the prompt. */
    .cu-g-badge {
      position: absolute;
      left: 6px;
      bottom: 6px;
      display: inline-flex;
      align-items: center;
      height: 16px;
      padding: 0 5px;
      border-radius: 3px;
      border: 1px solid rgba(147, 181, 120, 0.35);
      background: rgba(19, 19, 22, 0.8);
      color: var(--cu-moss);
      font-family: var(--cu-mono);
      font-size: 9px;
      line-height: 1;
    }

    /* Opposite the trigger badge, in the muted text colour. */
    .cu-g-metric {
      position: absolute;
      right: 6px;
      bottom: 6px;
      display: inline-flex;
      align-items: center;
      height: 16px;
      padding: 0 5px;
      border-radius: 3px;
      border: 1px solid var(--cu-seam-strong);
      background: rgba(19, 19, 22, 0.8);
      color: var(--cu-ash);
      font-family: var(--cu-mono);
      font-size: 9px;
      line-height: 1;
    }

    /* The "n versions ▾" chip (16px high, 6px up) holds the corner: the number sits above it. */
    .cu-g-metric.cu-g-metric-stacked {
      bottom: 26px;
    }

    .cu-g-creator,
    .cu-g-detail {
      display: block;
      height: 13px;
      margin: 0 7px;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      line-height: 13px;
      color: var(--cu-dust);
    }

    .cu-g-creator {
      margin-top: 6px;
      font-size: 10px;
    }

    .cu-g-title {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      height: 30px;
      margin: 1px 7px 2px;
      overflow: hidden;
      overflow-wrap: anywhere;
      font-family: var(--cu-disp);
      font-size: 12px;
      font-weight: 500;
      line-height: 15px;
      letter-spacing: 0.01em;
      color: var(--cu-bone);
    }

    .cu-g-detail {
      margin-bottom: 7px;
      font-family: var(--cu-mono);
      font-size: 9.5px;
    }

    /* Download, Cancel, Stop and Delete: top-left, the height of the link out.
       Delete shows on hover or focus; visibility keeps the box where it is. */
    .cu-g-act {
      position: absolute;
      top: 6px;
      left: 6px;
      display: flex;
      align-items: center;
      height: 22px;
    }

    .cu-g .cu-g-btn {
      height: 22px;
      padding: 0 7px;
      border-color: var(--cu-seam-strong);
      background: rgba(19, 19, 22, 0.72);
      color: var(--cu-bone);
      line-height: 1;
    }

    .cu-g .cu-g-btn:hover:not(:disabled),
    .cu-g .cu-g-btn:focus-visible {
      border-color: var(--cu-dust);
    }

    .cu-g .cu-g-btn:disabled {
      cursor: default;
      color: var(--cu-dust);
    }

    .cu-g-card:not(:hover):not(:focus-within) .cu-g-del:not([data-confirm]):not([data-deleting]) {
      visibility: hidden;
    }

    /* Covers exactly the thumbnail; the state sits in its middle. */
    .cu-g-over {
      position: absolute;
      inset: 0 0 auto 0;
      aspect-ratio: 3 / 4;
      display: flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      padding: 30px 8px;
      overflow: hidden;
      pointer-events: none;
    }

    .cu-g-state {
      max-width: 100%;
      max-height: 100%;
      box-sizing: border-box;
      padding: 5px 10px;
      overflow: hidden;
      border-radius: var(--cu-radius);
      border: 1px solid var(--cu-seam-strong);
      background: rgba(19, 19, 22, 0.88);
      color: var(--cu-bone);
      font-family: var(--cu-mono);
      font-size: 9.5px;
      line-height: 1.4;
      text-align: center;
      overflow-wrap: anywhere;
    }

    .cu-g-state[data-error] {
      border-color: rgba(255, 75, 62, 0.35);
      color: var(--cu-red-soft);
    }

    .cu-g-state:empty {
      display: none;
    }

    /* The link out sits on the card, beside its button rather than inside it. */
    .cu-g-open {
      position: absolute;
      top: 6px;
      right: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      width: 22px;
      height: 22px;
      border-radius: 3px;
      border: 1px solid var(--cu-seam-strong);
      background: rgba(19, 19, 22, 0.72);
      color: var(--cu-bone);
      font-size: 12px;
      line-height: 1;
      text-decoration: none;
      opacity: 0.62;
      transition: opacity 0.12s ease, border-color 0.12s ease;
    }

    .cu-g-card:hover .cu-g-open {
      opacity: 0.9;
    }

    .cu-g-open:hover,
    .cu-g-open:focus-visible {
      opacity: 1;
      border-color: var(--cu-dust);
    }

    /* ---- Motion ----
       Three moments only: results arriving, a card settling from provisional
       to checked, and a caret turning. Nothing else moves. */

    @keyframes cu-rise {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: none; }
    }

    .cu-results .cu-item {
      animation: cu-rise 220ms cubic-bezier(0.2, 0.7, 0.3, 1) backwards;
      animation-delay: calc(var(--cu-i, 1) * 18ms);
    }

    /* Same page, fresher data: update in place. Only a card that actually
       changed state still announces itself. */
    .cu-results.cu-quiet .cu-item:not(.cu-settled) {
      animation: none;
    }

    @keyframes cu-settle {
      0% { border-color: var(--cu-red); background: rgba(255, 75, 62, 0.07); }
      100% { border-color: var(--cu-seam); background: rgba(0, 0, 0, 0.18); }
    }

    .cu-item.cu-settled {
      animation: cu-rise 220ms cubic-bezier(0.2, 0.7, 0.3, 1) backwards,
                 cu-settle 900ms ease-out 220ms backwards;
    }

    @keyframes cu-ignite {
      0% { transform: scale(0.4); opacity: 0; }
      60% { transform: scale(1.25); opacity: 1; }
      100% { transform: scale(1); opacity: 1; }
    }

    .cu-item.cu-settled .cu-ver-label[data-role="new"]::before {
      animation: cu-ignite 420ms cubic-bezier(0.2, 0.8, 0.3, 1) 260ms backwards;
    }

    /* ---- Focus + reduced motion ---- */

    .cu-root :focus-visible,
    .cu-lightbox :focus-visible {
      outline: 1px solid var(--cu-red);
      outline-offset: 2px;
    }

    @media (prefers-reduced-motion: reduce) {
      .cu-root *,
      .cu-root *::before,
      .cu-root *::after,
      .cu-lightbox,
      .cu-lightbox * {
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
        animation-delay: 0ms !important;
      }

      /* The loading indicators loop: cut to 0.01ms they would flicker, so they
         slow down instead. More specific than the rule above, so they win. */
      .cu-root .cu-spinner,
      .cu-root .cu-loading-bar.is-on::after {
        animation-duration: 5s !important;
      }
    }

    /* ---- Very narrow sidebar ----
       The panel's own width decides, not the window's: a viewport query
       never fired for a sidebar. Below this the thumbnail stacks on top. */

    @container (max-width: 280px) {
      .cu-item {
        grid-template-columns: 1fr;
      }

      .cu-thumb {
        width: 100%;
        height: 120px;
      }
    }

    /* ---- Lightbox (appended to body, outside .cu-root) ---- */

    .cu-lightbox {
      position: fixed;
      inset: 0;
      z-index: 99999;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: opacity 150ms ease;
    }

    .cu-lightbox.cu-lb-open {
      opacity: 1;
    }

    .cu-lb-backdrop {
      position: absolute;
      inset: 0;
      background: rgba(8, 8, 10, 0.85);
    }

    .cu-lb-container {
      position: relative;
      background: #1b1b20;
      border: 1px solid #40404b;
      border-radius: 8px;
      max-width: 92vw;
      max-height: 92vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      box-shadow: 0 12px 48px rgba(0, 0, 0, 0.6);
    }

    .cu-lb-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 13px 16px;
      border-bottom: 1px solid #2e2e36;
      flex-shrink: 0;
    }

    .cu-lb-title {
      color: #eceae4;
      font-family: "Space Grotesk", "Segoe UI", system-ui, sans-serif;
      font-size: 14px;
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }

    .cu-lb-title .cu-creator {
      color: #6a6772;
      font-family: "Segoe UI", system-ui, sans-serif;
      font-size: 11px;
    }

    .cu-lb-close {
      background: none;
      border: none;
      color: #6a6772;
      font-size: 24px;
      cursor: pointer;
      padding: 0 2px;
      line-height: 1;
      flex-shrink: 0;
      transition: color 120ms;
    }

    .cu-lb-close:hover {
      color: #eceae4;
    }

    .cu-lb-body {
      padding: 16px;
      display: flex;
      gap: 16px;
      justify-content: center;
      flex-wrap: wrap;
    }

    .cu-lb-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      min-width: 0;
      flex: 0 1 auto;
    }

    .cu-lb-label {
      font-family: "IBM Plex Mono", Consolas, ui-monospace, monospace;
      font-size: 8.5px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #6a6772;
    }

    .cu-lb-label[data-role="new"] {
      color: #f2857b;
    }

    .cu-lb-vname {
      font-size: 12px;
      color: #96939c;
      text-align: center;
      max-width: 400px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .cu-lb-base {
      font-family: "IBM Plex Mono", Consolas, ui-monospace, monospace;
      font-size: 9.5px;
      color: #6a6772;
    }

    .cu-lb-card img,
    .cu-lb-card video {
      max-width: min(420px, 42vw);
      max-height: 65vh;
      border-radius: 5px;
      border: 1px solid #2e2e36;
      object-fit: contain;
      background: rgba(0, 0, 0, 0.3);
    }

    .cu-lb-body:not(.cu-lb-compare) .cu-lb-card img,
    .cu-lb-body:not(.cu-lb-compare) .cu-lb-card video {
      max-width: min(600px, 80vw);
      max-height: 75vh;
    }

    .cu-lb-empty {
      width: min(240px, 42vw);
      aspect-ratio: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 5px;
      border: 1px solid #2e2e36;
      background: rgba(0, 0, 0, 0.3);
      color: #6a6772;
      font-family: "IBM Plex Mono", Consolas, ui-monospace, monospace;
      font-size: 9.5px;
    }
  `;
  document.head.appendChild(style);
}
