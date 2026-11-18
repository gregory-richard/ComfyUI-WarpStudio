// One toolbar per tab: 🔍 (search behind a button), Filter ▾, Sort ▾, and ⓘ.
// Markup builders are pure and take the panel's escapeHtml last, as
// gallery_view.js does; wireToolbar is the only part that touches the DOM.
// The styles (cu-tb- classes) live in the panel's style template.

const QUERY_SHOWN = 18;
const QUERY_DELAY_MS = 150;

const escaper = (escapeHtml) => (value) => escapeHtml(value == null ? "" : String(value));

const shortQuery = (query) =>
  query.length > QUERY_SHOWN ? `${query.slice(0, QUERY_SHOWN)}…` : query;

/** The toolbar row, the hidden search field and the two empty menu slots. */
export function toolbarHtml({ query, filtered, sortLabel, info }, escapeHtml) {
  const esc = escaper(escapeHtml);
  const text = query || "";
  const search =
    `<button type="button" class="cu-tb-btn" data-tb="search" title="Search" aria-label="Search"${text ? " data-active" : ""}>` +
    `🔍${text ? `<span class="cu-tb-query">${esc(shortQuery(text))}</span>` : ""}</button>`;
  const filter = filtered > 0 ? `Filter · ${filtered}` : "Filter";
  return (
    `<div class="cu-tb">` +
    `<div class="cu-tb-row">${search}` +
    `<button type="button" class="cu-tb-btn" data-tb="filter" aria-haspopup="true">${esc(filter)} ▾</button>` +
    `<button type="button" class="cu-tb-btn" data-tb="sort" aria-haspopup="true">${esc(`Sort · ${sortLabel}`)} ▾</button>` +
    (info
      ? `<button type="button" class="cu-tb-btn cu-tb-info" data-tb="info" title="About this tab" aria-label="About this tab">ⓘ</button>`
      : "") +
    `</div>` +
    `<input type="search" class="cu-tb-field" data-tb="field" value="${esc(text)}" placeholder="Search" aria-label="Search" hidden>` +
    `<div class="cu-tb-menu" data-tb-menu="filter" hidden></div>` +
    `<div class="cu-tb-menu" data-tb-menu="sort" hidden></div>` +
    `</div>`
  );
}

function sectionHtml(section, esc) {
  const key = esc(section.key);
  if (section.kind === "toggle") {
    return (
      `<label class="cu-filter-option"><input type="checkbox" data-key="${key}"${section.checked ? " checked" : ""}>` +
      `<span>${esc(section.label)}</span></label>`
    );
  }
  const type = section.kind === "choice" ? "radio" : "checkbox";
  const options = section.options
    .map((option) => {
      const on = section.kind === "choice" ? option.value === section.value : option.checked;
      return (
        `<label class="cu-filter-option"><input type="${type}" name="${key}" data-key="${key}" data-value="${esc(option.value)}"${on ? " checked" : ""}>` +
        `<span>${esc(option.label)}</span></label>`
      );
    })
    .join("");
  return (
    `<div class="cu-tb-section"><div class="cu-tb-title">${esc(section.title)}</div>` +
    `<div class="cu-filter-options">${options}</div></div>`
  );
}

/** The menu panel, in the existing cu-filter-panel styles. */
export function menuHtml(sections, escapeHtml) {
  const esc = escaper(escapeHtml);
  return `<div class="cu-filter-panel">${sections.map((s) => sectionHtml(s, esc)).join("")}</div>`;
}

/**
 * Wire one toolbar. Listens on `root` and the document; destroy() removes the
 * document listeners (call it before re-rendering), close() just closes.
 */
export function wireToolbar(root, { onQuery, onOpen }) {
  const field = () => root.querySelector('[data-tb="field"]');
  const menu = (name) => root.querySelector(`[data-tb-menu="${name}"]`);
  let timer = null;

  const closeMenus = () => {
    for (const el of root.querySelectorAll("[data-tb-menu]")) el.hidden = true;
  };
  const closeField = () => {
    const input = field();
    if (input) input.hidden = true;
  };
  const close = () => {
    closeMenus();
    closeField();
  };

  const openMenu = (name) => {
    const el = menu(name);
    if (!el) return;
    const wasOpen = !el.hidden;
    closeMenus();
    if (wasOpen) return;
    if (onOpen) onOpen(name);
    el.hidden = false;
  };

  const onClick = (event) => {
    const button = event.target.closest && event.target.closest("[data-tb]");
    if (!button || !root.contains(button)) return;
    const name = button.dataset.tb;
    if (name === "search") {
      closeMenus();
      const input = field();
      if (!input) return;
      input.hidden = false;
      input.focus();
    } else if (name === "filter" || name === "sort") {
      openMenu(name);
    }
  };
  const onInput = (event) => {
    if (event.target !== field()) return;
    clearTimeout(timer);
    const value = event.target.value;
    timer = setTimeout(() => onQuery && onQuery(value), QUERY_DELAY_MS);
  };
  const onBlur = (event) => {
    if (event.target === field() && !event.target.value) closeField();
  };
  const onDocClick = (event) => {
    const inside = event.target.closest && event.target.closest("[data-tb-menu], [data-tb]");
    if (!inside || !root.contains(inside)) closeMenus();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };

  root.addEventListener("click", onClick);
  root.addEventListener("input", onInput);
  root.addEventListener("focusout", onBlur);
  const doc = root.ownerDocument;
  doc.addEventListener("click", onDocClick);
  doc.addEventListener("keydown", onKey);

  return {
    close,
    destroy() {
      clearTimeout(timer);
      root.removeEventListener("click", onClick);
      root.removeEventListener("input", onInput);
      root.removeEventListener("focusout", onBlur);
      doc.removeEventListener("click", onDocClick);
      doc.removeEventListener("keydown", onKey);
    },
  };
}
