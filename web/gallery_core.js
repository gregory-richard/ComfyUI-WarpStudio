// The gallery's logic without its markup: which LoRAs a search and the filters
// leave, how they fall into bands, and what a card shows for each.
//
// Pure on purpose, so node can test it and the panel only has to draw.
import { baseKey, groupOf, normaliseBase } from "./model_base.js";
import { stemOf } from "./text.js";

const TYPE = "lora"; // the type the panel's shared Types menu calls a LoRA
const KIND = "loras"; // the folder key an entry carries as its kind
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
    const hay = [entry.creator, entry.name, entry.title, entry.version, groupOf(entry)]
      .map(fold)
      .join(" ");
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

const num = (value) => (Number.isFinite(Number(value)) && value != null ? Number(value) : 0);
const time = (iso) => {
  const ms = Date.parse(iso ?? "");
  return Number.isNaN(ms) ? 0 : ms;
};
const byId = (x, y) => String(x.id).localeCompare(String(y.id));

/** The name a card shows: the file's own when it follows the naming scheme. */
function cardTitle(entry) {
  const name = entry.structured ? entry.name : entry.title || entry.name;
  return name || stemOf(entry.id);
}

/** Entries of one Civitai model and base as a stack, newest version first.
 *
 * An entry without a model id stands alone. Stacks keep the order in which
 * their first member appears.
 */
export function stackEntries(entries) {
  const stacks = new Map();
  for (const entry of entries) {
    const key = entry.model_id
      ? `stack:${entry.kind || KIND}:${entry.model_id}:${groupOf(entry)}`
      : `file:${entry.id}`;
    if (!stacks.has(key)) stacks.set(key, { key, members: [] });
    stacks.get(key).members.push(entry);
  }
  const newest = (x, y) =>
    time(y.published) - time(x.published) || num(y.version_id) - num(x.version_id) || byId(x, y);
  return [...stacks.values()].map((stack) => ({ ...stack, members: stack.members.sort(newest) }));
}

/** Stacks as bands: by base (the ones that fit first, then the biggest) or as
 * one band. Within a band, by the shown member's title or by when it was added. */
export function arrange(stacks, { groupBy = "base", sort = "name", fitting = new Set() } = {}) {
  const shown = (stack) => stack.members[0];
  const byName = (x, y) =>
    fold(cardTitle(shown(x))).localeCompare(fold(cardTitle(shown(y)))) || byId(shown(x), shown(y));
  const byAdded = (x, y) => time(shown(y).added) - time(shown(x).added) || byName(x, y);
  const order = sort === "newest" ? byAdded : byName;
  if (groupBy === "none") {
    return [{ base: null, fits: false, stacks: [...stacks].sort(order) }];
  }
  const bands = new Map();
  for (const stack of stacks) {
    const base = groupOf(shown(stack));
    if (!bands.has(base)) bands.set(base, []);
    bands.get(base).push(stack);
  }
  return [...bands]
    .map(([base, list]) => ({ base, fits: fitting.has(base), stacks: list.sort(order) }))
    .sort(
      (x, y) =>
        Number(y.fits) - Number(x.fits) ||
        y.stacks.length - x.stacks.length ||
        x.base.localeCompare(y.base)
    );
}

/** The folded bases that still exist; a remembered one that is gone is dropped. */
export function foldedIn(stored, bases) {
  const present = new Set(bases);
  return new Set((Array.isArray(stored) ? stored : []).filter((base) => present.has(base)));
}

/** What one card shows. Text is raw; escaping is the view's job. */
export function cardModel(entry) {
  const stem = stemOf(entry.id);
  const base = groupOf(entry);
  return {
    key: entry.id,
    title: cardTitle(entry),
    kind: entry.kind || KIND,
    creator: entry.creator || "",
    detail: entry.structured && entry.version ? `${entry.version} · ${base}` : base,
    triggers: (entry.triggers || []).length,
    url: entry.url || null,
    thumbnail: entry.has_preview ? entry.thumbnail : null,
    stem,
    missing: Boolean(entry.missing),
  };
}
