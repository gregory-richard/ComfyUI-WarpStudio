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
export function filterEntries(
  entries,
  { query = "", types = null, bases = null, categories = null, stats = null } = {}
) {
  const terms = fold(query).split(/\s+/).filter(Boolean);
  const wanted = bases ? wantedKeys(bases) : null;
  return entries.filter((entry) => {
    if (types && !types.includes(TYPE)) return false;
    if (categories && !categories.includes(categoryOf({ members: [entry] }, stats))) return false;
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

/** What the filter chips offer: the types present and the bases, sorted, and
 * the categories present by label, with "" (No category) last when some entry has none. */
export function galleryFacets(entries, stats = null) {
  const bases = new Set(entries.map(groupOf));
  const found = new Set(entries.map((entry) => categoryOf({ members: [entry] }, stats)));
  const categories = Object.keys(CATEGORY_LABELS).filter((key) => found.has(key));
  categories.sort((x, y) => CATEGORY_LABELS[x].localeCompare(CATEGORY_LABELS[y]));
  if (found.has("")) categories.push("");
  return {
    modelTypes: entries.length ? [TYPE] : [],
    baseModels: [...bases].sort((x, y) => fold(x).localeCompare(fold(y)) || x.localeCompare(y)),
    categories,
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

export const STAT_SORTS = ["downloads", "likes", "comments"];

/** Civitai's categories as the Gallery names them, keyed by the tag. */
export const CATEGORY_LABELS = {
  character: "Character",
  style: "Style",
  celebrity: "Celebrity",
  concept: "Concept",
  clothing: "Clothing",
  "base model": "Base model",
  poses: "Poses",
  background: "Background",
  tool: "Tool",
  buildings: "Buildings",
  vehicle: "Vehicles",
  objects: "Objects",
  animal: "Animals",
  action: "Action",
  assets: "Assets",
};
export const NO_CATEGORY = "No category";

/** The Civitai numbers kept for a stack's model, or null. */
function recordOf(stack, stats) {
  const id = stack.members[0].model_id;
  return (id && stats?.get?.(String(id))) || null;
}

/** The shown member's number for a stat sort, or null when there is none. */
export function statOf(stack, sort, stats) {
  const value = recordOf(stack, stats)?.[sort];
  return Number.isFinite(value) ? value : null;
}

/** The stack's Civitai category key, or "" when none is known. */
export function categoryOf(stack, stats) {
  const key = recordOf(stack, stats)?.category;
  return typeof key === "string" && Object.hasOwn(CATEGORY_LABELS, key) ? key : "";
}

/** A count for a card badge: 950, 11.6k, 1.2M. Not a count gives "". */
export function formatCount(n) {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "";
  if (n < 1000) return String(Math.round(n));
  const tenths = (value) => Math.round(value * 10) / 10;
  const k = tenths(n / 1000);
  if (k < 1000) return `${k}k`;
  return `${tenths(n / 1e6)}M`;
}

/** Stacks as bands: by base (the ones that fit first, then the biggest) or as
 * one band. Within a band, by the shown member's title, by when it was added, or
 * by a Civitai number (highest first; stacks without one last, by name). */
export function arrange(
  stacks,
  { groupBy = "base", sort = "name", fitting = new Set(), stats = new Map() } = {}
) {
  const shown = (stack) => stack.members[0];
  const byName = (x, y) =>
    fold(cardTitle(shown(x))).localeCompare(fold(cardTitle(shown(y)))) || byId(shown(x), shown(y));
  const byAdded = (x, y) => time(shown(y).added) - time(shown(x).added) || byName(x, y);
  const byStat = (x, y) => {
    const nx = statOf(x, sort, stats);
    const ny = statOf(y, sort, stats);
    if (nx === null || ny === null) return (nx === null) - (ny === null) || byName(x, y);
    return ny - nx || byName(x, y);
  };
  const order = sort === "newest" ? byAdded : STAT_SORTS.includes(sort) ? byStat : byName;
  if (groupBy === "none") {
    return [{ base: null, fits: false, stacks: [...stacks].sort(order) }];
  }
  if (groupBy === "category") {
    const byLabel = new Map();
    for (const stack of stacks) {
      const key = categoryOf(stack, stats);
      const label = key ? CATEGORY_LABELS[key] : NO_CATEGORY;
      if (!byLabel.has(label)) byLabel.set(label, []);
      byLabel.get(label).push(stack);
    }
    return [...byLabel]
      .map(([label, list]) => ({ base: label, fits: false, stacks: list.sort(order) }))
      .sort(
        (x, y) =>
          (x.base === NO_CATEGORY) - (y.base === NO_CATEGORY) || x.base.localeCompare(y.base)
      );
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

/** The stored fold list after one band is toggled. Every other entry stays,
 *  including a base that is not listed right now (a filter hides it), so it is
 *  still folded when it comes back. */
export function toggleFolded(stored, base) {
  const list = Array.isArray(stored) ? stored : [];
  return list.includes(base) ? list.filter((name) => name !== base) : [...list, base];
}

const TYPE_LABEL = { loras: "LoRA", models: "Checkpoint", embeddings: "Embedding" };

/** How many bases a model covers besides the card's own, as the Gallery names
 * them (the same filing the bands use). null when no numbers are known. */
function otherBases(bases, own) {
  if (!Array.isArray(bases)) return null;
  const seen = new Set([baseKey(own)]);
  for (const raw of bases) {
    const key = baseKey(normaliseBase(raw) ?? raw);
    if (key) seen.add(key);
  }
  return seen.size - 1;
}

/** What one card shows. Text is raw; escaping is the view's job.
 *
 * `numbers` is the model's Civitai record from the check (see statOf), if any. */
export function cardModel(entry, numbers = null) {
  const stem = stemOf(entry.id);
  const base = groupOf(entry);
  const kind = entry.kind || KIND;
  const has = numbers && typeof numbers === "object";
  const image = has && typeof numbers.creatorImage === "string" ? numbers.creatorImage : "";
  return {
    key: entry.id,
    title: cardTitle(entry),
    kind,
    typeLabel: TYPE_LABEL[kind] || TYPE_LABEL[KIND],
    base,
    moreBases: has ? otherBases(numbers.bases, base) : null,
    creator: entry.creator || "",
    creatorImage: image,
    detail: entry.structured && entry.version ? `${entry.version} · ${base}` : base,
    versionSuffix: entry.structured && entry.version ? entry.version : "",
    stats: has
      ? {
          downloads: num(numbers.downloads),
          likes: num(numbers.likes),
          comments: num(numbers.comments),
          tipped: num(numbers.tipped),
        }
      : null,
    triggers: (entry.triggers || []).length,
    url: entry.url || null,
    thumbnail: entry.has_preview ? entry.thumbnail : null,
    stem,
    missing: Boolean(entry.missing),
  };
}
