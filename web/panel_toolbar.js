// One toolbar per tab, all icon buttons: search (a field behind a button), Filter,
// Sort and About.
// Markup builders are pure and take the panel's escapeHtml last, as
// gallery_view.js does; wireToolbar is the only part that touches the DOM.
// The styles (cu-tb- classes) live in the panel's style template.

const QUERY_SHOWN = 18;
const QUERY_DELAY_MS = 150;

const escaper = (escapeHtml) => (value) => escapeHtml(value == null ? "" : String(value));

const shortQuery = (query) =>
  query.length > QUERY_SHOWN ? `${query.slice(0, QUERY_SHOWN)}…` : query;

/**
 * The toolbar row, the search field and the empty menu slots. The field is
 * hidden unless `open`, so a re-render while the user types can keep it shown.
 *
 * A tab with no search passes `search: false`; one with no sort leaves
 * `sortLabel` out. `info` adds the ⓘ button and its popover slot.
 */
export function toolbarHtml(
  { query, filtered, sortLabel, info, search = true, open = false },
  escapeHtml
) {
  const esc = escaper(escapeHtml);
  const text = query || "";
  // Icon buttons: the tooltip and the accessible name say what each is and how it
  // is set; data-active draws a dot when a filter or a search narrows the list.
  const icon = (name) => `<i class="pi ${name}" aria-hidden="true"></i>`;
  const button = (name, label, inner, extra = "") =>
    `<button type="button" class="cu-tb-btn cu-tb-icon" data-tb="${name}" title="${esc(label)}" aria-label="${esc(label)}"${extra}>${inner}</button>`;
  const find = search
    ? button(
        "search",
        text ? `Search: ${text}` : "Search",
        icon("pi-search") +
          (text ? `<span class="cu-tb-query">${esc(shortQuery(text))}</span>` : ""),
        text ? " data-active" : ""
      )
    : "";
  const filterLabel = filtered > 0 ? `Filter: ${filtered} active` : "Filter";
  const hasSort = sortLabel != null;
  const about = info
    ? `<button type="button" class="cu-tb-btn cu-tb-icon cu-tb-info" data-tb="info" title="About this tab" aria-label="About this tab" aria-haspopup="true" aria-expanded="false">${icon("pi-info-circle")}</button>`
    : "";
  const menu = ' aria-haspopup="true" aria-expanded="false"';
  return (
    `<div class="cu-tb">` +
    `<div class="cu-tb-row">` +
    button("filter", filterLabel, icon("pi-filter"), menu + (filtered > 0 ? " data-active" : "")) +
    (hasSort ? button("sort", `Sort: ${sortLabel}`, icon("pi-sort-alt"), menu) : "") +
    (find || about ? `<span class="cu-tb-end">${find}${about}</span>` : "") +
    `</div>` +
    (search
      ? `<input type="search" class="cu-tb-field" data-tb="field" value="${esc(text)}" placeholder="Search" aria-label="Search"${open ? "" : " hidden"}>`
      : "") +
    `<div class="cu-tb-menu" data-tb-menu="filter" hidden></div>` +
    (hasSort ? `<div class="cu-tb-menu" data-tb-menu="sort" hidden></div>` : "") +
    (info ? `<div class="cu-tb-menu" data-tb-menu="info" hidden></div>` : "") +
    `</div>`
  );
}

/**
 * Brings a toolbar that is already on the page up to date: the 🔍 text, the
 * Filter and Sort labels. The search field, the menus and focus are left
 * alone, so this is safe while the user types or a menu is open.
 */
export function updateToolbar(root, props, escapeHtml) {
  const holder = root.ownerDocument.createElement("div");
  holder.innerHTML = toolbarHtml(props, escapeHtml);
  for (const name of ["search", "filter", "sort"]) {
    const now = root.querySelector(`[data-tb="${name}"]`);
    const next = holder.querySelector(`[data-tb="${name}"]`);
    if (!now || !next) continue;
    if (now.innerHTML !== next.innerHTML) now.innerHTML = next.innerHTML;
    for (const attr of ["aria-label", "title"]) {
      const value = next.getAttribute(attr);
      if (value !== null && now.getAttribute(attr) !== value) now.setAttribute(attr, value);
    }
    now.toggleAttribute("data-active", next.hasAttribute("data-active"));
  }
}

/**
 * The ⓘ popover: plain lines, then an optional button. `action` is
 * { label, key, disabled }; the caller handles the click on [data-tb-act].
 */
export function infoHtml(lines, action, escapeHtml) {
  const esc = escaper(escapeHtml);
  const rows = (lines || [])
    .filter(Boolean)
    .map((line) => `<div class="cu-tb-line">${esc(line)}</div>`)
    .join("");
  const button = action
    ? `<button type="button" class="cu-btn cu-btn-sm cu-tb-act" data-tb-act="${esc(action.key)}"${action.disabled ? " disabled" : ""}>${esc(action.label)}</button>`
    : "";
  return `<div class="cu-filter-panel cu-tb-info-panel">${rows}${button}</div>`;
}

/**
 * The archive folder the server kept, from a config answer (GET or POST
 * /config). A folder it refused is not in it, so the ⋯ menu doesn't offer it.
 */
export function savedArchiveFolder(answer) {
  return String(answer?.config?.archiveFolder || "").trim();
}

/**
 * The items of the Updates tab's ⋯ menu. Check with archive only appears once
 * an archive folder is saved; both are disabled while a job runs.
 */
export function moreMenuItems({ archiveFolder, busy }) {
  const disabled = Boolean(busy);
  const items = [{ key: "scan", label: "Fetch Missing Metadata", disabled }];
  if (String(archiveFolder || "").trim()) {
    items.push({ key: "archive", label: "Check with archive", disabled });
  }
  return items;
}

let menuCalls = 0;

function sectionHtml(section, esc, uid) {
  const key = esc(section.key);
  if (section.kind === "toggle") {
    return (
      `<div class="cu-tb-section"><label class="cu-filter-option"><input type="checkbox" data-key="${key}"${section.checked ? " checked" : ""}>` +
      `<span>${esc(section.label)}</span></label></div>`
    );
  }
  const type = section.kind === "choice" ? "radio" : "checkbox";
  // "All" and "None" for a long list of checks (the shared Types and Bases).
  const actions = section.actions
    ? `<div class="cu-filter-actions">` +
      `<button type="button" class="cu-text-btn" data-tb-all="${key}">Select all</button>` +
      `<button type="button" class="cu-text-btn" data-tb-none="${key}">Deselect all</button></div>`
    : "";
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
    `${actions}<div class="cu-filter-options">${options || `<div class="cu-filter-empty">No options</div>`}</div></div>`
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
    } else if (name === "filter" || name === "sort" || name === "info") {
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
