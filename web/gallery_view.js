// The panel's Gallery: the models on disk as cards, in bands by base. A click
// puts one in the workflow: a LoRA in the prompt's list, an embedding in its
// prose, a checkpoint or diffusion model in a loader.
//
// Drawn in the panel's cu- styles, the way rename_view.js draws Rename. ComfyUI's
// app comes in through the options rather than an import, so the markup
// builders below can be tested in node.
import {
  arrange,
  cardModel,
  filterEntries,
  foldedIn,
  galleryFacets,
  stackEntries,
} from "./gallery_core.js";
import { embeddings, forget, loras, missingLoras, models } from "./library.js";
import {
  CONFIRM_FOR,
  DONE_FOR,
  galleryActionHtml,
  galleryKey,
  galleryStateText,
  slotState,
} from "./model_actions.js";
import { connectedBase, describeBase, fittingFamilies, groupOf } from "./model_base.js";
import {
  PROMPT_CHANGED,
  addEmbeddingToNode,
  currentLoader,
  currentTarget,
  cycleInNode,
  embeddingIn,
  loadedModel,
  loaderNodesOf,
  markIn,
  noteLoaderUsed,
  noteUsed,
  promptNodesOf,
  sameModelName,
  setLoaderModel,
} from "./prompt_target.js";
import { stemOf } from "./text.js";

const TYPE_KEY = "warpstudio.gallery.type";
const foldedKey = (type) => `warpstudio.gallery.folded.${type}`;
const TYPES = ["loras", "models", "embeddings"];
const TYPE_LABEL = { loras: "LoRAs", models: "Models", embeddings: "Embeddings" };
const NOUN = { loras: "LoRA", models: "model", embeddings: "embedding" };
const MARK_DELAY = 80;
const WATCH_EVERY = 1000;

export const COPY = {
  loading: "Loading the gallery…",
  empty: "Nothing matches. Clear the search or the filters.",
  none: {
    loras: "No LoRAs found.",
    models: "No models found.",
    embeddings: "No embeddings found.",
  },
  missingFailed: "Could not load the LoRAs that are not on this drive:",
  noPrompt: "No Prompt + LoRAs node in this workflow",
  pickPrompt: "Select a Prompt + LoRAs node",
  noLoader: "No model loader in this workflow",
  pickLoader: "Select a Load Diffusion Model or Load Checkpoint node",
};

/* What a mark looks like: a glyph for the eye and a label for the reader. */
export const MARKS = {
  on: { glyph: "✓", label: "In the prompt" },
  parked: { glyph: "○", label: "Parked in the prompt" },
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const escaper = (escapeHtml) => (value) => escapeHtml(value == null ? "" : String(value));

/** Only a link that opens a web page; anything else gets no ↗ at all. */
export function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

/** The mark's slot: always drawn, so a mark arriving or going moves nothing. */
function markHtml(mark, esc) {
  const known = MARKS[mark];
  if (!known) return `<span class="cu-g-mark" data-g-mark role="img"></span>`;
  return `<span class="cu-g-mark" data-g-mark data-mark="${esc(mark)}" role="img" aria-label="${esc(known.label)}">${known.glyph}</span>`;
}

/** One card, from cardModel's raw text and ui = { mark, versions, versionLabel }.
 *
 * Every row is there whether or not it has text, and each has a fixed height
 * in the styles, so a card without a creator or a preview is as tall as the
 * rest. The face is one button; the ↗ link and the version chip are its
 * siblings, because a link or a button inside a button is neither valid nor
 * reachable by keyboard.
 */
export function cardHtml(model, ui, escapeHtml) {
  const esc = escaper(escapeHtml);
  const { mark = null, versions = 1, versionLabel = "" } = ui || {};
  const thumb = model.thumbnail
    ? `<img src="${esc(model.thumbnail)}" alt="" loading="lazy" decoding="async">`
    : `<span class="cu-g-noimg">${esc(model.stem)}</span>`;
  const badge =
    model.triggers > 0
      ? `<span class="cu-g-badge" title="${esc(plural(model.triggers, "trigger word"))}">⊕ ${esc(model.triggers)}</span>`
      : "";
  const url = safeUrl(model.url);
  const link = url
    ? `<a class="cu-g-open" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Open on Civitai" aria-label="${esc(`Open ${model.title} on Civitai`)}">↗</a>`
    : "";
  const chip =
    versions > 1
      ? `<button type="button" class="cu-g-vers" data-g-vers aria-haspopup="true" aria-expanded="false" aria-label="${esc(`${versions} versions, showing ${versionLabel}`)}">${esc(versions)} versions ▾</button>`
      : "";
  const marked = MARKS[mark] ? ` data-mark="${esc(mark)}"` : "";
  return (
    `<div class="cu-g-card" data-g-key="${esc(model.key)}"${marked}${model.missing ? " data-missing" : ""}>` +
    `<button type="button" class="cu-g-pick" title="${esc(model.stem)}"${model.missing ? ' aria-disabled="true"' : ""}>` +
    `<span class="cu-g-thumb">${thumb}${badge}</span>` +
    `<span class="cu-g-creator">${esc(model.creator)}</span>` +
    `<span class="cu-g-title">${esc(model.title)}</span>` +
    `<span class="cu-g-detail">${esc(model.detail)}</span>` +
    `</button>${link}<span class="cu-g-layer">${markHtml(mark, esc)}${chip}</span>` +
    // Empty slots, siblings of the face: never dimmed, never a button in a button.
    `<span class="cu-g-act" data-g-act></span>` +
    `<span class="cu-g-over"><span class="cu-g-state" role="status"></span></span>` +
    `</div>`
  );
}

/** The list a version chip opens: items are { id, label, date, current }. */
export function versionMenuHtml(items, escapeHtml) {
  const esc = escaper(escapeHtml);
  return items
    .map(
      (item) =>
        `<button type="button" class="cu-g-vitem" data-g-ver="${esc(item.id)}"${item.current ? ' aria-current="true"' : ""}>` +
        `<span class="cu-g-vname">${esc(item.label)}</span>` +
        `<span class="cu-g-vdate">${esc(item.date)}</span></button>`
    )
    .join("");
}

/** A band: the Updates group band, then an empty grid for its cards.
 *
 * The marker is always in the head and only shown or hidden, so marking a band
 * never changes its height. A folded band keeps its head and an empty grid. With
 * no base (one flat grid) there is no head at all.
 */
export function bandHtml(
  band,
  { fits = false, quiet = false, folded = false, fitTitle = "", noun = "LoRA" } = {},
  escapeHtml
) {
  const esc = escaper(escapeHtml);
  const head =
    band.base == null
      ? ""
      : `<div class="cu-group-head-wrap"><button type="button" class="cu-group-head cu-g-band-head" data-g-fold aria-expanded="${!folded}" data-collapsed="${folded}">` +
        `<span class="cu-group-caret" aria-hidden="true">▾</span>` +
        `<span class="cu-group-name">${esc(band.base)}</span>` +
        `<span class="cu-g-fit" title="${esc(fits ? fitTitle : "")}">for this workflow</span>` +
        `<span class="cu-group-count">${esc(plural(band.entries.length, noun))}</span>` +
        `</button></div>`;
  return (
    `<section class="cu-g-band" data-g-band="${esc(band.base)}" data-workflow="${fits}"${quiet ? " data-quiet" : ""}${folded ? " data-folded" : ""}>` +
    `${head}<div class="cu-g-grid"></div></section>`
  );
}

/** The switch at the top: one button for each type that has files. */
export function typeSwitchHtml(types, current, escapeHtml) {
  const esc = escaper(escapeHtml);
  const shown = TYPES.filter((type) => (types || []).includes(type));
  if (!shown.length) return "";
  return (
    `<div class="cu-g-types" role="group" aria-label="Type">` +
    shown
      .map(
        (type) =>
          `<button type="button" class="cu-g-type" data-g-type="${esc(type)}" aria-pressed="${type === current}">${esc(TYPE_LABEL[type])}</button>`
      )
      .join("") +
    `</div>`
  );
}

/** The note above the bands: only when no node can take a click, and why. */
export function targetHtml(type, hasTarget, nodeCount, escapeHtml) {
  if (hasTarget) return "";
  const loader = type === "models";
  let text;
  if (nodeCount > 0) text = loader ? COPY.pickLoader : COPY.pickPrompt;
  else text = loader ? COPY.noLoader : COPY.noPrompt;
  return escaper(escapeHtml)(text);
}

/* ---------------------------------------------------------------- styles */

// The panel's own style template has the card and band rules. What this view
// adds (marks, the version chip and its list, folding, the type switch) lives
// here, so it ships with the markup that uses it. It rides on the panel's
// custom properties, which the panel root defines.
const STYLE_ID = "cu-g-style-2";
const STYLE = `
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
`;

function ensureStyle() {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLE;
  document.head.append(style);
}

/* ---------------------------------------------------------------- storage */

// localStorage can throw or be empty (private windows, blocked site data).
function readStored(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    /* not remembered; the view works the same */
  }
}

function readFolded(type) {
  try {
    return JSON.parse(readStored(foldedKey(type)) ?? "null");
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------- canvas */

// The canvas has one onSelectionChange slot. It is chained once per canvas and
// calls whichever view was mounted last: the panel builds a new one each time
// the sidebar tab opens, and the old ones must not keep listening.
const hooked = new WeakSet();
let onSelection = null;
// The view mounted last. Older ones stop their watch and their listeners.
let mounted = null;

function hookSelection(app) {
  const canvas = app?.canvas;
  if (!canvas || typeof canvas !== "object" || hooked.has(canvas)) return;
  hooked.add(canvas);
  const previous = canvas.onSelectionChange;
  canvas.onSelectionChange = function (...args) {
    const result = typeof previous === "function" ? previous.apply(this, args) : undefined;
    onSelection?.();
    return result;
  };
}

const graphOf = (app) => app?.canvas?.graph ?? app?.graph ?? null;

/* ---------------------------------------------------------------- the view */

/* options: { escapeHtml, app, filter() -> { bases }, query() -> string,
     arrange() -> { groupBy, sort }, includeDownloadable() -> boolean,
     onFacets(facets), onCount(), downloads() -> Map, canTrash() -> boolean,
     onDownload(name), onStop(requestId), onDelete(name, kind) -> Promise<"" | error> }
   Every option is optional; a missing one means its plain default.
   Returns { refresh, render, rerender, setQuery, count, downloadableCount, type,
     updateTarget, paintDownloads }. */
export function renderGalleryView(el, options) {
  const esc = escaper(options.escapeHtml);
  const app = options.app;
  const token = {};
  mounted = token;
  ensureStyle();
  el.classList.add("cu-g");
  el.innerHTML =
    `<div class="cu-g-top">` +
    `<div data-g-types></div>` +
    `<div class="cu-summary cu-g-note" data-g-note></div>` +
    `</div><div class="cu-g-bands" data-g-bands></div>`;
  const typesEl = el.querySelector("[data-g-types]");
  const noteEl = el.querySelector("[data-g-note]");
  const bandsEl = el.querySelector("[data-g-bands]");

  let lib = null; // { loras, models, embeddings }; null until the first load
  let absent = null; // the LoRAs that are not on this drive; null until loaded
  let absentError = "";
  let error = "";
  let query = "";
  let shown = 0;
  let loading = null;
  let generation = 0;
  let type = TYPES.includes(readStored(TYPE_KEY)) ? readStored(TYPE_KEY) : "loras";

  let target = null; // the Prompt + LoRAs node of the prompt types
  let connected = null;
  let fitting = new Set();
  let targetSeq = 0;
  let noteShown = "";
  let typesShown = "";

  // Cards are built once and moved between renders, so a search never reloads
  // a thumbnail that is already on screen. Rebuilt only when their text changes.
  const cards = new Map(); // entry id -> { html, el, model, stack, mark, ... }
  const chosen = new Map(); // stack key -> the id of the version the card shows
  let bandStacks = new Map(); // base -> its stacks, as last drawn
  let openMenu = null; // { card, menu, chip }

  /* ---- what is listed ---- */

  function listOf(which) {
    if (!lib) return [];
    if (which !== "loras") return lib[which] || [];
    if (!absent || !options.includeDownloadable?.()) return lib.loras;
    const have = new Set(lib.loras.map((entry) => entry.id));
    return [...lib.loras, ...absent.filter((entry) => !have.has(entry.id))];
  }

  const listed = () => listOf(type);
  const availableTypes = () => TYPES.filter((t) => lib && lib[t].length > 0);

  function pickType() {
    const have = availableTypes();
    if (!have.includes(type)) type = have.includes("loras") ? "loras" : have[0] || "loras";
  }

  const facets = () => options.onFacets?.(galleryFacets(listed()));

  /* ---- the version a stack shows ---- */

  function shownOf(stack) {
    const id = chosen.get(stack.key);
    return stack.members.find((member) => member.id === id) || stack.members[0];
  }

  const versionName = (entry) => entry.version || entry.title || stemOf(entry.id);

  /* ---- target and marks ---- */

  function stateIn(node, member) {
    if (type === "loras") return markIn(node, stemOf(member.id));
    if (type === "embeddings") return embeddingIn(node, stemOf(member.id)) ? "on" : null;
    return sameModelName(loadedModel(node), member.id) ? "on" : null;
  }

  /* The strongest state of any version in the stack: on, then parked, then none. */
  function markOf(hit, nodeOf) {
    const node = nodeOf(hit.model.kind);
    if (!node) return null;
    let best = null;
    for (const member of hit.stack.members) {
      const state = stateIn(node, member);
      if (state === "on") return "on";
      if (state === "parked") best = "parked";
    }
    return best;
  }

  function paintOne(hit, nodeOf) {
    const mark = markOf(hit, nodeOf);
    if (mark !== hit.mark) {
      hit.mark = mark;
      const slot = hit.el.querySelector("[data-g-mark]");
      const known = MARKS[mark];
      if (mark) hit.el.dataset.mark = mark;
      else delete hit.el.dataset.mark;
      if (slot) {
        if (known) {
          slot.dataset.mark = mark;
          slot.setAttribute("aria-label", known.label);
          slot.textContent = known.glyph;
        } else {
          delete slot.dataset.mark;
          slot.removeAttribute("aria-label");
          slot.textContent = "";
        }
      }
    }
    // A card that is not on this drive can never be clicked, target or not.
    const pick = hit.el.querySelector(".cu-g-pick");
    if (pick) {
      if (nodeOf(hit.model.kind) && !hit.el.hasAttribute("data-missing")) {
        pick.removeAttribute("aria-disabled");
      } else pick.setAttribute("aria-disabled", "true");
    }
  }

  function nodeResolver() {
    if (type !== "models") return () => target;
    const found = new Map();
    return (kind) => {
      if (!found.has(kind)) found.set(kind, currentLoader(app, kind));
      return found.get(kind);
    };
  }

  /* The marks and the disabled state of every card on screen. */
  function paintTargets() {
    const nodeOf = nodeResolver();
    for (const hit of cards.values()) if (hit.el.isConnected) paintOne(hit, nodeOf);
  }

  function drawNote() {
    let html = "";
    if (lib) {
      let has;
      let count;
      if (type === "models") {
        has = Boolean(currentLoader(app, "checkpoints") || currentLoader(app, "diffusion_models"));
        count =
          loaderNodesOf(app, "checkpoints").length + loaderNodesOf(app, "diffusion_models").length;
      } else {
        has = Boolean(target);
        count = promptNodesOf(app).length;
      }
      html = targetHtml(type, has, count, options.escapeHtml);
    }
    if (html !== noteShown) {
      noteEl.innerHTML = html;
      noteShown = html;
    }
  }

  const fitTitle = () => (connected ? `Made for ${describeBase(connected)}` : "");

  /* The target and what fits its model. Says whether the fit marks need
     redrawing, and whether the connected base itself changed. */
  async function retarget() {
    hookSelection(app);
    const seq = ++targetSeq;
    target = type === "models" ? null : currentTarget(app);
    drawNote();
    paintTargets();
    let base = null;
    if (target && lib) {
      try {
        base = await connectedBase(target);
      } catch {
        base = null;
      }
    }
    if (seq !== targetSeq) return { redraw: false, baseChanged: false }; // a newer call has the answer
    // A model decides the workflow, so there is no band that fits it.
    const next =
      lib && type !== "models" ? new Set(fittingFamilies(listed(), base).keys()) : new Set();
    const before = fitTitle();
    const baseChanged =
      (base ? describeBase(base) : "") !== (connected ? describeBase(connected) : "");
    const same = next.size === fitting.size && [...next].every((name) => fitting.has(name));
    connected = base;
    fitting = next;
    return { redraw: !same || fitTitle() !== before, baseChanged };
  }

  /* Only the fit marks: the bands keep their order until the next render, so
     selecting a node never moves the cards under the cursor. */
  function drawFits() {
    const title = fitTitle();
    for (const band of bandsEl.querySelectorAll("[data-g-band]")) {
      const fits = fitting.has(band.dataset.gBand);
      band.dataset.workflow = String(fits);
      band.toggleAttribute("data-quiet", fitting.size > 0 && !fits);
      const mark = band.querySelector(".cu-g-fit");
      if (mark) mark.title = fits ? title : "";
    }
  }

  /* Resolves to { baseChanged }: the caller decides whether to reorder the bands. */
  async function updateTarget() {
    const { redraw, baseChanged } = await retarget();
    if (redraw) drawFits();
    return { baseChanged };
  }

  /* For callers that do not wait: the canvas hook, a click, the workflow watch.
     A changed base reorders the bands, but never on a click: the cards under
     the cursor stay put. */
  function follow(reorder = true) {
    updateTarget()
      .then(({ baseChanged }) => {
        if (reorder && baseChanged) render();
      })
      .catch((err) => console.warn("[Warp Studio] Gallery target:", err));
  }

  /* ---- loading ---- */

  async function loadAbsent() {
    try {
      absent = await missingLoras();
      absentError = "";
    } catch (err) {
      absent = null;
      absentError = `${COPY.missingFailed} ${err?.message || err}`;
    }
  }

  const everyEntry = () => [
    ...(lib ? [...lib.loras, ...lib.models, ...lib.embeddings] : []),
    ...(absent || []),
  ];

  /* Loads the three lists. The first load takes the shared cache (the prompt may
     have filled it); every later one asks the server again, so a model added
     since shows up. The cards on screen stay until the new lists are in. */
  function refresh(force = false) {
    // A forced refresh (after a delete) never takes an in-flight load: its list may predate the change.
    if (loading && !force) return loading;
    const seq = ++generation;
    const run = (async () => {
      try {
        if (lib) forget();
        const [lorasList, modelsList, embeddingsList] = await Promise.all([
          loras(),
          // An older server has no models index, and that is no reason for the rest to fail.
          models().catch(() => []),
          embeddings().catch(() => []),
        ]);
        if (seq !== generation) return; // a newer load has the answer
        lib = { loras: lorasList, models: modelsList, embeddings: embeddingsList };
        error = "";
        pickType();
        const keys = new Set(everyEntry().map((entry) => entry.id));
        for (const key of cards.keys()) if (!keys.has(key)) cards.delete(key);
        facets();
      } catch (err) {
        // A failed reload keeps the gallery that works; only a first load shows the error.
        if (!lib) error = err?.message || String(err);
        else console.warn("[Warp Studio] Could not reload the gallery:", err);
      }
      await retarget();
      if (seq !== generation) return;
      render();
      // The missing list is the slow one; it fills in behind the first paint.
      loadAbsent()
        .then(() => {
          if (seq !== generation) return;
          options.onCount?.();
          if (options.includeDownloadable?.() && type === "loras") {
            facets();
            return updateTarget().then(() => render());
          }
        })
        .catch((err) => console.warn("[Warp Studio] Gallery:", err));
    })().finally(() => {
      if (loading === run) loading = null;
    });
    loading = run;
    return run;
  }

  /* ---- rendering ---- */

  function fragment(html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    return template.content;
  }

  function scroller() {
    for (let node = el.parentElement; node; node = node.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node;
    }
    return document.scrollingElement;
  }

  /* ---- downloads and delete, painted in place ---- */

  const painted = new Map(); // key -> the download state last painted, for "just downloaded"
  const justDone = new Set();
  const asking = new Map(); // key -> "confirming" | "deleting"
  const askTimers = new Map();
  const notes = new Map(); // key -> why a delete failed

  function paintCard(hit) {
    const key = hit.model.key;
    const record = options.downloads?.().get(galleryKey(key));
    const state = slotState(record);
    const before = painted.get(key);
    if (state === "done" && (before === "waiting" || before === "downloading")) {
      justDone.add(key);
      setTimeout(() => {
        justDone.delete(key);
        paintDownloads();
      }, DONE_FOR);
    }
    painted.set(key, state);
    const ask = asking.get(key);
    const html = galleryActionHtml(
      hit.model,
      record,
      {
        canTrash: Boolean(options.canTrash?.()),
        confirming: ask === "confirming",
        deleting: ask === "deleting",
      },
      options.escapeHtml
    );
    if (html !== hit.act) {
      hit.act = html;
      hit.el.querySelector("[data-g-act]").innerHTML = html;
    }
    const text = notes.get(key) || galleryStateText(record, { justDone: justDone.has(key) });
    const bad = Boolean(notes.get(key)) || state === "error";
    if (text !== hit.text || bad !== hit.bad) {
      hit.text = text;
      hit.bad = bad;
      const stateEl = hit.el.querySelector(".cu-g-state");
      stateEl.textContent = text;
      stateEl.toggleAttribute("data-error", bad);
    }
  }

  function paintDownloads() {
    for (const hit of cards.values()) paintCard(hit);
  }

  function setAsking(key, value) {
    clearTimeout(askTimers.get(key));
    if (value) asking.set(key, value);
    else asking.delete(key);
    const hit = cards.get(key);
    if (hit) paintCard(hit);
  }

  async function onAction(button, card) {
    const key = card.dataset.gKey;
    const action = button.dataset.gAction;
    if (action === "download") {
      await options.onDownload?.(key);
    } else if (action === "stop") {
      await options.onStop?.(button.dataset.requestId || "");
    } else if (action === "delete") {
      const ask = asking.get(key);
      if (ask === "deleting") return;
      if (ask !== "confirming") {
        notes.delete(key);
        setAsking(key, "confirming");
        askTimers.set(
          key,
          setTimeout(() => setAsking(key, null), CONFIRM_FOR)
        );
        return;
      }
      setAsking(key, "deleting");
      const failure = await options.onDelete?.(key, cards.get(key)?.model.kind);
      if (failure) notes.set(key, failure);
      // Success: the refresh has run and removed the card. Never repaint Delete under the
      // cursor in between; only an error, or a card the server kept, turns it back.
      if (failure || cards.has(key)) setAsking(key, null);
      else asking.delete(key);
    }
    paintDownloads();
  }

  /* One card for each stack, showing the version the stack is set to. A card is
     rebuilt only when what it draws changed; its mark is painted separately. */
  function cardsFor(stacks) {
    const nodeOf = nodeResolver();
    const wanted = stacks.map((stack) => {
      const member = shownOf(stack);
      const model = cardModel(member);
      const ui = {
        mark: null,
        versions: stack.members.length,
        versionLabel: versionName(member),
      };
      return { stack, model, html: cardHtml(model, ui, options.escapeHtml) };
    });
    const fresh = [];
    for (const item of wanted) {
      const hit = cards.get(item.model.key);
      if (hit && hit.html === item.html) {
        hit.model = item.model;
        hit.stack = item.stack;
        paintOne(hit, nodeOf); // it may have sat out of the page while the prompt changed
      } else fresh.push(item);
    }
    if (fresh.length) {
      // One parse for every new card, not one per card.
      const built = [...fragment(fresh.map((item) => item.html).join("")).children];
      fresh.forEach(({ stack, model, html }, i) => {
        const hit = { html, el: built[i], model, stack, mark: null, act: null, text: null };
        cards.set(model.key, hit);
        paintCard(hit);
        paintOne(hit, nodeOf);
      });
    }
    return wanted.map((item) => cards.get(item.model.key).el);
  }

  const foldedNow = (all) => foldedIn(readFolded(type), new Set(all.map(groupOf)));

  function render() {
    closeMenu();
    const box = el.isConnected ? scroller() : null;
    const top = box ? box.scrollTop : 0;
    let content;
    bandStacks = new Map();
    drawTypes();
    drawNote();
    if (!lib) {
      shown = 0;
      content = error
        ? fragment(`<div class="cu-g-error">${esc(`Could not load the gallery: ${error}`)}</div>`)
        : fragment(
            `<div class="cu-loading-note"><span class="cu-spinner" aria-hidden="true"></span>${esc(COPY.loading)}</div>`
          );
    } else {
      const all = listed();
      const filter = options.filter?.() || {};
      const visible = filterEntries(all, {
        query: typeof options.query === "function" ? options.query() || "" : query,
        bases: filter.bases ?? null,
      });
      // A file that is not on this drive has no sibling versions: it stands alone.
      const stacks = [
        ...stackEntries(visible.filter((entry) => !entry.missing)),
        ...visible
          .filter((entry) => entry.missing)
          .map((entry) => ({ key: `file:${entry.id}`, members: [entry] })),
      ];
      shown = stacks.length;
      if (!stacks.length) {
        content = fragment(
          `<div class="cu-empty">${esc(all.length ? COPY.empty : COPY.none[type])}</div>`
        );
      } else {
        const { groupBy = "base", sort = "name" } = options.arrange?.() || {};
        const folded = groupBy === "none" ? new Set() : foldedNow(all);
        const bands = arrange(stacks, { groupBy, sort, fitting });
        const title = fitTitle();
        content = fragment(
          bands
            .map((band) =>
              bandHtml(
                { base: band.base, entries: band.stacks },
                {
                  fits: band.fits,
                  quiet: fitting.size > 0 && !band.fits,
                  folded: folded.has(band.base),
                  fitTitle: title,
                  noun: NOUN[type],
                },
                options.escapeHtml
              )
            )
            .join("")
        );
        const grids = content.querySelectorAll(".cu-g-grid");
        bands.forEach((band, i) => {
          bandStacks.set(band.base, band.stacks);
          // A folded band draws no cards at all.
          if (!folded.has(band.base)) grids[i].append(...cardsFor(band.stacks));
        });
      }
    }
    if (lib && absentError && type === "loras" && options.includeDownloadable?.()) {
      content.prepend(fragment(`<div class="cu-g-error">${esc(absentError)}</div>`));
    }
    bandsEl.replaceChildren(content);
    if (box && box.scrollTop !== top) box.scrollTop = top;
    options.onCount?.();
  }

  /* The type switch, drawn only when it changed so a focused button keeps focus.
     One type alone needs no switch. */
  function drawTypes() {
    const have = availableTypes();
    const html = have.length > 1 ? typeSwitchHtml(have, type, options.escapeHtml) : "";
    if (html !== typesShown) {
      typesEl.innerHTML = html;
      typesShown = html;
    }
  }

  /* ---- version lists ---- */

  function closeMenu() {
    if (!openMenu) return;
    openMenu.menu.remove();
    openMenu.chip?.setAttribute("aria-expanded", "false");
    openMenu = null;
    document.removeEventListener("click", onOutside, true);
    document.removeEventListener("keydown", onEscape, true);
  }

  function onOutside(ev) {
    if (openMenu && !openMenu.card.contains(ev.target)) closeMenu();
  }

  function onEscape(ev) {
    if (ev.key !== "Escape" || !openMenu) return;
    const chip = openMenu.chip;
    closeMenu();
    chip?.focus({ preventScroll: true });
  }

  function openVersions(card, hit, chip) {
    const same = openMenu?.card === card;
    closeMenu();
    if (same) return;
    const items = hit.stack.members.map((member) => ({
      id: member.id,
      label: versionName(member),
      date: typeof member.published === "string" ? member.published.slice(0, 10) : "",
      current: member.id === hit.model.key,
    }));
    const menu = document.createElement("div");
    menu.className = "cu-g-vmenu";
    menu.setAttribute("role", "group");
    menu.innerHTML = versionMenuHtml(items, options.escapeHtml);
    card.append(menu);
    chip.setAttribute("aria-expanded", "true");
    openMenu = { card, menu, chip };
    document.addEventListener("click", onOutside, true);
    document.addEventListener("keydown", onEscape, true);
    (menu.querySelector("[aria-current]") || menu.firstElementChild)?.focus({
      preventScroll: true,
    });
  }

  /* Picking a version: the card for that version takes the old one's place. */
  function pickVersion(card, hit, id) {
    closeMenu();
    if (id === hit.model.key) return;
    chosen.set(hit.stack.key, id);
    const [next] = cardsFor([hit.stack]);
    card.replaceWith(next);
    next.querySelector(".cu-g-vers")?.focus({ preventScroll: true });
  }

  /* ---- events ---- */

  /* After a deliberate change of what is listed: draw it now, then reorder the
     bands if the set that fits this workflow turned out different. */
  function settle() {
    retarget()
      .then(({ redraw }) => {
        if (redraw) render();
      })
      .catch((err) => console.warn("[Warp Studio] Gallery target:", err));
  }

  /* A click on a card's face: put that model in the workflow. */
  function pickCard(hit, ev, pick) {
    if (hit.model.missing) return; // not on this drive: nothing to use
    if (type === "models") {
      const loader = currentLoader(app, hit.model.kind);
      if (!loader) return;
      // Marked already (some version of this model is loaded): nothing to do.
      if (hit.stack.members.some((member) => sameModelName(loadedModel(loader), member.id))) return;
      if (!setLoaderModel(loader, hit.model.key)) return;
      noteLoaderUsed(loader);
    } else {
      const node = currentTarget(app);
      if (node !== target) follow(false); // the selection moved without telling us
      if (!node) return;
      if (type === "embeddings") {
        if (embeddingIn(node, hit.model.stem)) return;
        if (!addEmbeddingToNode(node, hit.model.stem)) return;
      } else if (!cycleInNode(node, hit.model.stem)) return;
      noteUsed(node);
      target = node;
    }
    paintOne(hit, nodeResolver());
    // Adding moved focus into the node's box. A press from the keyboard (no
    // pointer, so detail 0) gets it back, so the reader keeps their place.
    if (ev.detail === 0) pick.focus({ preventScroll: true });
  }

  bandsEl.addEventListener("click", (ev) => {
    const at = ev.target instanceof Element ? ev.target : null;
    if (!at) return;
    const card = at.closest("[data-g-key]");
    const hit = card ? cards.get(card.dataset.gKey) : null;
    const chip = at.closest("[data-g-vers]");
    const item = at.closest("[data-g-ver]");
    const pick = at.closest(".cu-g-pick");
    const action = at.closest("[data-g-action]");
    const fold = at.closest("[data-g-fold]");
    if (fold) return toggleBand(fold.closest("[data-g-band]"));
    if (!card || !hit) return;
    if (item) return pickVersion(card, hit, item.dataset.gVer);
    if (chip) return openVersions(card, hit, chip);
    if (action) {
      // The action buttons are siblings of the face, so these clicks never reach its pick.
      if (action.disabled) return;
      onAction(action, card).catch((err) => console.warn("[Warp Studio] Gallery action:", err));
      return;
    }
    if (pick) pickCard(hit, ev, pick);
  });

  /* Folding in place: only this band's cards come or go, so nothing else moves. */
  function toggleBand(band) {
    const base = band?.dataset.gBand;
    if (base == null || !bandStacks.has(base)) return;
    const all = listed();
    const folded = foldedNow(all);
    if (folded.has(base)) folded.delete(base);
    else folded.add(base);
    writeStored(foldedKey(type), JSON.stringify([...folded]));
    const now = folded.has(base);
    band.toggleAttribute("data-folded", now);
    const head = band.querySelector("[data-g-fold]");
    head?.setAttribute("aria-expanded", String(!now));
    head?.setAttribute("data-collapsed", String(now));
    const grid = band.querySelector(".cu-g-grid");
    if (now) grid.replaceChildren();
    else grid.append(...cardsFor(bandStacks.get(base)));
  }

  typesEl.addEventListener("click", (ev) => {
    const button = ev.target instanceof Element ? ev.target.closest("[data-g-type]") : null;
    const next = button?.dataset.gType;
    if (!next || next === type || !TYPES.includes(next)) return;
    type = next;
    writeStored(TYPE_KEY, type);
    facets();
    render();
    settle();
  });

  // A thumbnail that fails to load becomes the quiet stem box, in the same space.
  // Load errors do not bubble, hence the capture.
  bandsEl.addEventListener(
    "error",
    (ev) => {
      const img = ev.target;
      if (!(img instanceof HTMLImageElement)) return;
      const hit = cards.get(img.closest("[data-g-key]")?.dataset.gKey);
      const blank = document.createElement("span");
      blank.className = "cu-g-noimg";
      blank.textContent = hit?.model.stem || "";
      img.replaceWith(blank);
    },
    true
  );

  /* ---- keeping up with the workflow ---- */

  // Every keystroke in a prompt box fires this, so it only schedules one repaint,
  // and only for the node the marks are about.
  let markTimer = 0;
  const onPromptChanged = (ev) => {
    if (type === "models" || !target || ev?.detail?.node !== target) return;
    clearTimeout(markTimer);
    markTimer = setTimeout(paintTargets, MARK_DELAY);
  };
  window.addEventListener(PROMPT_CHANGED, onPromptChanged);

  // Switching workflow tabs, or opening a subgraph, swaps the canvas's graph
  // without a selection change. Watched while the view is on screen; it ends
  // when the view is replaced or taken out of the page.
  let lastGraph = graphOf(app);
  let detached = 0;
  const watch = setInterval(() => {
    if (mounted !== token) return stop();
    if (!el.isConnected) {
      // The panel builds the view before it attaches it; give that a moment.
      if (++detached > 5) stop();
      return;
    }
    detached = 0;
    if (el.getClientRects().length === 0) return; // hidden: no work until it shows again
    const graph = graphOf(app);
    if (graph !== lastGraph) {
      lastGraph = graph;
      follow();
    } else if (type === "models") {
      paintTargets(); // a loader's own widget changes without an event
    }
  }, WATCH_EVERY);

  function stop() {
    clearInterval(watch);
    clearTimeout(markTimer);
    window.removeEventListener(PROMPT_CHANGED, onPromptChanged);
    closeMenu();
  }

  onSelection = () => {
    if (mounted === token) follow();
  };
  follow();
  render();
  return {
    refresh,
    render,
    // For a changed arrangement or downloadable choice: the bases may differ too.
    rerender() {
      if (lib) facets();
      render();
      settle();
    },
    setQuery(next) {
      query = String(next ?? "");
      render();
    },
    count: () => (lib ? shown : null),
    downloadableCount: () => (absent ? absent.length : 0),
    type: () => type,
    updateTarget,
    paintDownloads,
  };
}
