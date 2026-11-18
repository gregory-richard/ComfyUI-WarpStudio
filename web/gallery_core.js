// The gallery's logic without its markup: which LoRAs a search and the filters
// leave, how they fall into bands, and what a card shows for each.
//
// Pure on purpose, so node can test it and the panel only has to draw.
import { groupOf } from "./model_base.js";
import { stemOf } from "./text.js";

const TYPE = "lora";
const label = (entry) => entry.title || entry.name || "";
const fold = (text) => String(text ?? "").toLowerCase();

/** The entries that match the search and the selected types and bases.
 *
 * null for types or bases means no filter; an empty array means nothing is
 * selected, so nothing matches.
 */
export function filterEntries(entries, { query = "", types = null, bases = null } = {}) {
  const terms = fold(query).split(/\s+/).filter(Boolean);
  return entries.filter((entry) => {
    if (types && !types.includes(TYPE)) return false;
    if (bases && !bases.includes(groupOf(entry))) return false;
    if (!terms.length) return true;
    const hay = fold(`${entry.creator} ${label(entry)} ${entry.version} ${groupOf(entry)}`);
    return terms.every((term) => hay.includes(term));
  });
}

/** What the filter chips offer: the types present and the bases, sorted. */
export function galleryFacets(entries) {
  const bases = new Set(entries.map(groupOf));
  return {
    modelTypes: entries.length ? [TYPE] : [],
    baseModels: [...bases].sort((x, y) => fold(x).localeCompare(fold(y)) || x.localeCompare(y)),
  };
}

/** Entries as bands by base: the ones that fit first, then the biggest. */
export function groupEntries(entries, fitting = new Set()) {
  const bands = new Map();
  for (const entry of entries) {
    const base = groupOf(entry);
    if (!bands.has(base)) bands.set(base, []);
    bands.get(base).push(entry);
  }
  const byName = (x, y) =>
    fold(label(x)).localeCompare(fold(label(y))) || String(x.id).localeCompare(String(y.id));
  return [...bands]
    .map(([base, list]) => ({ base, fits: fitting.has(base), entries: list.sort(byName) }))
    .sort(
      (x, y) =>
        Number(y.fits) - Number(x.fits) ||
        y.entries.length - x.entries.length ||
        x.base.localeCompare(y.base)
    );
}

/** What one card shows. Text is raw; escaping is the view's job. */
export function cardModel(entry) {
  const stem = stemOf(entry.id);
  const base = groupOf(entry);
  return {
    key: entry.id,
    title: entry.title || entry.name || stem,
    creator: entry.creator || "",
    detail: entry.structured && entry.version ? `${entry.version} · ${base}` : base,
    triggers: (entry.triggers || []).length,
    url: entry.url,
    thumbnail: entry.has_preview ? entry.thumbnail : null,
    stem,
  };
}
