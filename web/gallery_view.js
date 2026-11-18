// The panel's Gallery: every LoRA on disk as a card, in bands by base, and a
// click adds it to the Prompt + LoRAs node.
//
// Drawn in the panel's cu- styles, the way rename_view.js draws Rename; the
// cu-g- classes live in the panel's style template. ComfyUI's app comes in
// through the options rather than an import, so the markup builders below can
// be tested in node.
import { cardModel, filterEntries, galleryFacets, groupEntries } from "./gallery_core.js";
import { forget, loras } from "./library.js";
import { connectedBase, describeBase, fittingFamilies } from "./model_base.js";
import {
  PROMPT_NODE,
  currentTarget,
  insertIntoNode,
  noteUsed,
  targetLabel,
} from "./prompt_target.js";

const SEARCH_DELAY = 150;
const ADDED_FOR = 1200;

export const COPY = {
  search: "Search name, creator or version",
  loading: "Loading the gallery…",
  empty: "Nothing matches. Clear the search or the filters.",
  noLoras: "No LoRAs found.",
  added: "Added",
  noNode: "No Prompt + LoRAs node in this workflow",
  // Two or more nodes, none selected and none used yet: naming none of them
  // is the truth, and "none in this workflow" would not be.
  noChoice: "No Prompt + LoRAs node selected",
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const escaper = (escapeHtml) => (value) => escapeHtml(value == null ? "" : String(value));

/** Only a link that opens a web page; anything else gets no ↗ at all. */
export function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

/** One card, from cardModel's raw text.
 *
 * Every row is there whether or not it has text, and each has a fixed height
 * in the styles, so a card without a creator or a preview is as tall as the
 * rest. The face is one button; the ↗ link is its sibling, because a link
 * inside a button is neither valid nor reachable by keyboard.
 */
export function cardHtml(model, escapeHtml) {
  const esc = escaper(escapeHtml);
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
  return (
    `<div class="cu-g-card" data-g-key="${esc(model.key)}">` +
    `<button type="button" class="cu-g-pick" title="${esc(model.stem)}">` +
    `<span class="cu-g-thumb">${thumb}${badge}<span class="cu-g-added" role="status"></span></span>` +
    `<span class="cu-g-creator">${esc(model.creator)}</span>` +
    `<span class="cu-g-title">${esc(model.title)}</span>` +
    `<span class="cu-g-detail">${esc(model.detail)}</span>` +
    `</button>${link}</div>`
  );
}

/** A band: the Updates group band, then an empty grid for its cards.
 *
 * The fit marker is always in the head and only shown or hidden, so marking a
 * band never changes its height.
 */
export function bandHtml(band, { fits = false, quiet = false, fitTitle = "" }, escapeHtml) {
  const esc = escaper(escapeHtml);
  return (
    `<section class="cu-g-band" data-g-band="${esc(band.base)}" data-fits="${fits}"${quiet ? " data-quiet" : ""}>` +
    `<div class="cu-group-head-wrap"><div class="cu-group-head cu-g-band-head">` +
    `<span class="cu-group-name">${esc(band.base)}</span>` +
    `<span class="cu-g-fit" title="${esc(fits ? fitTitle : "")}">fits</span>` +
    `<span class="cu-group-count">${esc(plural(band.entries.length, "LoRA"))}</span>` +
    `</div></div><div class="cu-g-grid"></div></section>`
  );
}

/** The target line: the node a click adds to, or why there is none. */
export function targetHtml(node, promptNodes, escapeHtml) {
  if (node) {
    return `Adds to: <span class="cu-g-node">${escaper(escapeHtml)(targetLabel(node))}</span>`;
  }
  return promptNodes > 0 ? COPY.noChoice : COPY.noNode;
}

/* ---------------------------------------------------------------- canvas */

// The canvas has one onSelectionChange slot. It is chained once per canvas and
// calls whichever view was mounted last: the panel builds a new one each time
// the sidebar tab opens, and the old ones must not keep listening.
const hooked = new WeakSet();
let onSelection = null;

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

function promptNodeCount(app) {
  const graph = app?.graph;
  const all = graph?._nodes ?? graph?.nodes ?? [];
  return Array.from(all).filter(
    (n) => n && (n.type === PROMPT_NODE || n.comfyClass === PROMPT_NODE)
  ).length;
}

/* ---------------------------------------------------------------- the view */

/* options: { escapeHtml, filter() -> { types, bases }, onFacets(facets), onCount(), app } */
export function renderGalleryView(el, options) {
  const esc = escaper(options.escapeHtml);
  const app = options.app;
  el.classList.add("cu-g");
  el.innerHTML =
    `<div class="cu-g-top">` +
    `<div class="cu-summary cu-g-target" data-g-target></div>` +
    `<input class="cu-g-search" type="search" placeholder="${esc(COPY.search)}" aria-label="Search the gallery" spellcheck="false" autocomplete="off">` +
    `</div><div class="cu-g-bands" data-g-bands></div>`;
  const targetEl = el.querySelector("[data-g-target]");
  const searchEl = el.querySelector(".cu-g-search");
  const bandsEl = el.querySelector("[data-g-bands]");

  let entries = null; // null until the first load
  let error = "";
  let query = "";
  let shown = 0;
  let loading = null;

  let target = null;
  let connected = null;
  let fitting = new Set();
  let targetSeq = 0;
  let hadTarget = null;
  let targetShown = "";

  // Cards are built once and moved between renders, so a search never reloads
  // a thumbnail that is already on screen. Rebuilt only when their text changes.
  const cards = new Map(); // key -> { html, el, model }
  const flashes = new WeakMap(); // card element -> timer

  /* ---- target ---- */

  function setDisabled(card) {
    const pick = card.querySelector(".cu-g-pick");
    if (!pick) return;
    if (target) pick.removeAttribute("aria-disabled");
    else pick.setAttribute("aria-disabled", "true");
  }

  function drawTarget() {
    const html = targetHtml(target, target ? 1 : promptNodeCount(app), options.escapeHtml);
    if (html !== targetShown) {
      targetEl.innerHTML = html;
      targetShown = html;
    }
    targetEl.title = target ? targetLabel(target) : "";
    const has = Boolean(target);
    if (has !== hadTarget) {
      hadTarget = has;
      for (const card of cards.values()) setDisabled(card.el);
    }
  }

  const fitTitle = () => (connected ? `Made for ${describeBase(connected)}` : "");

  /* The target and what fits its model. True when the fit marks need redrawing. */
  async function retarget() {
    hookSelection(app);
    const seq = ++targetSeq;
    target = currentTarget(app);
    drawTarget();
    let base = null;
    if (target && entries) {
      try {
        base = await connectedBase(target);
      } catch {
        base = null;
      }
    }
    if (seq !== targetSeq) return false; // a newer call has the answer
    const next = entries ? new Set(fittingFamilies(entries, base).keys()) : new Set();
    const before = fitTitle();
    const same = next.size === fitting.size && [...next].every((name) => fitting.has(name));
    connected = base;
    fitting = next;
    return !same || fitTitle() !== before;
  }

  /* Only the target line and the fit marks: the bands keep their order until the
     next render, so selecting a node never moves the cards under the cursor. */
  function drawFits() {
    const title = fitTitle();
    for (const band of bandsEl.querySelectorAll("[data-g-band]")) {
      const fits = fitting.has(band.dataset.gBand);
      band.dataset.fits = String(fits);
      band.toggleAttribute("data-quiet", fitting.size > 0 && !fits);
      const mark = band.querySelector(".cu-g-fit");
      if (mark) mark.title = fits ? title : "";
    }
  }

  async function updateTarget() {
    if (await retarget()) drawFits();
  }

  /* For callers that do not wait: the canvas hook, a click, the first draw. */
  function follow() {
    updateTarget().catch((err) => console.warn("[Warp Studio] Gallery target:", err));
  }

  /* ---- loading ---- */

  /* Loads the LoRA index. The first load takes the shared cache (the prompt may
     have filled it); every later one asks the server again, so a model added
     since shows up. The cards on screen stay until the new list is in. */
  function refresh() {
    if (loading) return loading;
    loading = (async () => {
      try {
        if (entries) forget();
        const list = await loras();
        entries = list;
        error = "";
        const keys = new Set(list.map((entry) => entry.id));
        for (const key of cards.keys()) if (!keys.has(key)) cards.delete(key);
        options.onFacets?.(galleryFacets(entries));
      } catch (err) {
        // A failed reload keeps the gallery that works; only a first load shows the error.
        if (!entries) error = err?.message || String(err);
        else console.warn("[Warp Studio] Could not reload the gallery:", err);
      }
      await retarget();
      render();
    })().finally(() => {
      loading = null;
    });
    return loading;
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

  function cardsFor(list) {
    const models = list.map(cardModel);
    const missing = [];
    for (const model of models) {
      const html = cardHtml(model, options.escapeHtml);
      const hit = cards.get(model.key);
      if (hit && hit.html === html) hit.model = model;
      else missing.push({ model, html });
    }
    if (missing.length) {
      // One parse for every new card, not one per card.
      const built = [...fragment(missing.map((m) => m.html).join("")).children];
      missing.forEach(({ model, html }, i) => {
        cards.set(model.key, { html, el: built[i], model });
        setDisabled(built[i]);
      });
    }
    return models.map((model) => cards.get(model.key).el);
  }

  function render() {
    const box = el.isConnected ? scroller() : null;
    const top = box ? box.scrollTop : 0;
    let content;
    if (!entries) {
      shown = 0;
      content = error
        ? fragment(`<div class="cu-g-error">${esc(`Could not load the gallery: ${error}`)}</div>`)
        : fragment(
            `<div class="cu-loading-note"><span class="cu-spinner" aria-hidden="true"></span>${esc(COPY.loading)}</div>`
          );
    } else {
      const filter = options.filter?.() || {};
      const visible = filterEntries(entries, {
        query,
        types: filter.types ?? null,
        bases: filter.bases ?? null,
      });
      shown = visible.length;
      if (!visible.length) {
        content = fragment(
          `<div class="cu-empty">${esc(entries.length ? COPY.empty : COPY.noLoras)}</div>`
        );
      } else {
        const bands = groupEntries(visible, fitting);
        const title = fitTitle();
        content = fragment(
          bands
            .map((band) =>
              bandHtml(
                band,
                { fits: band.fits, quiet: fitting.size > 0 && !band.fits, fitTitle: title },
                options.escapeHtml
              )
            )
            .join("")
        );
        const grids = content.querySelectorAll(".cu-g-grid");
        bands.forEach((band, i) => grids[i].append(...cardsFor(band.entries)));
      }
    }
    bandsEl.replaceChildren(content);
    if (box && box.scrollTop !== top) box.scrollTop = top;
    options.onCount?.();
  }

  /* ---- events ---- */

  function flashAdded(card) {
    clearTimeout(flashes.get(card));
    const label = card.querySelector(".cu-g-added");
    card.setAttribute("data-added", "");
    if (label) label.textContent = COPY.added;
    flashes.set(
      card,
      setTimeout(() => {
        card.removeAttribute("data-added");
        if (label) label.textContent = "";
      }, ADDED_FOR)
    );
  }

  bandsEl.addEventListener("click", (ev) => {
    const pick = ev.target instanceof Element ? ev.target.closest(".cu-g-pick") : null;
    const card = pick?.closest("[data-g-key]");
    const hit = card ? cards.get(card.dataset.gKey) : null;
    if (!hit) return;
    const node = currentTarget(app);
    if (node !== target) follow(); // the selection moved without telling us
    if (!node || !insertIntoNode(node, hit.model.stem)) return;
    noteUsed(node);
    flashAdded(card);
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

  let searchTimer = 0;
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      if (searchEl.value === query) return;
      query = searchEl.value;
      render();
    }, SEARCH_DELAY);
  });

  onSelection = follow;
  follow();
  render();
  return {
    refresh,
    render,
    count: () => (entries ? shown : null),
    updateTarget,
  };
}
