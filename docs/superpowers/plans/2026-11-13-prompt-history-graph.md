# Prompt History Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a Prompt Writer chat as a list in creation order with a git-style graph lane, make hand
edits and node prompts versions of their own, and let branches fold.

**Architecture:** All data rules (kinds, matching a text to versions, migration, lanes, folding, paths)
are pure functions in `web/prompt_chats.js`, unit-tested under node. `web/prompt_writer.js` only wires
them to the DOM: a gutter SVG drawn from measured card positions, fold arrows, and the save/send paths.

**Tech Stack:** Plain ES modules in the ComfyUI frontend, `node --test`, eslint, prettier, Playwright
(scratchpad script) against the running ComfyUI.

**Spec:** `docs/superpowers/specs/2026-11-13-prompt-history-graph-design.md`

## Global Constraints

- English UI text. No examples added to any system prompt. No change to what the LLM receives.
- Version numbers are `n` = array position + 1; versions are only appended (migration inserts once).
- Close = `diffStats(diffWords(candidate.text, text)).rewrite === false` (at most 60% of the words
  changed), the same test as "Mostly rewritten".
- Chats file stays `version: 1`; 5.0.0 must still read a migrated file without losing text.
- Tests use demo chats only; never the user's real `warppipe_prompt_chats.json` or settings.
- Commit only on `feat/writer-outside-click`; no push, PR or version bump.

## Review Focus

- A node prompt that differs from a version only in whitespace or punctuation → treated as close (a hand
  edit), never as a new root. Test in Task 2.
- An old chat whose `basePrompt` equals an earlier version exactly → no inserted version, and
  `n` unchanged. Test in Task 2.
- Typing in the open box, then pressing **Send to prompt** on that card → the click is not lost and the
  node gets the edited text. Checked in Task 6 (Playwright).
- Folding a version that hides the composer's choice → the choice stays (the menu still names it) and
  the next write unfolds the path. Test in Task 3.
- A pending question or failure in a migrated chat whose base was the node prompt → Retry and the answer
  still edit the same text. Test in Task 2.

---

### Task 1: Version kinds in the store

**Files:**
- Modify: `web/prompt_chats.js` (normalizeVersion, normalizeChat, createChat, addVersion, openQuestion,
  recordFailure, startOptions, startLabel)
- Test: `tests/web/prompt_chats.test.mjs`

**Interfaces:**
- Produces:
  - `KIND_WRITTEN = "written"`, `KIND_HAND = "hand"`, `KIND_NODE = "node"`.
  - Every version has `kind`, `editedIn` (`"node"` or null), `source` (string or null), `sentAt`
    (number or null). `addVersion(chat, {..., kind = KIND_WRITTEN, editedIn = null, source = null})`;
    `basePrompt` is gone from versions, pending steps and failures.
  - `addHandVersion(chat, {id, now, baseId, text, editedIn = null}) -> version`
  - `addNodeVersion(chat, {id, now, text, source}) -> version`
  - `markSent(version, now)`
  - `originLabel(chat, version) -> string`: `"new prompt"`, `"edit of v3"`, `"hand edit of v3"`,
    `"hand edit of v4, in the node"`, `"from <source>"` (`"from the node"` when source is null).
  - `startLabel(chat, baseId)` (for live/question/failure cards): `"new prompt"` or `"edit of vN"`.
  - Chats have `folded: string[]`.

- [ ] **Step 1: Write failing tests**
  - `"addVersion marks a version written, never sent"`: `kind === "written"`, `sentAt === null`,
    `editedIn === null`, `source === null`; no `basePrompt` key.
  - `"addHandVersion and addNodeVersion"`: hand has `baseId`, `request: ""`, `model: ""`, `cost: null`;
    node has `baseId: null`, `source: "🌀 Prompt + LoRAs"`; both numbered by position.
  - `"the first written version names the chat"`: node version first, then written "make it morning" →
    title `"make it morning"`; a later written version does not rename.
  - `"normalizeStore keeps kinds, sentAt and folded, and repairs junk"`: unknown kind → `"written"`,
    `editedIn` other than `"node"` → null, non-number `sentAt` → null, `folded` keeps only ids of
    existing versions, missing → `[]`.
  - `"originLabel says where a version came from"`: the five strings above.
  - `"startOptions names hand and node versions"`: `"Edit v2 (edited by hand)"`,
    `"Edit v3 (edited in the node)"`, `"Edit v1 (taken from the node)"`, newest still `"(newest)"`.
  - Update the existing tests that pass or expect `basePrompt` (around lines 194, 571, 589, 609, 691,
    717) to the new shape.

- [ ] **Step 2: Run** `npm test` → the new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above in `web/prompt_chats.js`. Titles: `addVersion` and
  `addHandVersion`/`addNodeVersion` only set the title when the new version is written and no earlier
  version is written. `createChat` adds `folded: []`.
- [ ] **Step 4: Run** `npm test` → PASS.
- [ ] **Step 5: Commit** `feat: version kinds for hand edits and node prompts`.

### Task 2: Matching a text to versions, and the migration

**Files:**
- Modify: `web/prompt_chats.js` (new `matchText`, `adoptText`; `normalizeChat`)
- Test: `tests/web/prompt_chats.test.mjs`

**Interfaces:**
- Consumes: Task 1 kinds; `diffWords`, `diffStats` from `web/prompt_writer_core.js`.
- Produces:
  - `matchText(versions, text) -> {how: "same"|"close", version} | null` — same: newest version with
    exactly this text; else rank by shared words (multiset overlap / total, ties to the newer), diff only
    the best, close when not a rewrite.
  - `adoptText(chat, text, {id, now, source}) -> version` — same → that version; close → new hand
    version (`editedIn: "node"`) under it; else → new node version.

- [ ] **Step 1: Write failing tests**
  - `"matchText finds the same text, newest first"`.
  - `"matchText calls a small tweak close"`: base `"a red bike by a lake at dusk"`, text
    `"a red bike by a calm lake at dusk"` → close to it.
  - `"matchText calls whitespace and punctuation changes close"` (Review Focus).
  - `"matchText calls a different prompt nothing"`: `"portrait of an old sailor"` → null.
  - `"adoptText adds a hand version in the node, or a node version, or nothing"`: three cases with
    `kind`, `editedIn`, `baseId`, `source` asserted, and `chat.versions.length` before/after.
  - `"migration: a quoted node prompt equal to an earlier version adds nothing"` (Review Focus): `n`
    unchanged, `baseId` = that version, no `basePrompt` key.
  - `"migration: a close node prompt becomes a hand edit inserted before"`: id `"v2~node"`, `created`
    copied, `editedIn: "node"`, later versions renumbered.
  - `"migration: another node prompt becomes a node version"` with `source` = `targetLabel`.
  - `"migration: seed, 'prompt' without text and unknown bases become roots"`.
  - `"migration: a pending question and a failure get their node prompt as a version at the end"`
    (Review Focus): ids `"c~pending"` / `"c~failure"`, step `baseId` points to it, the version's text is
    the old `basePrompt`.
  - `"migration is idempotent"`: `normalizeStore(normalizeStore(old))` deep-equals
    `normalizeStore(old)`.

- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement** `matchText`, `adoptText`, and the migration in `normalizeChat` (versions in
  order, then pending, then failure; empty base text → root).
- [ ] **Step 4: Run** `npm test` → PASS.
- [ ] **Step 5: Commit** `feat: node prompts become versions, also in old chats`.

### Task 3: Graph layout, folding and the path

**Files:**
- Modify: `web/prompt_chats.js`
- Test: `tests/web/prompt_chats.test.mjs`

**Interfaces:**
- Produces:
  - `childrenOf(chat, id) -> version[]`, `foldedCount(chat, id) -> number` (all descendants).
  - `toggleFold(chat, id)`, `unfoldTo(chat, id)` (removes the id's ancestors from `folded`).
  - `visibleVersions(chat) -> version[]` — in order, without descendants of folded versions.
  - `pathTo(chat, id) -> Set<string>` — the id and its ancestors.
  - `historyLayout(items) -> {lanes: number, rows: [{id, lane, parentRow: number|null}]}` where
    `items` is `[{id, baseId}]` in order (versions plus an optional `{id: "next", baseId}` at the end).
    A first follow-up continues its parent's lane; other follow-ups start a chain of their own. Chains
    (head + first follow-ups) occupy `[start, end]` rows, start = parent's row (or own row for a root);
    assign by start, lowest lane whose previous chain ended before `start`.

- [ ] **Step 1: Write failing tests**
  - `"historyLayout: a chain stays in one lane"`: 4 versions each from the previous → all lane 0,
    `lanes === 1`.
  - `"historyLayout: the spec example"`: v1←, v2←v1, v3←v2, v4←v3, v5←v2, v6←v5, v7←v3, v8←v4 → lanes
    v1-v4,v8 = 0; v5,v6 and v7 in two different lanes > 0; `parentRow` of v5 = 1.
  - `"historyLayout: a lane is reused after its chain ended"`.
  - `"historyLayout: roots start their own chain"` (node version after a chain).
  - `"visibleVersions hides what came from a folded version"` and `"foldedCount counts all of it"`.
  - `"unfoldTo opens the path to a hidden version"`; `"folding may hide the composer's choice"`
    (Review Focus): `startValue` still returns it; after `unfoldTo(chat, newId)` it is visible.
  - `"pathTo walks back to the root"`.

- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement** the functions above.
- [ ] **Step 4: Run** `npm test` → PASS.
- [ ] **Step 5: Commit** `feat: graph lanes, folding and paths for chat versions`.

### Task 4: Hand edits, Edit current prompt, Again and titles in the Prompt Writer

**Files:**
- Modify: `web/prompt_chats.js` (new `isUsed`, `editVersion`), `web/prompt_writer.js` (saveEdit,
  flushEdits, nextStep, startWrite, launch, finish, retrySpec, writeAgain, fillDraft, useVersion,
  toggleSuggestion, stepAsk, versionBase, versionCard)
- Test: `tests/web/prompt_chats.test.mjs`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `isUsed(chat, version, writingBaseId = null) -> boolean` — `sentAt`, a follow-up, pending/failure
    base, or the running write's base.
  - `editVersion(chat, version, text, {id, now, writingBaseId}) -> {version, made} | null` — null when
    the text is empty or equal ignoring whitespace; an unused hand version is changed in place
    (`made: false`); otherwise a hand version under it is appended (`made: true`).

- [ ] **Step 1: Write failing tests**
  - `"editVersion keeps what the AI wrote and adds a hand edit"`.
  - `"editVersion changes an unused hand edit in place, a sent one gets a follow-up"`.
  - `"editVersion ignores an empty or whitespace-only change"`.
  - `"isUsed: sent, followed up, waiting, failed or being written from"`.
- [ ] **Step 2: Run** `npm test` → FAIL; implement both; run → PASS.
- [ ] **Step 3: Wire the Prompt Writer**
  - `saveEdit`: use `editVersion`; on `made`, move `draft.base` from the old id to the new one, point the
    editor entry at the new version, record `view.successor.set(old.id, new.id)`, save, and redraw only
    after the current pointer press ends (`pointerup` then `setTimeout 0`; at once when no press).
    On null, put the version's text back into the box.
  - `current(version)` follows `view.successor`; `useVersion` and `toggleSuggestion` flush, then act on
    `current(version)`. `useVersion` calls `markSent` and saves.
  - `nextStep`/`startWrite`: Edit current prompt calls `adoptText(chat, nodeText, {id: uid(), now,
    source: targetNow()?.label})`, saves, and the step's `baseId` is that version's id; `baseText` is
    always the base version's text. Remove every `basePrompt` use (spec, pending, failure, retrySpec,
    stepAsk quote, versionBase, writeAgain note).
  - `launch`: ask for the AI title when the chat has no written version yet. `finish`, the answer and
    Retry call `unfoldTo(chat, newVersion.id)`.
  - Again only for written versions; `stepAsk` shows nothing for hand and node versions.
  - Card heads use `originLabel`; closed rows show the request, "Edited by hand" / "Edited in the node"
    with `+a −r words`, or "Taken from the node", followed by the origin label.
- [ ] **Step 4: Run** `npm run check` → PASS.
- [ ] **Step 5: Commit** `feat: hand edits and node prompts become versions in the Prompt Writer`.

### Task 5: The graph gutter and folding in the Prompt Writer

**Files:**
- Modify: `web/prompt_writer.js` (CSS block, renderConvo, versionCard, liveCard, new `drawGraph`)

**Interfaces:**
- Consumes: `visibleVersions`, `historyLayout`, `pathTo`, `foldedCount`, `toggleFold`, `startValue`.

- [ ] **Step 1: Implement**
  - `renderConvo` puts the cards in `div.wpw-history` (position relative, flex column, gap 14px,
    `padding-left` = 12 + lanes × 14 px) with an absolutely placed `svg.wpw-graph` on its left; the
    live/question/failure card is the item `{id: "next", baseId}`.
  - `drawGraph()` measures each card's `.wpw-card-head` centre, draws edges (straight in one lane, a
    curve out of the parent's dot into a new lane), then dots: filled written, hollow hand, square node,
    dashed ring for `"next"`. Path from `pathTo(chat, startValue(...))` in `var(--wpw-warp)`, the rest in
    `var(--wpw-muted)` at reduced opacity.
  - A `ResizeObserver` on the history container's cards and the window `resize` call `drawGraph` on the
    next animation frame; `paintLive` and `fitEditor` call it too.
  - Fold arrow (`pi-chevron-down` / `pi-chevron-right`) at the start of the head of every version with
    follow-ups; folded heads show a `+N` tag. Click toggles, saves, redraws (and does not open the card).
  - Choosing a hidden version in the start menu calls `unfoldTo`.
- [ ] **Step 2: Run** `npm run check` → PASS.
- [ ] **Step 3: Commit** `feat: the Prompt Writer shows its history as a graph`.

### Task 6: Docs, browser check, wrap-up

**Files:**
- Modify: `web/docs/Prompt Writer.md` (Versions, Writing a request, Chats), `CHANGELOG.md`
  (Unreleased → Added/Changed)
- Scratchpad: `shots/history.mjs` (Playwright, demo chats via `lib.mjs`)

- [ ] **Step 1: Write the docs** — graph, numbers in creation order, hand edits and "in the node",
  Edit current prompt making a version, folding, Again only on written versions, old chats migrated once.
- [ ] **Step 2: Playwright check** with a demo chat like the spec example plus a migrated old chat:
  lanes and dots drawn, fold hides and shows `+N`, typing then **Send to prompt** writes the edited text
  and makes a hand version (Review Focus), Edit current prompt with a tweak makes "hand edit … in the
  node", with a different text makes "from …". Screenshots looked at.
- [ ] **Step 3: Run** `npm run check` → PASS.
- [ ] **Step 4: Commit** `docs: the prompt history graph`.
