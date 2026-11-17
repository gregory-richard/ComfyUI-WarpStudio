# Prompt history graph — design

Date: 2026-11-13. Branch: `feat/writer-outside-click` (collects the changes after 5.0.0).

## Why

Every version in a Prompt Writer chat already remembers what it was made from (`baseId`), but the
chat shows a flat list v1…vN. The user often goes back to an earlier version and tries another
request on it. Their largest chat has 25 versions, and 17 of its 24 edits were made from a version that
already had a follow-up. In a flat list, a card saying "edit of v1" sits below v3, and it is hard to
tell what came from what.

Two kinds of text are also lost:

- **Edit current prompt** keeps the node's prompt only as a quote inside the new version
  (`basePrompt`, "The current prompt it edited"). It is not a version, so it cannot be chosen again.
  After **Send to prompt** puts the new version into the node, the original is gone from the choices.
  That is the case the user hit: try the new prompt, then go back and edit the original one.
- Typing in the newest version's box overwrites that version.

The user wants to see at a glance which prompt came from which prompt and request, to go back to any
earlier prompt (also one taken from the node or edited by hand) and branch from it, and to see whether
a prompt in the node is a tweak of a version or something else.

## Goals

- The chat keeps its order (oldest first, newest at the bottom) with a graph lane on its left, like git
  history: a dot per version and lines from each version to the ones made from it.
- Version numbers stay a plain count in the order versions were made: v1, v2, v3. Edits made one after
  another get consecutive numbers.
- The version the next request edits and its path back to the start are highlighted; the rest is muted.
- A hand edit becomes a version of its own, so what the AI wrote stays as it was.
- The node's prompt that a write edits becomes a version, linked to the version it was tweaked from
  when it is close to one.
- Branches can be folded.
- Old chats show in the graph, and the node prompts they edited become versions.

## Non-goals

- A separate node-graph view (boxes and wires). It can come later on top of this.
- Deleting, moving or re-parenting versions.
- Linking versions across chats.
- Any change to what the LLM receives.

## Anti-goals

- A number that changes after it was shown. (Migrating an old chat renumbers it once, see below.)
- Losing text: a node prompt, a written version or a hand edit that was used.
- Clutter: an unchanged edit, or a node prompt that already is a version, must not add one.

## Approaches considered

1. Indented tree with full-path numbers (v1.7.2.3): long numbers and deep indents for edits made one
   after another, which is how the user mostly works. Rejected.
2. Indented tree with step.branch numbers (v3.2): the first of two siblings sat straight under its
   parent and the second was indented, or the whole subtree moved right when a branch appeared.
   Rejected as unintuitive.
3. One path at a time with `‹ 1/2 ›` switchers (like editing a message in ChatGPT): familiar, but other
   branches are hidden, and the user branches a lot. Rejected; its path idea is kept as the highlight.
4. **List in creation order with a graph lane (chosen).** Nothing moves when a version is added,
   numbers stay short, lineage is in the graph and in each card's "edit of v3".

## Design

### Versions

A version gains `kind`:

- `"written"` — written by the LLM, as every version is today. The default when `kind` is missing.
- `"hand"` — edited by hand. `baseId` is the version it was edited from; `request: ""`, no model, cost
  or usage. `editedIn: "node"` marks one that was edited in the node's prompt box rather than in the
  Prompt Writer.
- `"node"` — taken from a Prompt + LoRAs and not close to any version. A root (`baseId: null`),
  `request: ""`, and `source`: the label of the node it was read from.

A version also gains `sentAt`: when **Send to prompt** last wrote it into a node (null if never).

Versions are only ever appended to `chat.versions`, never removed or reordered. `n` (array position + 1)
is the shown number.

### Matching a text to the chat's versions

One pure function decides what a prompt text is to a chat, used by Edit current prompt and by the
migration:

1. **Same:** a version holds exactly this text → the newest such version.
2. **Close:** otherwise, the version whose words are closest; it counts as close when the word diff
   from it is not a rewrite (`diffStats(...).rewrite` is false: at most 60% of the words changed, the
   same test that shows "Mostly rewritten"). To keep this cheap, candidates are ranked by shared words
   and only the best one is diffed. Ties go to the newer version.
3. **Other:** nothing is close.

### Edit current prompt

When the request is sent, the node's text is read as today; empty gives the same error as today. Then:

- **same** → that version is the base;
- **close** → a hand version (`editedIn: "node"`) under the close version is appended, saved, and is the
  base;
- **other** → a node version is appended, saved, and is the base.

From there the request edits that version like any other. New steps no longer carry `basePrompt`. If the
write fails, is stopped, or the AI asks a question, the new version stays.

### Hand edits

The newest version stays open with an editable box, as today. When its text is saved (on blur, or
whenever edits are flushed) and differs from the version's text:

- A hand version that has not been **used** yet is changed in place. Used means: it was sent to a node
  (`sentAt`), or a write, a question or a failure starts from it.
- Any other version stays as it is, and a hand version with the new text is appended under it. If the
  composer's choice was the edited version, it moves to the new hand version.
- An edit that leaves the text empty or changes only whitespace is not saved as a new version; the box
  gets the version's text back.

Anything that acts on the open card flushes edits first and then acts on the version that holds the
text, which is the new hand version if the flush made one: **Send to prompt** and a LoRA suggestion's
**+**. The conversation is drawn again after the press, never between the press and the click, so the
click is not lost.

A LoRA suggestion's **+** keeps changing the version's first line in place (pressing again undoes it);
it is not a hand edit.

### Again

Only written versions have the Again arrow. It puts their request back with the version they edited, as
today. Hand and node versions have no request, so they have none. On the open card, Again first saves
an edit (which may make a hand version) and then puts back the written version's own request and base.
The note "the prompt in the node has changed since" goes: a base is now always a version.

### The graph

- The cards stay in creation order. A gutter on their left holds the graph: each version is a dot at the
  height of its card's first line, and a line runs from each version to every version made from it.
- Lanes: a version's first follow-up continues its lane; each further follow-up branches into a lane
  of its own. Lanes are assigned so that the fewest are used (a lane is reused once its line ended).
- Dots: filled for written, hollow for hand, a small square for node versions.
- Highlight: the composer's choice and its path back to its root are drawn in the accent colour, the
  rest muted.
- A write in progress, a question and a failure show as a dashed dot at the bottom, with a line from
  the version they edit, numbered with the number the version would get.
- The graph is drawn as one SVG over the gutter, from the cards' measured positions, and drawn again
  when a card changes size (open, close, typing, streaming, thinking, show changes).

### Card heads and rows

- Heads say where a version came from: "new prompt", "edit of v3", "hand edit of v3", "hand edit of v4,
  in the node", "from 🌀 Prompt + LoRAs" (the `source`).
- Closed rows show the request for written versions, "Edited by hand" or "Edited in the node" with the
  word changes for hand versions, and "Taken from the node" for node versions, plus where they came
  from.
- Hand and node versions have no tags, no request line and no thinking. Their changes line compares
  with their parent like any version.

### Folding

- A version that has others made from it gets a fold arrow in its head. Folding hides every version
  made from it, directly or further down; the version stays and shows "+N".
- What is folded is saved with the chat (`folded`: a list of version ids).
- A version written, answered or retried inside a folded part unfolds the path to it. Picking a hidden
  version in the start menu unfolds the path to it.
- The newest version may be folded away; then no card is open.

### The start menu

"What the next request does" lists versions newest first, as today: "Edit v9 (newest)", "Edit v5 (make
it winter)", "Edit v4 (edited by hand)", "Edit v8 (taken from the node)", then "Edit current prompt
(…)" and "New prompt". The pencil on a version still chooses it.

### Changes from the version before

Unchanged, except that a version's base is always its parent version: node versions and written roots
have no changes line. The quote "The current prompt it edited" goes.

### Chat titles

A chat is titled after its first **written** version's request, and the AI title is asked for while the
first written version is being written. A node or hand version made by the same send does not count.

### Migration of old chats (on load)

In `normalizeChat`, for each version in order, then the pending question, then the failure:

- If it has `basePrompt`, or `baseId: "seed"` with a `seedPrompt`, that text is matched against the
  versions before it: **same** → that version is its base; **close** → a hand version
  (`editedIn: "node"`) is inserted right before it; **other** → a node version (`source`: the chat's
  `targetLabel`) is inserted right before it. The inserted version's id is `<its id>~node` (`~pending` /
  `~failure` for those) and its `created` is the same. For the pending question and the failure, the
  inserted version goes at the end. `basePrompt` is then dropped.
- `baseId: "prompt"` without a `basePrompt`, `"seed"` without a seed prompt, and an id that names no
  earlier version become null (a root).

Inserting renumbers an old chat once. It is idempotent: a migrated chat has no `basePrompt` left, and
the ids are derived. The chats file stays `version: 1`. 5.0.0 reading a migrated file ignores `kind`,
`sentAt` and `folded` and shows node and hand versions as versions with an empty request; nothing is
lost.

### Docs and tests

- `web/prompt_chats.js`: kinds, `sentAt`, `folded`, text matching, the migration, the graph layout
  (lanes, edges, visible versions after folding), the path, start options and labels, as pure functions
  with unit tests in `tests/web/prompt_chats.test.mjs`.
- `web/prompt_writer.js`: the gutter and its SVG, the fold arrow, hand edits, Edit current prompt,
  Again only on written versions, titles.
- `web/docs/Prompt Writer.md` (Versions, Writing a request) and `CHANGELOG.md` under Unreleased.
- A Playwright check against the running ComfyUI with demo chats only, never the user's real chats.

## Left to the builder

Lane width and colours, the dot sizes, where exactly the fold arrow sits, the exact wording within the
lines above, and how the functions are split between `prompt_chats.js` and `prompt_writer.js`.
