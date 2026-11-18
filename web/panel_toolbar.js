// One toolbar per tab: 🔍 (search behind a button), Filter ▾, Sort ▾, and ⓘ.
// Markup builders are pure and take the panel's escapeHtml last, as
// gallery_view.js does; wireToolbar is the only part that touches the DOM.
// The styles (cu-tb- classes) live in the panel's style template.

const QUERY_SHOWN = 18;
const QUERY_DELAY_MS = 150;

const escaper = (escapeHtml) => (value) => escapeHtml(value == null ? "" : String(value));

const shortQuery = (query) =>
  query.length > QUERY_SHOWN ? `${query.slice(0, QUERY_SHOWN)}…` : query;

/**
 * The toolbar row, the search field and the two empty menu slots. The field is
 * hidden unless `open`, so a re-render while the user types can keep it shown.
 */
export function toolbarHtml({ query, filtered, sortLabel, info, open = false }, escapeHtml) {
  const esc = escaper(escapeHtml);
  const text = query || "";
  const search =
    `<button type="button" class="cu-tb-btn" data-tb="search" title="Search" aria-label="${esc(text ? `Search: ${text}` : "Search")}"${text ? " data-active" : ""}>` +
    `🔍${text ? `<span class="cu-tb-query">${esc(shortQuery(text))}</span>` : ""}</button>`;
  const filter = filtered > 0 ? `Filter · ${filtered}` : "Filter";
  return (
    `<div class="cu-tb">` +
    `<div class="cu-tb-row">${search}` +
    `<button type="button" class="cu-tb-btn" data-tb="filter" aria-haspopup="true" aria-expanded="false">${esc(filter)} ▾</button>` +
    `<button type="button" class="cu-tb-btn" data-tb="sort" aria-haspopup="true" aria-expanded="false">${esc(`Sort · ${sortLabel}`)} ▾</button>` +
    (info
      ? `<button type="button" class="cu-tb-btn cu-tb-info" data-tb="info" title="About this tab" aria-label="About this tab">ⓘ</button>`
      : "") +
    `</div>` +
    `<input type="search" class="cu-tb-field" data-tb="field" value="${esc(text)}" placeholder="Search" aria-label="Search"${open ? "" : " hidden"}>` +
    `<div class="cu-tb-menu" data-tb-menu="filter" hidden></div>` +
    `<div class="cu-tb-menu" data-tb-menu="sort" hidden></div>` +
    `</div>`
  );
}

let menuCalls = 0;

function sectionHtml(section, esc, uid) {
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
        `<label class="cu-filter-option"><input type="${type}" name="${uid}-${key}" data-key="${key}" data-value="${esc(option.value)}"${on ? " checked" : ""}>` +
        `<span>${esc(option.label)}</span></label>`
      );
    })
    .join("");
  return (
    `<div class="cu-tb-section"><div class="cu-tb-title">${esc(section.title)}</div>` +
    `<div class="cu-filter-options">${options}</div></div>`
  );
}

/**
 * The menu panel, in the existing cu-filter-panel styles. Radio names carry a
 * per-call id, so two menus with the same key never share a radio group.
 */
export function menuHtml(sections, escapeHtml) {
  const esc = escaper(escapeHtml);
  const uid = `cu-tb-${++menuCalls}`;
  return `<div class="cu-filter-panel">${sections.map((s) => sectionHtml(s, esc, uid)).join("")}</div>`;
}

const wired = new WeakMap();

/**
 * Wire one toolbar. Listens on `root` and the document. Wiring a root that is
 * already wired first removes the earlier wiring, so a caller that re-renders
 * and wires again never stacks listeners. destroy() removes everything this
 * call added; close() just closes the field and the menus.
 */
export function wireToolbar(root, { onQuery, onOpen }) {
  const earlier = wired.get(root);
  if (earlier) earlier();
  const field = () => root.querySelector('[data-tb="field"]');
  const menu = (name) => root.querySelector(`[data-tb-menu="${name}"]`);
  let timer = null;

  const setExpanded = (name, open) => {
    const button = root.querySelector(`[data-tb="${name}"]`);
    if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  };
  const closeMenus = () => {
    for (const el of root.querySelectorAll("[data-tb-menu]")) {
      el.hidden = true;
      setExpanded(el.dataset.tbMenu, false);
    }
  };
  const closeField = () => {
    const input = field();
    if (input) input.hidden = true;
  };
  const focusSearch = () => {
    const button = root.querySelector('[data-tb="search"]');
    if (button && button.focus) button.focus();
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
    if (onOpen) {
      try {
        onOpen(name);
      } catch (err) {
        console.warn("[Warp Studio] Toolbar menu:", err);
      }
    }
    el.hidden = false;
    setExpanded(name, true);
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
    if (event.target !== field() || event.target.value) return;
    closeField();
    // Focus moved to another control: leave it there. Nowhere: back to 🔍.
    if (!event.relatedTarget) focusSearch();
  };
  const onDocClick = (event) => {
    const inside = event.target.closest && event.target.closest("[data-tb-menu], [data-tb]");
    if (!inside || !root.contains(inside)) closeMenus();
  };
  const onKey = (event) => {
    if (event.key !== "Escape") return;
    const input = field();
    const hadField = input && !input.hidden;
    close();
    if (hadField) focusSearch();
  };

  root.addEventListener("click", onClick);
  root.addEventListener("input", onInput);
  root.addEventListener("focusout", onBlur);
  const doc = root.ownerDocument;
  doc.addEventListener("click", onDocClick);
  doc.addEventListener("keydown", onKey);

  const destroy = () => {
    wired.delete(root);
    clearTimeout(timer);
    root.removeEventListener("click", onClick);
    root.removeEventListener("input", onInput);
    root.removeEventListener("focusout", onBlur);
    doc.removeEventListener("click", onDocClick);
    doc.removeEventListener("keydown", onKey);
  };
  wired.set(root, destroy);
  return { close, destroy };
}
