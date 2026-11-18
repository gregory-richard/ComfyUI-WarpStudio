// The gallery's logic without its markup: which LoRAs a search and the filters
// leave, how they fall into bands, and what a card shows for each.
//
// Pure on purpose, so node can test it and the panel only has to draw.
import { baseKey, groupOf, normaliseBase } from "./model_base.js";
import { stemOf } from "./text.js";

const TYPE = "lora";
const label = (entry) => entry.title || entry.name || "";
const fold = (text) => String(text ?? "").toLowerCase();

/* The keys a selected base stands for: as written, and as the server files it.
   The first keeps a folder name that ends in "turbo" matching its own LoRAs;
   the second lets the check's "ZImageTurbo" find the LoRAs filed as "ZImage". */
function wantedKeys(bases) {
  const keys = new Set();
  for (const base of bases) {
    for (const name of [base, normaliseBase(base)]) {
      const key = baseKey(name);
      if (key) keys.add(key);
    }
  }
  return keys;
}

/** The entries that match the search and the selected types and bases.
 *
 * null for types or bases means no filter; an empty array means nothing is
 * selected, so nothing matches. A base matches spelling aside, and a Turbo or
 * Base spelling matches the base it is filed under.
 */
export function filterEntries(entries, { query = "", types = null, bases = null } = {}) {
  const terms = fold(query).split(/\s+/).filter(Boolean);
  const wanted = bases ? wantedKeys(bases) : null;
  return entries.filter((entry) => {
    if (types && !types.includes(TYPE)) return false;
    if (bases) {
      const group = groupOf(entry);
      if (!bases.includes(group) && !wanted.has(baseKey(group))) return false;
    }
    if (!terms.length) return true;
    const hay = [entry.creator, label(entry), entry.version, groupOf(entry)].map(fold).join(" ");
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

/** The gallery's bases that no other view's base already stands for.
 *
 * The panel's Bases menu is one list for every view. The check and the dry run
 * name Civitai's spelling ("ZImageTurbo"); the gallery names it the way the
 * server filed it ("ZImage"). Listing both would offer the same base twice.
 * A gallery base goes when ticking one of the others shows its LoRAs anyway.
 */
export function galleryOnlyBases(galleryBases, otherBases) {
  const taken = wantedKeys((otherBases || []).filter((base) => typeof base === "string"));
  return (galleryBases || []).filter((base) => !taken.has(baseKey(base)));
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
    url: entry.url || null,
    thumbnail: entry.has_preview ? entry.thumbnail : null,
    stem,
  };
}
