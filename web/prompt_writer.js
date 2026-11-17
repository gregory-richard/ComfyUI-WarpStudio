// 🌀 Prompt Writer: a chat-style pop-up, opened from the ✨ AI Prompt Writer button above
// the prompt of each 🌀 Prompt + LoRAs, where an LLM writes and rewrites an image prompt.
//
// It exists only in the frontend, so it never takes part in a queued prompt.
// Every network call goes through the /warppipe/llm routes; the browser never
// sees an API key. Chats live in the ComfyUI user folder, never in a workflow.
//
// What a write depends on is module-level, not part of the pop-up: several
// chats can write at once, and all of them carry on after it is closed.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { loras as loraLibrary } from "./library.js";
import { connectedBase, connectedModelName } from "./model_base.js";
import {
  addTriggerLine,
  appendLoraTag,
  buildLoraLists,
  isLoraListed,
  listTargets,
  disableLoraTag,
  enableLoraTag,
  loraScopeCounts,
  targetOffLoraTags,
  modelLabel,
  modelPrice,
  removeLoraTag,
  removeTriggerLine,
  resolveSuggestions,
  REASONING_LEVELS,
  diffLines,
  diffStats,
  diffWords,
  reasoningLevel,
  resolveTarget,
  splitLines,
  targetLoraTags,
} from "./prompt_writer_core.js";
import {
  CHATS_FILE,
  CURRENT_PROMPT,
  KIND_HAND,
  KIND_NODE,
  KIND_WRITTEN,
  NEW_PROMPT,
  addVersion,
  adoptText,
  branchOf,
  canFold,
  clearFailure,
  createChat,
  deleteChat,
  deleteVersion,
  dropQuestion,
  editVersion,
  emptyStore,
  foldedCount,
  hasFollowUps,
  historyLayout,
  isStore,
  keepSystemPrompt,
  keptPaths,
  latestChatFor,
  markSent,
  mergedDraft,
  nextVersionNumber,
  normalizeStore,
  openQuestion,
  originLabel,
  pathTo,
  recordFailure,
  renameChat,
  replaceVersion,
  rowText,
  setAutoTitle,
  setVersionTitle,
  sortedChats,
  startOptions,
  startValue,
  systemPromptOf,
  toggleFold,
  unfoldTo,
  untitledVersions,
  visibleVersions,
} from "./prompt_chats.js";

const SETTING_DIR = "WarpPipe.PromptWriter.SystemPromptDir";
const SETTING_BASE_URL = "WarpPipe.PromptWriter.BaseUrl";
const SETTING_MODEL = "WarpPipe.PromptWriter.Model";
const SETTING_SYSTEM_PROMPT = "WarpPipe.PromptWriter.SystemPrompt";
const SETTING_TEMPERATURE = "WarpPipe.PromptWriter.Temperature";
const SETTING_TITLE_MODEL = "WarpPipe.PromptWriter.TitleModel";
const SETTING_LORA_SCOPE = "WarpPipe.PromptWriter.LoraScope";
const SETTING_REASONING = "WarpPipe.PromptWriter.ReasoningEffort";
const LORA_SCOPES = ["off", "list", "all"];
const DEFAULT_TITLE_MODEL = "gemma-4-26b-a4b-it-chimeraxheretic";

const DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1";
const DEFAULT_TEMPERATURE = 0.6;
const TEXT_WIDGET = "text";
const LORAS_WIDGET = "loras";
// The node's text boxes in the order they are drawn, the way prompt_ui.js finds them.
const BOX_WIDGETS = [TEXT_WIDGET, LORAS_WIDGET, "loras_2"];
const NOT_LISTED = " (not listed)";
const MAX_IMAGES = 8;
const FLASH_MS = 600;
const TICK_MS = 1000;
const TOAST_MS = 4000;
const LIVE_MS = 100;

// The composer's height, in lines of its CSS line-height plus padding and border.
const INPUT_LINE = 19;
const INPUT_CHROME = 19;
const INPUT_MIN_LINES = 3;
const INPUT_MAX_LINES = 10;

const PLACEHOLDER_WRITE = "Describe the prompt, or the change you want. Ctrl+Enter sends.";
const PLACEHOLDER_ANSWER = "Answer the question. Ctrl+Enter sends.";
const ATTACH_TITLE = "Add images (or drop or paste them here)";
const REWIND_TITLE = "Take this request back into the input";
const followedTitle = (n) =>
  `Versions were made from v${n}, so writing over it would change what they came from. Recreate writes a new version beside it instead.`;

// The AI Prompt Writer button floats just above the prompt box, in the middle of
// the node's last row of slots, where nothing is drawn: it takes no room of its own.
// Canvas pixels from the box's top edge to the button's bottom edge.
const WRITE_BUTTON_GAP = 12;
// Below this width (canvas pixels) the full name would run into the slot labels.
const WRITE_BUTTON_SHORT = 280;
// The height of a card's first line, which the graph's dots sit in the middle of.
const HEAD_LINE = 30;
// The side padding of the conversation and of the input, which line up.
const COMPOSER_PAD = 24;

const STYLE = `
/* Above the prompt box, never over its text, in the empty middle of the node's last
   row of slots. The row it spans lets every press through but the button's own, so
   the slots and the node still drag as before; the canvas scales it with the box. */
.wpw-open-bar {
  position: absolute; left: 0; right: 0; bottom: calc(100% + ${WRITE_BUTTON_GAP}px); z-index: 4; height: 20px;
  display: flex; justify-content: center; pointer-events: none; container-type: inline-size;
}
.wpw-open {
  position: relative; height: 20px; padding: 0 10px; cursor: pointer; white-space: nowrap; pointer-events: auto;
  font: 500 12px/20px var(--font-inter, Inter, Arial, sans-serif);
  color: #fff; background: var(--primary-background, #0b8ce9); border: 0; border-radius: 5px;
}
@container (max-width: ${WRITE_BUTTON_SHORT - 1}px) { .wpw-open-more { display: none; } }
.wpw-open:hover { background: var(--primary-background-hover, #31b9f4); }
.wpw-open.is-writing { color: #1a1a1a; background: #e8c44e; border-color: #e8c44e; }
.wpw-open.has-unseen::after {
  content: ""; position: absolute; top: -3px; right: -3px; width: 8px; height: 8px;
  border-radius: 50%; background: #5fd38a; box-shadow: 0 0 0 1px #1a1a1a;
}

/* The pop-up sits over ComfyUI's toasts otherwise, and they report from here. */
body:has(.wpw-backdrop) .p-toast { z-index: 1500 !important; }

.wpw-backdrop {
  position: fixed; inset: 0; z-index: 1400;
  background: rgba(0, 0, 0, 0.62);
  display: flex; align-items: center; justify-content: center;
}
.wpw-modal {
  --wpw-warp: var(--primary-background, #0b8ce9);
  --wpw-warp-hover: var(--primary-background-hover, #31b9f4);
  --wpw-raised: var(--secondary-background, #262729);
  --wpw-raised-hover: var(--secondary-background-hover, #313235);
  --wpw-yellow: #e8c44e;
  --wpw-green: #5fd38a;
  --wpw-red: #f07070;
  --wpw-ground: var(--comfy-menu-bg, #202020);
  --wpw-panel: var(--secondary-background, var(--comfy-input-bg, #262729));
  --wpw-ink: var(--base-foreground, var(--input-text, #fff));
  --wpw-rule: var(--border-default, var(--border-color, #494a50));
  --wpw-line: color-mix(in srgb, var(--wpw-rule) 55%, transparent);
  --wpw-muted: color-mix(in srgb, var(--wpw-ink) 58%, transparent);

  position: relative; width: min(1280px, 94vw); height: 88vh;
  display: grid; grid-template-columns: 248px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr);
  grid-template-areas: "rail main";
  background: var(--wpw-ground); color: var(--wpw-ink); color-scheme: dark;
  border: 1px solid var(--wpw-rule); border-radius: 10px; outline: none;
  overflow: hidden; box-shadow: 0 24px 70px rgba(0, 0, 0, 0.55);
  font: 400 14px/1.45 var(--font-inter, Inter, Arial, sans-serif);
}
.wpw-modal button, .wpw-modal input, .wpw-modal select, .wpw-modal textarea { font: inherit; color: inherit; }
.wpw-modal :focus-visible { outline: 2px solid var(--wpw-warp); outline-offset: 1px; }
/* Parts given a display of their own still hide while they have nothing to show. */
.wpw-modal [hidden] { display: none !important; }
.wpw-modal .pi { font-size: inherit; line-height: 1; }
.wpw-spacer { flex: 1; }

.wpw-icon {
  flex: none; width: 30px; height: 30px; padding: 0; background: none; cursor: pointer; font-size: 14px;
  border: 0; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; color: var(--wpw-muted);
}
.wpw-icon:hover { background: var(--wpw-raised-hover); color: var(--wpw-ink); }
.wpw-btn {
  height: 30px; padding: 0 12px; background: var(--wpw-raised); cursor: pointer; white-space: nowrap;
  border: 0; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
}
.wpw-btn .pi { font-size: 12px; }
.wpw-btn:hover { background: var(--wpw-raised-hover); }
.wpw-btn:disabled { opacity: 0.45; cursor: default; }
.wpw-primary { background: var(--wpw-warp); color: #fff !important; }
.wpw-primary:hover { background: var(--wpw-warp-hover); }
.wpw-field {
  height: 30px; box-sizing: border-box; background: var(--wpw-ground);
  border: 1px solid var(--wpw-rule); border-radius: 6px; padding: 0 8px;
}
.wpw-danger { color: var(--wpw-red); }

/* The rail: the app, its settings, and the chats. */
.wpw-rail {
  grid-area: rail; display: flex; flex-direction: column; min-height: 0;
  border-right: 1px solid var(--wpw-line); background: var(--wpw-panel);
}
.wpw-rail-head { display: flex; align-items: center; gap: 6px; padding: 12px 10px 6px 16px; }
.wpw-brand { flex: 1; display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; }
.wpw-brand .pi { color: var(--wpw-warp); }
.wpw-new { margin: 6px 10px 10px; flex: none; background: none; border: 1px solid var(--wpw-line); }
.wpw-new:hover { background: var(--wpw-raised-hover); }
.wpw-chats { flex: 1; min-height: 0; overflow-y: auto; padding: 0 8px 10px; }
.wpw-chat { position: relative; border-radius: 7px; }
.wpw-chat + .wpw-chat { margin-top: 1px; }
.wpw-chat:hover { background: rgba(255, 255, 255, 0.04); }
.wpw-chat.is-open { background: color-mix(in srgb, var(--wpw-warp) 16%, transparent); }
.wpw-chat-open {
  display: block; width: 100%; padding: 8px 30px 8px 10px; background: none; border: 0;
  text-align: left; cursor: pointer;
}
.wpw-chat-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; }
.wpw-chat-title span:first-child { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wpw-check { flex: none; color: var(--wpw-green); font-size: 11px; }
.wpw-chat-sub {
  display: flex; gap: 10px; margin-top: 1px; font-size: 11.5px; color: var(--wpw-muted);
  white-space: nowrap; overflow: hidden;
}
.wpw-chat-sub.is-writing, .wpw-chat-sub.is-question { color: var(--wpw-yellow); }
.wpw-chat-sub.is-failed { color: var(--wpw-red); }
.wpw-chat-more {
  position: absolute; top: 7px; right: 4px; width: 22px; height: 22px; padding: 0; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  background: none; border: 0; border-radius: 5px; font-size: 12px; color: var(--wpw-muted); opacity: 0;
}
.wpw-chat:hover .wpw-chat-more, .wpw-chat.is-open .wpw-chat-more, .wpw-chat-more:focus-visible { opacity: 1; }
.wpw-chat-more:hover { background: var(--wpw-raised-hover); color: var(--wpw-ink); }
.wpw-rename-input {
  width: 100%; height: 30px; box-sizing: border-box; padding: 0 8px;
  background: var(--wpw-ground); border: 1px solid var(--wpw-warp); border-radius: 6px;
}
.wpw-confirm { padding: 8px 10px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.wpw-confirm span { flex: 1 1 100%; font-size: 12px; }

/* Pop-overs: the settings and the chat menu. */
.wpw-pop {
  position: absolute; z-index: 6; box-sizing: border-box;
  background: var(--wpw-ground); border: 1px solid var(--wpw-rule); border-radius: 9px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5); padding: 6px;
}
.wpw-chat-menu { position: fixed; width: 130px; display: flex; flex-direction: column; }
.wpw-menu-item {
  height: 30px; padding: 0 8px; background: none; border: 0; border-radius: 5px;
  text-align: left; cursor: pointer;
}
.wpw-menu-item:hover { background: var(--wpw-raised-hover); }
.wpw-menu-item.is-danger { color: var(--wpw-red); }
.wpw-settings {
  top: 48px; left: 12px; width: 420px; max-width: calc(100% - 24px); max-height: calc(100% - 60px);
  overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px;
}
.wpw-settings-title { font-size: 14px; font-weight: 600; margin-bottom: 2px; }
.wpw-set { display: grid; grid-template-columns: 104px minmax(0, 1fr); align-items: center; gap: 10px; font-size: 13px; }
.wpw-set-name { color: var(--wpw-muted); font-size: 12px; }
.wpw-set-control { display: flex; align-items: center; gap: 6px; min-width: 0; }
.wpw-set-control > .wpw-field { flex: 1; min-width: 0; }
.wpw-set-group { margin-top: 4px; padding-top: 12px; border-top: 1px solid var(--wpw-line); }
.wpw-combo {
  display: flex; align-items: center; gap: 8px; cursor: pointer; text-align: left; white-space: nowrap; overflow: hidden;
}
.wpw-combo.is-error { color: var(--wpw-red); }
.wpw-combo:hover, .wpw-prompts:hover { border-color: var(--wpw-warp); }
.wpw-combo .pi-chevron-down { font-size: 10px; color: var(--wpw-muted); }
.wpw-prompts { cursor: pointer; }
.wpw-prompts option { background: var(--wpw-ground); color: var(--wpw-ink); }
.wpw-models { display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px solid var(--wpw-line); border-radius: 8px; }
.wpw-filter { width: 100%; }
.wpw-options { max-height: 38vh; overflow-y: auto; }
.wpw-option {
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 5px 8px; background: none; border: 0;
  border-radius: 5px; text-align: left; cursor: pointer;
}
.wpw-option:hover { background: color-mix(in srgb, var(--wpw-warp) 14%, transparent); }
.wpw-option.is-current { color: var(--wpw-warp); }
.wpw-option-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wpw-option-price { flex: none; font-size: 11.5px; color: var(--wpw-muted); font-variant-numeric: tabular-nums; }
.wpw-option .pi-eye { flex: none; font-size: 11px; color: var(--wpw-muted); }
.wpw-none { padding: 8px; color: var(--wpw-muted); }
.wpw-peek-text {
  max-height: 34vh; overflow-y: auto; padding: 10px 12px; white-space: pre-wrap; overflow-wrap: anywhere;
  font-size: 12.5px; line-height: 1.55; user-select: text; background: var(--wpw-panel); border-radius: 8px;
}
.wpw-key-state { font-size: 12px; color: var(--wpw-muted); }
.wpw-key-state.is-set { color: var(--wpw-green); }

.wpw-spin {
  display: inline-block; flex: none; width: 9px; height: 9px; margin-right: 6px; vertical-align: -1px;
  border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%;
  animation: wpw-turn 0.8s linear infinite;
}
@keyframes wpw-turn { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .wpw-spin { animation-duration: 2.4s; } }

/* The chat: its title and node on top, then a reading column of cards and the input. */
.wpw-main { grid-area: main; display: flex; flex-direction: column; min-height: 0; }
.wpw-chat-head {
  display: flex; align-items: center; gap: 12px; padding: 10px 12px 10px 24px;
  border-bottom: 1px solid var(--wpw-line);
}
.wpw-title, .wpw-title-input { flex: 0 1 auto; min-width: 0; max-width: 60%; font-size: 16px; font-weight: 600; }
.wpw-title {
  background: none; border: 1px solid transparent; border-radius: 6px; padding: 3px 8px; margin-left: -9px;
  cursor: text; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wpw-title:hover { border-color: var(--wpw-line); }
.wpw-title-input { flex: 1 1 auto; background: var(--wpw-panel); border: 1px solid var(--wpw-warp); border-radius: 6px; padding: 3px 8px; }
.wpw-for { flex: none; display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--wpw-muted); }
.wpw-modal .wpw-target {
  height: 30px; box-sizing: border-box; min-width: 120px; max-width: 240px; padding: 0 8px; cursor: pointer;
  font-size: 13px; font-weight: 500; color: var(--wpw-ink); background: var(--wpw-panel);
  border: 1px solid color-mix(in srgb, var(--wpw-warp) 65%, transparent); border-radius: 6px;
}
.wpw-modal .wpw-target:hover { border-color: var(--wpw-warp); }
.wpw-modal .wpw-target:disabled { opacity: 0.6; cursor: default; }
.wpw-target option { background: var(--wpw-ground); color: var(--wpw-ink); }

.wpw-convo { flex: 1; min-height: 0; overflow-y: auto; padding: 22px 24px 28px; display: flex; flex-direction: column; gap: 14px; }
.wpw-convo > * { width: 100%; max-width: 780px; margin-inline: auto; box-sizing: border-box; flex: none; }
.wpw-convo > .wpw-hint { margin: auto; max-width: 420px; text-align: center; color: var(--wpw-muted); }
.wpw-hint.is-error { color: var(--wpw-red); }

/* The versions in the order they were made, with a graph on their left of what came from
   what. A closed version is a light row and only an open one is a card, so the history
   reads as a list and the open card is what stands out. */
.wpw-history { position: relative; display: flex; flex-direction: column; gap: 2px; }
.wpw-graph { position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }
.wpw-edge { fill: none; stroke: var(--wpw-muted); stroke-width: 1.5; opacity: 0.55; }
.wpw-edge.is-path { stroke: var(--wpw-warp); stroke-width: 2; opacity: 1; }
.wpw-edge.is-next { stroke: var(--wpw-yellow); stroke-dasharray: 3 3; opacity: 0.8; }
.wpw-node { fill: var(--wpw-muted); stroke: var(--wpw-muted); stroke-width: 1.5; }
.wpw-node.is-path { fill: var(--wpw-warp); stroke: var(--wpw-warp); }
.wpw-node.is-hand { fill: var(--wpw-ground); stroke-width: 2; }
.wpw-node.is-next { fill: var(--wpw-ground); stroke: var(--wpw-yellow); stroke-dasharray: 2 2; }
/* A version's dot is a button: it makes the version the input of the next request. The
   chosen one has a ring; pointing at another shows where its ring would be. */
.wpw-graph .wpw-pick { pointer-events: auto; cursor: pointer; }
.wpw-graph .wpw-hit { fill: transparent; pointer-events: all; }
.wpw-ring { fill: none; stroke: transparent; stroke-width: 1.5; }
.wpw-pick:hover .wpw-ring { stroke: color-mix(in srgb, var(--wpw-warp) 55%, transparent); }
.wpw-pick.is-chosen .wpw-ring { stroke: var(--wpw-warp); }
/* Fold controls sit in the graph's margin, beside the dot of the version they fold. */
.wpw-modal .wpw-fold {
  position: absolute; left: 0; top: 0; z-index: 1; width: 16px; height: 16px; padding: 0;
  border: 0; border-radius: 4px; background: transparent; opacity: 0.55;
  color: var(--wpw-muted); cursor: pointer; font-size: 9px;
  display: flex; align-items: center; justify-content: center;
}
.wpw-modal .wpw-fold:hover { opacity: 1; color: var(--wpw-ink); background: var(--wpw-raised-hover); }

.wpw-card { border: 1px solid transparent; border-radius: 8px; padding: 3px 10px; }
.wpw-card:not(.is-open):hover { background: color-mix(in srgb, var(--wpw-ink) 4%, transparent); }
/* Open, a card grows only downwards: its first line stays exactly where it was. */
.wpw-card.is-open {
  margin-bottom: 6px; padding: 3px 10px 12px; background: var(--wpw-panel); border-color: var(--wpw-line);
}
.wpw-card.is-writing, .wpw-card.is-question { border-color: color-mix(in srgb, var(--wpw-yellow) 45%, transparent); }
.wpw-card.is-error { border-color: color-mix(in srgb, var(--wpw-red) 50%, transparent); }
/* One line of one height, closed or open: the number, the title, the menu. */
.wpw-card-head { display: flex; align-items: center; gap: 10px; height: ${HEAD_LINE}px; line-height: ${HEAD_LINE}px; }
.wpw-card-head.is-toggle { cursor: pointer; }
.wpw-ver { flex: none; min-width: 20px; font-size: 12.5px; font-weight: 600; font-variant-numeric: tabular-nums; }
.wpw-fold-count { flex: none; font-size: 11.5px; color: var(--wpw-muted); }
.wpw-head-text {
  flex: 1; min-width: 0; color: var(--wpw-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wpw-card.is-open .wpw-head-text { color: var(--wpw-ink); font-weight: 500; }
.wpw-card .wpw-head-text.is-empty { font-style: italic; font-weight: 400; color: var(--wpw-muted); }
/* A version's ⋮: always there on the open card, on a closed one while pointed at. */
.wpw-modal .wpw-card-menu { width: 26px; height: 26px; }
.wpw-card:not(.is-open):not(:hover) .wpw-card-menu:not(.is-active):not(:focus-visible) { opacity: 0; }
.wpw-modal .wpw-card-menu.is-active { background: var(--wpw-raised-hover); color: var(--wpw-ink); }

/* The menu of a version. Each line says what it does, so Retry and Recreate read apart. */
.wpw-vmenu { position: fixed; width: 280px; display: flex; flex-direction: column; padding: 5px; }
.wpw-vmenu-item {
  display: flex; align-items: flex-start; gap: 10px; width: 100%; padding: 6px 8px; text-align: left;
  background: none; border: 0; border-radius: 6px; cursor: pointer;
}
.wpw-vmenu-item .pi { flex: none; width: 14px; margin-top: 3px; font-size: 12px; color: var(--wpw-muted); }
.wpw-vmenu-item:hover:not(:disabled) { background: var(--wpw-raised-hover); }
.wpw-vmenu-item:disabled { cursor: default; opacity: 0.5; }
.wpw-vmenu-item.is-danger, .wpw-vmenu-item.is-danger .pi { color: var(--wpw-red); }
.wpw-vmenu-words { display: flex; flex-direction: column; min-width: 0; line-height: 1.35; }
.wpw-vmenu-label { font-size: 13px; }
.wpw-vmenu-sub { font-size: 11.5px; color: var(--wpw-muted); }
.wpw-vmenu-rule { height: 1px; margin: 4px 6px; background: var(--wpw-line); }
.wpw-vmenu-confirm { padding: 8px 8px 10px; font-size: 12.5px; line-height: 1.45; }
.wpw-vmenu-buttons { display: flex; justify-content: flex-end; gap: 6px; padding: 0 4px 4px; }

/* Generation info: everything about how a version came to be, over the conversation. */
.wpw-info-shade {
  position: absolute; inset: 0; z-index: 7; padding: 28px;
  display: flex; align-items: center; justify-content: center; background: rgba(0, 0, 0, 0.45);
}
.wpw-info {
  width: min(720px, 100%); max-height: 100%; display: flex; flex-direction: column; outline: none;
  background: var(--wpw-ground); border: 1px solid var(--wpw-rule); border-radius: 10px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
}
.wpw-info-head { display: flex; align-items: center; gap: 10px; padding: 10px 10px 10px 18px; border-bottom: 1px solid var(--wpw-line); }
.wpw-info-title { flex: 1; min-width: 0; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wpw-info-body { overflow-y: auto; padding: 14px 18px 18px; display: flex; flex-direction: column; gap: 16px; }
.wpw-info-grid { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 18px; margin: 0; font-size: 12.5px; }
.wpw-info-grid dt { color: var(--wpw-muted); }
.wpw-info-grid dd { margin: 0; overflow-wrap: anywhere; user-select: text; }
.wpw-info-name { margin: 0 0 6px; font-size: 12px; font-weight: 500; color: var(--wpw-muted); }
.wpw-info-text {
  max-height: 220px; overflow-y: auto; padding: 8px 10px; border-radius: 6px; background: var(--wpw-panel);
  font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text;
}
.wpw-info-text.is-note { padding: 0; background: none; font-style: italic; color: var(--wpw-muted); }
.wpw-info-part .wpw-turns { margin-top: 8px; }
.wpw-status {
  flex: none; display: flex; align-items: center; gap: 8px; height: ${HEAD_LINE}px;
  font-size: 12px; color: var(--wpw-muted); white-space: nowrap; font-variant-numeric: tabular-nums;
}
.wpw-card.is-writing .wpw-status, .wpw-card.is-question .wpw-status { color: var(--wpw-yellow); }
.wpw-card.is-error .wpw-status { color: var(--wpw-red); }
/* Puts the request back into the input. On a version only while pointed at. */
.wpw-modal .wpw-rewind {
  flex: none; width: 26px; height: 26px; margin-top: 2px; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: 0; border-radius: 6px; background: transparent; color: var(--wpw-muted);
  font-size: 12px; cursor: pointer; opacity: 0;
}
.wpw-card:hover .wpw-rewind, .wpw-modal .wpw-rewind:focus-visible, .wpw-modal .wpw-rewind.is-shown { opacity: 1; }
.wpw-modal .wpw-rewind:hover { background: var(--wpw-raised-hover); color: var(--wpw-ink); }

/* The images a request sent and the questions on the way, quiet under the request. */
.wpw-turns { margin: 0 0 4px; font-size: 12.5px; }
.wpw-said-grid { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 3px 10px; line-height: 19px; }
.wpw-who { font-size: 11px; color: var(--wpw-muted); }
.wpw-said { white-space: pre-wrap; overflow-wrap: anywhere; }
.wpw-who.is-question, .wpw-said.is-question { color: var(--wpw-yellow); }
.wpw-said.is-empty { font-style: italic; color: var(--wpw-muted); }
/* Your request on an open version: quiet, with a rule so it does not read as the prompt. */
.wpw-request { padding: 2px 0 2px 10px; border-left: 2px solid var(--wpw-line); font-size: 13px; }
.wpw-request .wpw-thumbs { margin: 6px 0 2px; }
/* Recreate: the request in a field of its own, in the place where it was shown. */
.wpw-modal .wpw-recreate-input {
  display: block; width: 100%; box-sizing: border-box; margin: 0; padding: 6px 10px; resize: none;
  font-size: 13px; line-height: 19px; background: var(--wpw-ground); border-radius: 6px; outline: none;
  border: 1px solid color-mix(in srgb, var(--wpw-warp) 70%, transparent);
}
.wpw-recreate-row { display: flex; justify-content: flex-end; gap: 6px; margin: 8px 0 2px; }
.wpw-thumbs { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 6px; }
.wpw-thumbs img { width: 64px; height: 64px; object-fit: cover; border-radius: 6px; display: block; }
.wpw-hint-line { margin-top: 8px; font-size: 12px; color: var(--wpw-muted); }

/* Above the prompt, one row of switches: the changes and the LoRAs. An open switch shows
   its part below the row, so the switch itself never moves. */
.wpw-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 0 16px; margin: 0 0 2px; font-size: 12px; color: var(--wpw-muted); }
.wpw-modal .wpw-toggle {
  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0;
  border: 0; background: none; color: var(--wpw-muted); cursor: pointer; font-size: 12px;
}
.wpw-modal .wpw-toggle:hover, .wpw-modal .wpw-toggle.is-open { color: var(--wpw-ink); }
.wpw-modal .wpw-toggle .pi { font-size: 8px; transition: transform 0.12s ease; }
.wpw-modal .wpw-toggle.is-open .pi { transform: rotate(90deg); }
@media (prefers-reduced-motion: reduce) { .wpw-modal .wpw-toggle .pi { transition: none; } }
.wpw-toggle-count { color: var(--wpw-muted); font-variant-numeric: tabular-nums; }
.wpw-toggle-count.is-new { color: var(--wpw-ink); }
.wpw-meta-note { line-height: 26px; }
.wpw-panel { margin: 4px 0 6px; }
/* The one button under the prompt, naming where it writes; a long group name is cut short. */
.wpw-send-to { max-width: 100%; }
.wpw-send-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.wpw-diff-added { color: var(--wpw-green); font-variant-numeric: tabular-nums; }
.wpw-diff-removed { color: var(--wpw-red); font-variant-numeric: tabular-nums; }
.wpw-diff, .wpw-diff-col {
  background: var(--wpw-ground); border-radius: 8px; padding: 10px 12px; max-height: 60vh; overflow-y: auto;
  white-space: pre-wrap; overflow-wrap: anywhere; user-select: text;
}
.wpw-diff { margin-top: 6px; padding-left: 14px; font-size: 13.5px; line-height: 1.6; }
/* The new text reads as it is; a changed line has its old self small above it. */
.wpw-diff-row { min-height: 1lh; margin-left: -14px; padding-left: 12px; border-left: 2px solid transparent; }
.wpw-diff-row.is-changed { border-left-color: color-mix(in srgb, var(--wpw-warp) 70%, transparent); }
.wpw-diff-row.is-added { border-left-color: color-mix(in srgb, var(--wpw-green) 75%, transparent); }
.wpw-diff-row.is-removed {
  border-left-color: color-mix(in srgb, var(--wpw-red) 60%, transparent);
  font-size: 12px; line-height: 1.5; color: color-mix(in srgb, var(--wpw-red) 80%, var(--wpw-ink));
}
/* A line with something replaced keeps room above each line it wraps to, for what was there. */
.wpw-diff-row.has-old { line-height: 2.35; padding-top: 5px; }
.wpw-diff-add { background: color-mix(in srgb, var(--wpw-green) 20%, transparent); border-radius: 3px; }
.wpw-diff-change { position: relative; }
.wpw-diff-old {
  position: absolute; left: 0; bottom: 100%; max-width: 32ch; overflow: hidden; text-overflow: ellipsis;
  font-size: 10.5px; line-height: 1.2; white-space: nowrap; pointer-events: none;
  color: color-mix(in srgb, var(--wpw-red) 80%, var(--wpw-ink));
}
/* Near the right edge it hangs to the left instead, so it stays in view. */
.wpw-diff-old.is-flipped { left: auto; right: 0; }
/* Where something was only taken out: a thin mark, with what went small above it. */
.wpw-diff-mark {
  display: inline-block; width: 2px; height: 1.05em; margin: 0 2px; vertical-align: -0.15em;
  border-radius: 1px; background: color-mix(in srgb, var(--wpw-red) 75%, transparent);
}
.wpw-diff-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; margin-top: 6px; }
.wpw-diff-col { font-size: 13px; line-height: 1.55; }
.wpw-diff-col-name { margin-bottom: 6px; font-size: 11px; color: var(--wpw-muted); white-space: normal; }
/* What the model thought: quiet, scrolled to its newest line while it comes. */
.wpw-thinking-text {
  margin-top: 6px; max-height: 180px; overflow-y: auto; padding: 8px 10px; border-radius: 6px;
  background: var(--wpw-ground); color: var(--wpw-muted); font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; overflow-wrap: anywhere; user-select: text;
}
.wpw-text.is-live { color: color-mix(in srgb, var(--wpw-ink) 80%, transparent); }

/* The prompt itself, set for reading: the one thing on a card with a field of its own. */
.wpw-modal .wpw-editor, .wpw-text { font-size: 13.5px; line-height: 1.6; }
.wpw-modal .wpw-editor, .wpw-text {
  display: block; width: 100%; box-sizing: border-box; margin-top: 4px; padding: 10px 12px;
  background: var(--wpw-ground); border: 1px solid transparent; border-radius: 8px;
}
.wpw-modal .wpw-editor { resize: none; }
.wpw-modal .wpw-editor:hover { border-color: var(--wpw-line); }
.wpw-modal .wpw-editor:focus { border-color: color-mix(in srgb, var(--wpw-warp) 70%, transparent); outline: none; }
.wpw-text { white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.wpw-actions { display: flex; align-items: center; justify-content: flex-end; gap: 2px; flex: none; }
.wpw-actions.is-block { margin-top: 10px; flex-wrap: wrap; }
.wpw-vmenu-buttons .wpw-danger { background: color-mix(in srgb, var(--wpw-red) 22%, var(--wpw-raised)); color: var(--wpw-red); }
.wpw-vmenu-buttons .wpw-danger:hover { background: color-mix(in srgb, var(--wpw-red) 32%, var(--wpw-raised)); }
.wpw-error-text { margin-top: 10px; color: var(--wpw-red); white-space: pre-wrap; overflow-wrap: anywhere; }

.wpw-suggest + .wpw-suggest { margin-top: 10px; }
.wpw-suggest-label { font-size: 12px; color: var(--wpw-muted); margin-bottom: 6px; }
.wpw-suggest-row { display: flex; flex-wrap: wrap; gap: 6px; }
/* A fixed box, and + and the check share one size: pressing moves nothing. */
.wpw-lora {
  display: flex; align-items: center; gap: 8px; box-sizing: border-box;
  width: 290px; max-width: 100%; height: 48px; padding: 4px 6px 4px 4px;
  background: var(--wpw-ground); border: 1px solid var(--wpw-line); border-radius: 8px;
}
.wpw-lora.is-added { border-color: color-mix(in srgb, var(--wpw-warp) 70%, transparent); }
.wpw-lora-thumb {
  flex: none; display: block; width: 38px; height: 38px; object-fit: cover;
  border-radius: 5px; background: var(--wpw-raised);
}
.wpw-lora-body { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.3; }
.wpw-lora-top { display: flex; align-items: baseline; gap: 6px; font-size: 12px; font-weight: 500; }
.wpw-lora-title { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wpw-lora-strength { flex: none; font-size: 11px; font-weight: 400; color: var(--wpw-muted); font-variant-numeric: tabular-nums; }
.wpw-lora-reason { font-size: 11px; color: var(--wpw-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wpw-lora-add {
  flex: none; width: 26px; height: 26px; padding: 0; cursor: pointer; font-size: 11px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--wpw-raised); border: 0; border-radius: 6px;
}
.wpw-lora-add:hover { background: var(--wpw-raised-hover); }
.wpw-lora.is-added .wpw-lora-add { background: var(--wpw-warp); color: #fff; }
.wpw-lora.is-added .wpw-lora-add:hover { background: var(--wpw-warp-hover); }
.wpw-lora-add:disabled { cursor: default; opacity: 0.6; }
/* What is on already takes little room, so what is new stands out. */
.wpw-lora.is-compact { width: auto; max-width: 260px; height: 30px; padding: 2px 4px 2px 2px; gap: 6px; }
.wpw-lora.is-compact .wpw-lora-thumb { width: 24px; height: 24px; }
.wpw-lora.is-compact .wpw-lora-add { width: 22px; height: 22px; font-size: 10px; }

/* The input: one box with the settings of the next request on top, its images, its text,
   and send in the corner. Its edges line up with the cards above (alignComposer). */
.wpw-composer { flex: none; padding: 4px ${COMPOSER_PAD}px 18px; }
.wpw-composer > * { max-width: 780px; margin-inline: auto; }
.wpw-box { position: relative; border: 1px solid var(--wpw-rule); border-radius: 12px; background: var(--wpw-panel); }
.wpw-box:focus-within { border-color: color-mix(in srgb, var(--wpw-warp) 75%, transparent); }
.wpw-composer.is-drag .wpw-box { border-color: var(--wpw-warp); box-shadow: 0 0 0 3px color-mix(in srgb, var(--wpw-warp) 25%, transparent); }
.wpw-attached { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 12px 0; }
.wpw-attached:empty { display: none; }
.wpw-input {
  display: block; width: 100%; box-sizing: border-box; padding: 11px 56px 10px 14px; resize: none;
  line-height: ${INPUT_LINE}px; background: transparent; border: 0;
}
.wpw-modal .wpw-input:focus-visible { outline: none; }
.wpw-tools {
  display: flex; align-items: center; gap: 6px; padding: 8px 8px 7px;
  border-bottom: 1px solid var(--wpw-line);
}
.wpw-tools-left { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.wpw-tool {
  flex: none; width: 28px; height: 28px; padding: 0; border: 0; border-radius: 6px; background: none;
  display: inline-flex; align-items: center; justify-content: center; cursor: pointer; font-size: 14px; color: var(--wpw-muted);
}
.wpw-tool:hover { background: var(--wpw-raised-hover); color: var(--wpw-ink); }
.wpw-chip {
  display: inline-flex; align-items: center; gap: 6px; height: 26px; box-sizing: border-box; padding: 0 8px;
  background: var(--wpw-ground); border: 1px solid var(--wpw-line); border-radius: 13px; font-size: 12px;
}
.wpw-chip img { width: 20px; height: 20px; object-fit: cover; border-radius: 4px; display: block; }
.wpw-chip.is-base { border-color: color-mix(in srgb, var(--wpw-warp) 60%, transparent); color: var(--wpw-warp); }
.wpw-chip-x { background: none; border: 0; padding: 0 1px; cursor: pointer; font-size: 14px; line-height: 1; color: var(--wpw-muted); }
.wpw-chip-x:hover { color: var(--wpw-red); }
.wpw-start-chip { gap: 6px; padding: 0 4px 0 10px; font-size: 11px; }
/* Use as input changes this choice: it lights up so you see where it went. */
.wpw-start-chip.is-flash { animation: wpw-flash 1.1s ease-out; }
@keyframes wpw-flash {
  0%, 35% {
    background: color-mix(in srgb, var(--wpw-warp) 30%, var(--wpw-ground));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--wpw-warp) 40%, transparent);
  }
  100% { box-shadow: 0 0 0 3px transparent; }
}
.wpw-modal .wpw-start {
  height: 22px; max-width: 280px; padding: 0 2px; border: 0; background: transparent;
  color: var(--wpw-warp); font-size: 12px; font-weight: 500; cursor: pointer;
}
.wpw-start option { background: var(--wpw-ground); color: var(--wpw-ink); }
.wpw-seg {
  display: inline-flex; align-items: center; height: 26px; overflow: hidden;
  border: 1px solid var(--wpw-line); border-radius: 13px; font-size: 12px;
}
.wpw-seg-label { padding: 0 8px 0 10px; color: var(--wpw-muted); white-space: nowrap; }
.wpw-seg-btn { height: 100%; padding: 0 9px; background: none; border: 0; cursor: pointer; white-space: nowrap; }
.wpw-seg-btn:hover { background: var(--wpw-raised-hover); }
/* A quiet fill: the send button is the one solid colour in the input. */
.wpw-seg-btn.is-on { background: color-mix(in srgb, var(--wpw-ink) 16%, transparent); color: var(--wpw-ink); }
.wpw-seg-count { margin-left: 5px; opacity: 0.6; font-variant-numeric: tabular-nums; }
.wpw-seg-btn.is-on .wpw-seg-count { opacity: 0.85; }
/* Round and the same size whatever it does: write, answer or stop. */
.wpw-send {
  position: absolute; right: 10px; bottom: 10px;
  width: 32px; height: 32px; padding: 0; border-radius: 50%; font-size: 13px;
  display: inline-flex; align-items: center; justify-content: center;
}
.wpw-send.is-stop { background: var(--wpw-yellow); color: #1a1a1a !important; }
.wpw-note { font-size: 12px; margin-top: 6px; padding-left: 4px; color: var(--wpw-muted); }
.wpw-note.is-error { color: var(--wpw-red); }
.wpw-note:empty { display: none; }
`;

// --- State that outlives the pop-up -------------------------------------------

let store = null;
let storeLoad = null;
let saving = false;
let saveAgain = false;
let ticker = null;
let listsRun = 0;
let loraRun = 0;

/** chat id → the running job. */
const jobs = new Map();
/** chat id → the node it was written from, until the chat is opened. */
const unseen = new Map();
/** chat id → the unsent composer. */
const drafts = new Map();
/** Prompt + LoRAs node → its AI Prompt Writer button, and the band it sits in. */
const buttons = new WeakMap();
const bars = new WeakMap();

/** Settings changed from here, readable at once however the frontend stores them. */
const written = new Map();

const lists = { models: [], prompts: [], loading: false, error: "" };

/** The open pop-up: its elements and what it is showing. Null while closed. */
let view = null;

// --- Small helpers ------------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child != null && child !== false));
  return node;
}

function button(text, onClick, className = "wpw-btn", title) {
  return el("button", { type: "button", class: className, text, title, onClick });
}

/** One of ComfyUI's own PrimeIcons. */
function icon(name) {
  return el("i", { class: `pi ${name}`, "aria-hidden": "true" });
}

/** A button that is only an icon; the title says what it does. */
function iconButton(name, onClick, className, title) {
  return el(
    "button",
    { type: "button", class: className, title, "aria-label": title, onClick },
    icon(name)
  );
}

/** A button with an icon before its words. */
function labelledButton(name, text, onClick, className = "wpw-btn", title) {
  return el("button", { type: "button", class: className, title, onClick }, icon(name), text);
}

function uid() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function ensureStyle() {
  if (document.getElementById("wpw-style")) return;
  document.head.appendChild(el("style", { id: "wpw-style", textContent: STYLE }));
}

function getSetting(id, fallback) {
  if (written.has(id)) return written.get(id);
  const value = app.extensionManager?.setting?.get?.(id) ?? app.ui?.settings?.getSettingValue?.(id);
  return value ?? fallback;
}

function setSetting(id, value) {
  written.set(id, value);
  const manager = app.extensionManager?.setting;
  const done = manager?.set
    ? manager.set(id, value)
    : app.ui?.settings?.setSettingValue?.(id, value);
  Promise.resolve(done).catch((error) => console.error("[WarpPipe] setting", id, error));
}

function baseUrl() {
  return String(getSetting(SETTING_BASE_URL, "")).trim() || DEFAULT_BASE_URL;
}

function clampTemperature(value) {
  const number = Number(value);
  if (value === "" || value == null || !Number.isFinite(number)) return DEFAULT_TEMPERATURE;
  return Math.min(2, Math.max(0, number));
}

function toast(severity, summary, detail, life = TOAST_MS) {
  app.extensionManager?.toast?.add?.({ severity, summary, detail, life });
}

function redraw() {
  app.canvas?.setDirty?.(true, true);
  app.graph?.setDirtyCanvas?.(true, true);
}

/** Records the current graph as one undo step, where the frontend allows it. */
function checkpoint() {
  const tracker =
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker ??
    app.workflowManager?.activeWorkflow?.changeTracker;
  tracker?.checkState?.();
}

function flash(node) {
  const before = node.boxcolor;
  node.boxcolor = "#6f6";
  redraw();
  setTimeout(() => {
    node.boxcolor = before;
    redraw();
  }, FLASH_MS);
}

async function getJson(route, options) {
  return readJson(await api.fetchApi(route, options));
}

async function readJson(response) {
  let body;
  try {
    body = await response.json();
  } catch {
    return { error: `HTTP ${response.status}: no JSON answer` };
  }
  if (!response.ok && !body?.error) return { error: `HTTP ${response.status}` };
  return body ?? { error: "empty answer" };
}

function postJson(route, payload, signal) {
  return getJson(route, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

/**
 * A write, read as it streams: each {"type": "delta"} line goes to `onPiece`, and
 * the final {"type": "done"} line is what this returns. A server that answers in
 * one piece, or with an error, is read as plain JSON.
 */
async function streamJson(route, payload, signal, onPiece) {
  const response = await api.fetchApi(route, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.body || !(response.headers.get("Content-Type") ?? "").includes("ndjson")) {
    return readJson(response);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  let done = null;
  const take = (line) => {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      return;
    }
    if (event?.type === "done") done = event;
    else if (event?.type === "delta") onPiece(event);
  };
  for (;;) {
    const { value, done: ended } = await reader.read();
    if (ended) break;
    const split = splitLines(rest + decoder.decode(value, { stream: true }));
    rest = split.rest;
    split.lines.forEach(take);
  }
  if (rest.trim()) take(rest);
  return done ?? { error: "The answer broke off before it was complete." };
}

function secondsSince(started) {
  return `${Math.max(0, Math.round((Date.now() - started) / 1000))} s`;
}

function timeAgo(when) {
  const at = new Date(when).getTime();
  if (!at) return "";
  const minutes = Math.floor((Date.now() - at) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} d ago`;
  return new Date(at).toLocaleDateString("en-US");
}

/** Uploaded names are "subfolder/name" when there is a subfolder. */
function thumbUrl(name) {
  const at = name.lastIndexOf("/");
  const filename = encodeURIComponent(at < 0 ? name : name.slice(at + 1));
  const subfolder = at < 0 ? "" : `&subfolder=${encodeURIComponent(name.slice(0, at))}`;
  return api.apiURL(`/view?filename=${filename}&type=input${subfolder}`);
}

function secondsEl(started) {
  const span = el("span", { text: secondsSince(started) });
  span.dataset.started = String(started);
  return span;
}

function tick() {
  if (!jobs.size) {
    clearInterval(ticker);
    ticker = null;
  }
  if (!view) return;
  for (const span of view.modal.querySelectorAll("[data-started]")) {
    span.textContent = secondsSince(Number(span.dataset.started));
  }
}

// --- Persistence --------------------------------------------------------------

async function readStore() {
  const response = await api.getUserData(CHATS_FILE);
  if (response.status === 404) {
    store = emptyStore();
    return;
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const raw = await response.json();
  if (!isStore(raw)) throw new Error("not a chat file this version can read");
  store = normalizeStore(raw);
}

/** A failed read is left unloaded, so a later save cannot overwrite the file. */
function ensureStore() {
  if (store) return Promise.resolve();
  storeLoad ??= readStore().finally(() => {
    storeLoad = null;
  });
  return storeLoad;
}

async function save() {
  if (saving) {
    saveAgain = true;
    return;
  }
  saving = true;
  try {
    do {
      saveAgain = false;
      await api.storeUserData(CHATS_FILE, store, {
        stringify: true,
        overwrite: true,
        throwOnError: true,
      });
    } while (saveAgain);
  } catch (error) {
    console.error("[WarpPipe] saving chats", error);
    toast("error", "Could not save chats", String(error?.message ?? error), 6000);
  } finally {
    saving = false;
  }
}

// --- The Write button on each prompt ------------------------------------------

function syncButton(node) {
  const element = node ? buttons.get(node) : null;
  if (!element) return;
  const writing = [...jobs.values()].some((job) => job.originNode === node);
  const finished = [...unseen.values()].includes(node);
  element.classList.toggle("is-writing", writing);
  element.classList.toggle("has-unseen", finished);
  element.title = writing
    ? "Prompt Writer: writing…"
    : finished
      ? "Prompt Writer: a prompt is ready"
      : "Open the Prompt Writer";
}

/**
 * The AI Prompt Writer button for this node, in the row it floats in, made
 * once. The caller puts it in the prompt box's holder; it sits just above it.
 * On a narrow node it says only "✨ AI".
 */
export function writeBarFor(node) {
  let bar = bars.get(node);
  if (bar) return bar;
  ensureStyle();
  // The canvas selects and drags on the same press underneath.
  const keep = (event) => event.stopPropagation();
  const element = el(
    "button",
    {
      type: "button",
      class: "wpw-open",
      onPointerdown: keep,
      onDblclick: keep,
      onClick: (event) => {
        event.preventDefault();
        event.stopPropagation();
        openPromptWriter(node);
      },
    },
    "✨ AI",
    el("span", { class: "wpw-open-more", text: " Prompt Writer" })
  );
  bar = el("div", { class: "wpw-open-bar" }, element);
  buttons.set(node, element);
  bars.set(node, bar);
  syncButton(node);
  return bar;
}

// --- Drafts and the base of the next write ------------------------------------

function draftOf(chatId) {
  let draft = drafts.get(chatId);
  if (!draft) {
    draft = { text: "", images: [], base: null, uploading: 0, note: null };
    drafts.set(chatId, draft);
  }
  return draft;
}

function currentChat() {
  if (!view || !store) return null;
  return store.chats.find((chat) => chat.id === view.chatId) ?? null;
}

/** What the next write starts from: {id, text, prompt}, or null for nothing. */
function chosenBase(chat, draft) {
  const value = startValue(chat, draft.base);
  if (value === NEW_PROMPT) return null;
  if (value === CURRENT_PROMPT) {
    const widget = targetNow()?.node?.widgets?.find((w) => w.name === TEXT_WIDGET);
    return { id: CURRENT_PROMPT, text: String(widget?.value ?? ""), prompt: true };
  }
  const version = chat.versions.find((v) => v.id === value);
  return { id: version.id, text: version.text, prompt: false };
}

/** What a Write sends: a new request, or the answer to the question the AI asked. */
function nextStep(chat, draft) {
  const typed = draft.text.trim();
  const pending = chat.pending;
  if (pending) {
    if (!typed) return { error: "Type your answer first." };
    return {
      request: pending.request,
      images: pending.images,
      baseId: pending.baseId,
      baseText: chat.versions.find((v) => v.id === pending.baseId)?.text ?? "",
      turns: [...pending.turns, { question: pending.question, answer: typed }],
      answer: typed,
      // A Retry that was asked a question still writes over its version.
      replaceId: pending.replaceId,
    };
  }
  const base = chosenBase(chat, draft);
  if (base?.prompt && !base.text.trim()) return { error: "That Prompt + LoRAs is empty." };
  if (!typed && !base?.text?.trim()) return { error: "Type what you want first." };
  return {
    request: typed,
    images: [...draft.images],
    baseId: base?.id ?? null,
    // The node's text, which becomes a version of the chat when the request is sent.
    nodeText: base?.prompt ? base.text : null,
    baseText: base?.text ?? "",
    turns: [],
    answer: null,
  };
}

function setNote(chatId, text, isError = false) {
  draftOf(chatId).note = text ? { text, isError } : null;
  if (view?.chatId === chatId) renderNote();
}

// --- What the LLM is told about LoRAs -----------------------------------------

function loraScope() {
  const scope = getSetting(SETTING_LORA_SCOPE, "all");
  return LORA_SCOPES.includes(scope) ? scope : "all";
}

/** The lists a write for this node would send; null when there is nothing to send. */
async function loraListsFor(node, scope) {
  try {
    const [entries, base] = await Promise.all([loraLibrary(), connectedBase(node)]);
    return buildLoraLists(entries, base, targetLoraTags(node), {
      scope,
      listed: targetOffLoraTags(node),
    });
  } catch (error) {
    console.warn("[WarpPipe] LoRA library", error);
    return null;
  }
}

/** Done once per request, so a Retry repeats what failed. */
async function attachLoras(spec) {
  if (spec.loraRefs) return;
  spec.loraRefs = new Map();
  if (spec.loraScope === "off") return;
  const lists = await loraListsFor(spec.loraNode, spec.loraScope);
  if (!lists) return;
  spec.payload.loras = { available: lists.available, active: lists.active };
  spec.loraRefs = lists.refs;
  spec.activeLoras = lists.activeChips;
}

// --- Writing ------------------------------------------------------------------

function findModel(id) {
  return lists.models.find((m) => m.id === id) ?? null;
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/**
 * Everything one request needs, with the model and settings chosen now: a
 * Retry may be because of the model. `step` is what is asked and what it edits.
 */
function writeSpec(chat, step, images) {
  const model = String(getSetting(SETTING_MODEL, ""));
  const systemPrompt = String(getSetting(SETTING_SYSTEM_PROMPT, ""));
  const temperature = clampTemperature(getSetting(SETTING_TEMPERATURE, DEFAULT_TEMPERATURE));
  const thinkingLevel = reasoningLevel(getSetting(SETTING_REASONING, ""));
  const scope = loraScope();
  return {
    request: step.request,
    images,
    baseId: step.baseId,
    // The version a Retry writes over; null for a new version.
    replaceId: step.replaceId ?? null,
    turns: step.turns,
    answer: step.answer,
    model,
    systemPrompt,
    temperature,
    thinkingLevel,
    server: hostOf(baseUrl()),
    originNode: view.promptNode,
    loraScope: scope,
    loraNode: view.targetNode,
    payload: {
      base_url: baseUrl(),
      model,
      system_prompt: systemPrompt,
      temperature,
      reasoning_effort: thinkingLevel,
      idea: step.request,
      previous_result: step.baseText,
      references: images,
      turns: step.turns,
      // For the request log on the server.
      chat_id: chat.id,
      lora_scope: scope,
    },
  };
}

/** The images a request sends: none when the model chosen now cannot see them. */
function imagesFor(images) {
  const names = (images ?? []).filter((name) => typeof name === "string");
  if (!names.length || findModel(String(getSetting(SETTING_MODEL, "")))?.vision) {
    return { images: names, note: null };
  }
  return {
    images: [],
    note: { text: "Images left out: this model cannot see images.", isError: false },
  };
}

function startWrite(chat) {
  if (jobs.has(chat.id)) return;
  flushEdits();
  refreshTargets();
  const draft = draftOf(chat.id);
  if (!String(getSetting(SETTING_MODEL, ""))) {
    setNote(chat.id, "Pick a model first.", true);
    return;
  }
  if (draft.uploading) {
    setNote(chat.id, "Wait for the images to finish uploading.", true);
    return;
  }
  const step = nextStep(chat, draft);
  if (step.error) {
    setNote(chat.id, step.error, true);
    view.input.focus();
    return;
  }
  if (step.nodeText != null) {
    const base = adoptText(chat, step.nodeText, {
      id: uid(),
      now: Date.now(),
      source: targetNow()?.label ?? null,
    });
    step.baseId = base.id;
    save();
  }

  // An answer goes with the images its request sent, unless the model changed to one that cannot see.
  const { images, note } = imagesFor(step.images);
  const spec = writeSpec(chat, step, images);

  draft.text = "";
  if (step.answer == null) {
    draft.images = [];
    draft.base = null;
  }
  draft.note = note;
  view.input.value = "";
  growInput();
  launch(chat, spec);
}

/**
 * A version the AI wrote, written again from its own base and images: over
 * itself with its own request and answers (Retry), or as a new version beside
 * it (Recreate), with the request as it was or as you changed it. Sent at once.
 */
function writeAgainNow(chat, version, { replace, request = version.request }) {
  if (jobs.has(chat.id) || chat.pending) return;
  flushEdits();
  refreshTargets();
  if (!String(getSetting(SETTING_MODEL, ""))) {
    setNote(chat.id, "Pick a model first.", true);
    return;
  }
  // Typing in it just now may have made a version from it.
  if (replace && hasFollowUps(chat, version.id)) {
    setNote(chat.id, followedTitle(version.n), true);
    return;
  }
  const asked = String(request ?? "").trim();
  const changed = asked !== String(version.request ?? "").trim();
  if (!asked && !version.baseId) {
    setNote(chat.id, "Type what you want first.", true);
    return;
  }
  const { images, note } = imagesFor(version.images);
  const baseText = chat.versions.find((v) => v.id === version.baseId)?.text ?? "";
  const spec = writeSpec(
    chat,
    {
      request: changed ? asked : version.request,
      baseId: version.baseId,
      replaceId: replace ? version.id : null,
      // The answers were to its own request; a changed one may be asked again.
      turns: changed ? [] : version.questions,
      answer: null,
      baseText,
    },
    images
  );
  spec.titleAsked = true;
  // Same request, same title: a Retry keeps its own, a Recreate takes it along.
  // A changed request is a request of its own: it is named, and a Stop puts it in the input.
  spec.fromVersion = !changed;
  spec.title = changed ? null : version.title;
  if (view.recreating?.id === version.id) view.recreating = null;
  draftOf(chat.id).note = note;
  launch(chat, spec);
}

/** Runs one request; `spec` is everything it needs, so a Retry can repeat it. */
async function launch(chat, spec) {
  if (clearFailure(chat)) save();
  const controller = new AbortController();
  const job = {
    ...spec,
    chatId: chat.id,
    controller,
    startedAt: Date.now(),
    dropped: false,
    thinking: "",
    text: "",
    tokens: 0,
  };
  jobs.set(chat.id, job);
  syncButton(spec.originNode);
  ticker ??= setInterval(tick, TICK_MS);
  refresh(chat.id);
  const named = chat.versions.some((v) => v.kind === KIND_WRITTEN);
  if (!named && !spec.titleAsked && spec.answer == null) {
    spec.titleAsked = true;
    nameChat(chat, spec);
  }
  // The version's own title is asked for while it is written, so it is there when it is done.
  if (!spec.title && !spec.fromVersion) askTitle(spec.request, titleKind(spec));

  let out = null;
  let message = null;
  let cancelled = false;
  try {
    await attachLoras(spec);
    out = await streamJson("/warppipe/llm/write", spec.payload, controller.signal, (piece) => {
      job.thinking += piece.thinking ?? "";
      job.text += piece.text ?? "";
      job.tokens = piece.tokens ?? job.tokens;
      paintLiveSoon();
    });
    if (out?.error) message = String(out.error);
    else if (!String(out?.question ?? out?.text ?? "").trim()) {
      message = "The model returned no text.";
    }
  } catch (error) {
    if (error?.name === "AbortError") cancelled = true;
    else message = String(error?.message ?? error);
  }

  jobs.delete(chat.id);
  syncButton(spec.originNode);
  if (job.dropped) return;

  // A Retry or Recreate came from a version, which still holds its request: nothing to give back.
  if (cancelled) {
    if (!spec.fromVersion) restoreDraft(chat, spec);
  } else if (message) {
    recordFailure(chat, spec, message, Date.now());
    save();
    toast("error", "Prompt failed", chat.title, 6000);
  } else if (out.question) {
    askQuestion(chat, spec, out);
  } else {
    finish(chat, spec, out);
  }
  refresh(chat.id);
}

function finish(chat, spec, out) {
  const now = Date.now();
  const written = {
    now,
    text: String(out.text),
    model: spec.model,
    systemPrompt: spec.systemPrompt,
    // What it was written with, and the system prompt's text as the server read it.
    temperature: spec.temperature,
    thinkingLevel: spec.thinkingLevel,
    loraScope: spec.loraScope,
    server: spec.server,
    systemPromptKey: keepSystemPrompt(chat, out.system_prompt_text),
    cost: out.cost,
    seconds: out.seconds,
    usage: out.usage,
    suggestions: resolveSuggestions(out.suggestions, spec.loraRefs),
    activeLoras: spec.activeLoras ?? [],
    questions: spec.turns ?? [],
    reasoning: String(out.reasoning ?? ""),
  };
  // A Retry writes over its version, unless something was made from it meanwhile
  // or it is gone: then the answer is a new version, so nothing is lost.
  const over =
    spec.replaceId && !hasFollowUps(chat, spec.replaceId)
      ? replaceVersion(chat, spec.replaceId, written)
      : null;
  if (over) comparisons.delete(over.id);
  const version =
    over ??
    addVersion(chat, {
      id: uid(),
      request: spec.request,
      // A Recreate keeps the title of the version it wrote again: same request.
      title: spec.title ?? "",
      images: spec.images,
      baseId: spec.baseId,
      ...written,
    });
  unfoldTo(chat, version.id);
  dropQuestion(chat);
  nameVersion(chat, version, spec);
  // The new answer is the one open card.
  if (view?.chatId === chat.id) view.open = version.id;
  // The first version titles the chat with its first words; a generated title wins.
  if (spec.aiTitle) setAutoTitle(chat, spec.aiTitle);
  save();
  if (view?.chatId !== chat.id) {
    unseen.set(chat.id, spec.originNode);
    syncButton(spec.originNode);
  }
  toast("success", "Prompt ready", chat.title);
}

/** The AI asked instead of writing: the step waits in the chat for the answer. */
function askQuestion(chat, spec, out) {
  openQuestion(chat, spec, String(out.question), Date.now());
  if (spec.aiTitle) setAutoTitle(chat, spec.aiTitle);
  save();
  if (view?.chatId !== chat.id) {
    unseen.set(chat.id, spec.originNode);
    syncButton(spec.originNode);
  }
  toast("info", "The AI has a question", chat.title);
}

/** What the cheap model is asked to name, once per text: kind, model and text → its answer. */
const titleAsks = new Map();

/** How a step is named: a new prompt for what it shows, an edit for what it changes. */
function titleKind(step) {
  return step.baseId ? "edit" : "chat";
}

/**
 * The cheap title model's answer, {title, kind}, or null. `kind` is what the
 * server says it named, which a server from before edit titles leaves out.
 * The same text is asked once, so a chat and its first version share one
 * answer. A failure is asked again next time.
 */
function titleOf(source, kind = "chat") {
  const text = String(source ?? "").trim();
  const model = String(getSetting(SETTING_TITLE_MODEL, DEFAULT_TITLE_MODEL)).trim();
  if (!text || !model) return Promise.resolve(null);
  const key = `${kind}\n${model}\n${text}`;
  let ask = titleAsks.get(key);
  if (!ask) {
    const failed = (why) => {
      console.warn("[WarpPipe] title", why);
      titleAsks.delete(key);
      return null;
    };
    ask = getJson("/warppipe/llm/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: baseUrl(), model, source: text, kind }),
    })
      .then((out) => {
        if (out?.error) return failed(out.error);
        // A server from before edit titles is asked again after it restarts.
        if (!out?.kind) titleAsks.delete(key);
        const title = String(out?.title ?? "").trim();
        return title ? { title, kind: out.kind ?? null } : null;
      })
      .catch(failed);
    titleAsks.set(key, ask);
  }
  return ask;
}

/** A short title from the cheap title model, or null. */
function askTitle(source, kind = "chat") {
  return titleOf(source, kind).then((out) => out?.title ?? null);
}

/** Ask the cheap model for a title while the prompt is written. On failure the first words stay. */
async function nameChat(chat, spec) {
  if (chat.titleEdited) return;
  const source = spec.request || spec.payload?.previous_result || "";
  // Named for what its first prompt shows; a first version that is a new prompt shares this answer.
  const named = await askTitle(source, "chat");
  spec.aiTitle = named;
  if (!setAutoTitle(chat, named)) return;
  save();
  if (!view) return;
  renderRail();
  if (view.chatId === chat.id) renderTitle();
}

/** A version gets its title when the cheap model answers; until then its request stands in. */
function nameVersion(chat, version, spec) {
  if (version.title || !version.request) return;
  askTitle(version.request, titleKind(spec)).then((title) => giveTitle(chat, version, title));
}

/**
 * A title for a version that still has none, saved and shown. In place: drawing
 * the chat again would take the caret out of the box you type in.
 */
function giveTitle(chat, version, title) {
  if (!title || version.title || !chat.versions.includes(version)) return;
  setVersionTitle(version, title);
  save();
  if (view?.chatId !== chat.id) return;
  const line = view.convo.querySelector(
    `.wpw-card[data-id="${CSS.escape(version.id)}"] .wpw-head-text`
  );
  if (line) {
    line.textContent = rowLine(chat, version);
    line.classList.remove("is-empty");
  }
  renderStart(chat, draftOf(chat.id));
}

// Naming the versions from before titles: once per page, and again only if it could not finish.
let titlesNamed = false;
const NAMING_AT_ONCE = 3;

/**
 * Every version the AI wrote that has no title yet gets one, a few at a time,
 * in the background: the versions from before titles were kept. Only with a
 * server that names edits for what they change (it says which kind it named);
 * with an older one nothing is named, and the next opening after a restart
 * tries again.
 */
async function nameUntitled() {
  if (titlesNamed || !store) return;
  titlesNamed = true;
  const todo = untitledVersions(store);
  let named = 0;
  const work = async () => {
    while (todo.length && titlesNamed) {
      const { chat, version } = todo.shift();
      const kind = titleKind(version);
      const out = await titleOf(version.request, kind);
      if (!out) continue;
      if (out.kind !== kind) {
        if (titlesNamed) {
          console.warn(
            "[WarpPipe] Prompt Writer: restart ComfyUI to name the older versions; the server does not name edits yet."
          );
        }
        titlesNamed = false;
        return;
      }
      if (!version.title) named++;
      giveTitle(chat, version, out.title);
    }
  };
  await Promise.all(Array.from({ length: NAMING_AT_ONCE }, work));
  if (named) console.info(`[WarpPipe] Prompt Writer: named ${named} older versions.`);
}

/** After Stop, the request goes back into the composer unless a new one is there. */
function restoreDraft(chat, spec) {
  const draft = draftOf(chat.id);
  if (draft.text.trim() || draft.images.length) return;
  fillDraft(chat, spec);
}

/** Put a request back into the composer with its images and starting point, or an answer back. */
function fillDraft(chat, spec) {
  const draft = draftOf(chat.id);
  const answering = spec.answer != null;
  const merged = mergedDraft(
    draft,
    {
      text: (answering ? spec.answer : spec.request) ?? "",
      images: answering ? [] : (spec.images ?? []).filter((name) => typeof name === "string"),
    },
    MAX_IMAGES
  );
  draft.text = merged.text;
  draft.images = merged.images;
  if (!answering) draft.base = spec.baseId ?? NEW_PROMPT;
  if (merged.overflow) {
    draft.note = { text: `At most ${MAX_IMAGES} images per request.`, isError: true };
  }
  if (view?.chatId === chat.id) {
    view.input.value = draft.text;
    growInput();
  }
}

/** Show the composer again after a request moved back into it. */
function focusComposer() {
  renderRail();
  renderConvo(false);
  renderChips();
  renderSend();
  renderNote();
  view.input.focus();
  view.input.setSelectionRange(view.input.value.length, view.input.value.length);
}

/** The request of a step that waits for an answer goes back into the input instead. */
function rewindQuestion(chat) {
  if (jobs.has(chat.id)) return;
  const step = dropQuestion(chat);
  if (!step) return;
  clearFailure(chat);
  fillDraft(chat, step);
  save();
  focusComposer();
}

/** The same for a request that failed, which never became a version. */
function rewindFailure(chat) {
  const failure = chat.failure;
  clearFailure(chat);
  if (failure) fillDraft(chat, failure);
  save();
  focusComposer();
}

/** A failed request built again from what the chat kept, so a Retry works after a reload too. */
function retrySpec(chat, failure) {
  const baseText = chat.versions.find((v) => v.id === failure.baseId)?.text ?? "";
  const spec = writeSpec(chat, { ...failure, baseText }, imagesFor(failure.images).images);
  spec.titleAsked = true;
  return spec;
}

// --- Images -------------------------------------------------------------------

async function uploadImage(file) {
  const body = new FormData();
  body.append("image", file);
  const response = await api.fetchApi("/upload/image", { method: "POST", body });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  return data.subfolder ? `${data.subfolder}/${data.name}` : data.name;
}

async function addImages(chatId, files) {
  const images = files.filter((file) => file.type.startsWith("image/"));
  if (!images.length) return;
  const draft = draftOf(chatId);
  const room = Math.max(0, MAX_IMAGES - draft.images.length - draft.uploading);
  if (images.length > room) setNote(chatId, `At most ${MAX_IMAGES} images per request.`, true);
  const chips = () => {
    if (view?.chatId === chatId) renderChips();
  };
  const accepted = images.slice(0, room);
  draft.uploading += accepted.length;
  chips();
  for (const file of accepted) {
    try {
      draft.images.push(await uploadImage(file));
    } catch (error) {
      setNote(chatId, `Upload failed: ${error?.message ?? error}`, true);
    }
    draft.uploading -= 1;
    chips();
  }
}

function pickImages() {
  const chatId = view.chatId;
  const input = el("input", { type: "file", accept: "image/*", multiple: true });
  input.addEventListener("change", () => addImages(chatId, [...(input.files ?? [])]));
  input.click();
}

function hasFiles(event) {
  return [...(event.dataTransfer?.types ?? [])].includes("Files");
}

// --- Rendering ----------------------------------------------------------------

function refresh(chatId) {
  if (!view) return;
  renderRail();
  if (view.chatId !== chatId) return;
  renderTitle();
  renderConvo(true);
  renderChips();
  renderSend();
  renderNote();
}

function renderTitle() {
  const chat = currentChat();
  view.title.textContent = chat?.title || "New chat";
}

function modelButtonState() {
  const id = String(getSetting(SETTING_MODEL, ""));
  const model = findModel(id);
  if (model)
    return { text: model.name || model.id, price: modelPrice(model), title: modelLabel(model) };
  if (!lists.models.length && lists.error) {
    return { text: id || "Model list failed", title: lists.error, isError: true };
  }
  if (!lists.models.length && lists.loading) return { text: "Loading models…" };
  if (id) return { text: `${id}${NOT_LISTED}`, title: id };
  return { text: "Pick a model" };
}

/** The settings are a pop-over: while it is closed there is nothing to draw. */
function renderModelButton() {
  if (!view?.model) return;
  const state = modelButtonState();
  view.model.replaceChildren(
    el("span", { class: "wpw-option-name", text: state.text }),
    ...(state.price ? [el("span", { class: "wpw-option-price", text: state.price })] : []),
    icon("pi-chevron-down")
  );
  view.model.title = state.title ?? "";
  view.model.classList.toggle("is-error", !!state.isError);
}

function renderPrompts() {
  if (!view?.prompts) return;
  const current = String(getSetting(SETTING_SYSTEM_PROMPT, ""));
  const names = [...lists.prompts];
  if (current && !names.includes(current)) names.unshift(current);
  const options = names.map((name) => el("option", { value: name, text: name }));
  if (!options.length) {
    options.push(el("option", { value: "", text: "No system prompts" }));
  }
  view.prompts.replaceChildren(...options);
  view.prompts.value = current;
}

function renderRail() {
  if (view.railRenaming) return;
  const top = view.chats.scrollTop;
  view.chats.replaceChildren(...sortedChats(store).map(railRow));
  view.chats.scrollTop = top;
}

const MENU_W = 130;
const MENU_H = 76;

function openChatMenu(chat, anchor) {
  if (view.popover?.chatId === chat.id) {
    closePopover();
    return;
  }
  const item = (label, action, extra = "") =>
    button(
      label,
      () => {
        closePopover();
        action();
      },
      `wpw-menu-item${extra}`
    );
  const root = el(
    "div",
    { class: "wpw-pop wpw-chat-menu" },
    item("Rename", () => beginRailRename(chat)),
    item(
      "Delete",
      () => {
        view.confirmDelete = chat.id;
        renderRail();
      },
      " is-danger"
    )
  );
  const box = anchor.getBoundingClientRect();
  const below = box.bottom + 2 + MENU_H <= window.innerHeight;
  root.style.top = `${below ? box.bottom + 2 : box.top - 2 - MENU_H}px`;
  root.style.left = `${Math.max(4, box.right - MENU_W)}px`;
  openPopover(anchor, root);
  view.popover.chatId = chat.id;
}

/** The title in the list becomes a field. Enter or leaving it saves; Esc cancels. */
function beginRailRename(chat) {
  view.railRename = chat.id;
  view.renaming = { cancel: () => endRailRename(chat, false) };
  renderRail();
  view.railRenaming = true;
  const input = view.chats.querySelector(".wpw-rename-input");
  input?.focus();
  input?.select();
}

function endRailRename(chat, commit) {
  if (!view?.railRenaming) return;
  const value = view.chats.querySelector(".wpw-rename-input")?.value.trim() ?? "";
  view.railRenaming = false;
  view.railRename = null;
  view.renaming = null;
  if (commit && value && value !== chat.title) {
    renameChat(chat, value);
    save();
  }
  renderRail();
  if (chat.id === view.chatId) renderTitle();
}

function railRow(chat) {
  if (view.railRename === chat.id) {
    return el(
      "div",
      { class: "wpw-chat wpw-confirm" },
      el("input", {
        class: "wpw-rename-input",
        value: chat.title ?? "",
        maxLength: 120,
        onBlur: () => endRailRename(chat, true),
        onKeydown: (event) => {
          if (event.key === "Enter") endRailRename(chat, true);
        },
      })
    );
  }
  if (view.confirmDelete === chat.id) {
    return el(
      "div",
      { class: "wpw-chat wpw-confirm" },
      el("span", { text: `Delete "${chat.title}"?` }),
      button("Delete", () => removeChat(chat), "wpw-btn wpw-danger"),
      button("Keep", () => {
        view.confirmDelete = null;
        renderRail();
      })
    );
  }
  const job = jobs.get(chat.id);
  const failed = !!chat.failure;
  const count = chat.versions.length;
  let sub;
  if (job) {
    sub = el(
      "div",
      { class: "wpw-chat-sub is-writing" },
      el("span", { class: "wpw-spin" }),
      "Writing ",
      secondsEl(job.startedAt)
    );
  } else if (failed) {
    sub = el("div", { class: "wpw-chat-sub is-failed", text: "Failed" });
  } else if (chat.pending) {
    sub = el("div", { class: "wpw-chat-sub is-question", text: "Waiting for your answer" });
  } else {
    const when = timeAgo(chat.updated);
    sub = el(
      "div",
      { class: "wpw-chat-sub" },
      el("span", { text: `${count} version${count === 1 ? "" : "s"}` }),
      when && el("span", { text: when })
    );
  }
  return el(
    "div",
    { class: `wpw-chat${chat.id === view.chatId ? " is-open" : ""}` },
    el(
      "button",
      { type: "button", class: "wpw-chat-open", onClick: () => selectChat(chat.id) },
      el(
        "div",
        { class: "wpw-chat-title" },
        el("span", { text: chat.title, title: chat.title }),
        unseen.has(chat.id) &&
          el(
            "span",
            {
              class: "wpw-check",
              title: chat.pending ? "The AI has a question" : "Finished",
            },
            icon(chat.pending ? "pi-question-circle" : "pi-check")
          )
      ),
      sub
    ),
    (() => {
      const more = iconButton(
        "pi-ellipsis-v",
        () => openChatMenu(chat, more),
        "wpw-chat-more",
        "More"
      );
      return more;
    })()
  );
}

/** What a request says on its card's first line; an empty one asked to optimise. */
function requestText(step) {
  return step.request ? { text: step.request } : { text: "Optimise the prompt", empty: true };
}

/**
 * Under a card's first line: the images its request sent and the questions the
 * AI asked on the way. `question` is one still waiting for its answer. Null
 * when there is neither.
 */
function stepTurns(step, question = null) {
  const names = (step.images ?? []).filter((name) => typeof name === "string");
  const turns = step.turns ?? step.questions ?? [];
  if (!names.length && !turns.length && !question) return null;
  const said = (who, text, extra = "") => [
    el("span", { class: `wpw-who${extra}`, text: who }),
    el("span", { class: `wpw-said${extra}`, text }),
  ];
  const lines = [
    ...turns.flatMap((turn) => [
      ...said("AI", turn.question, " is-question"),
      ...said("You", turn.answer),
    ]),
    ...(question ? said("AI", question, " is-question") : []),
  ];
  return el(
    "div",
    { class: "wpw-turns" },
    names.length
      ? el(
          "div",
          { class: "wpw-thumbs" },
          ...names.map((name) => el("img", { src: thumbUrl(name), alt: "", loading: "lazy" }))
        )
      : null,
    lines.length ? el("div", { class: "wpw-said-grid" }, ...lines) : null
  );
}

/** The undo arrow on a card's first line: its request goes back into the input. */
function rewindButton(onRewind, title, always = false) {
  return iconButton(
    "pi-undo",
    (event) => {
      event.stopPropagation();
      onRewind();
    },
    `wpw-rewind${always ? " is-shown" : ""}`,
    title
  );
}

/**
 * A switch in a card's row of extras, under the prompt: the changes, the
 * thinking, the LoRAs, what it was written with. Open, it shows its part.
 */
function metaToggle(label, { open, onToggle, title }, ...extra) {
  return el(
    "button",
    {
      type: "button",
      class: `wpw-toggle${open ? " is-open" : ""}`,
      title,
      "aria-expanded": String(open),
      onClick: onToggle,
    },
    icon("pi-chevron-right"),
    label,
    ...extra
  );
}

/** Opens or closes one of a card's parts, by its key in one of the view's sets; the card stays put. */
function flip(set, key) {
  return () =>
    keepInPlace(key, () => {
      if (set.has(key)) set.delete(key);
      else set.add(key);
      renderConvo(false);
    });
}

/** What the model is doing, from what has come so far. */
function phaseOf(job) {
  if (job.text) return "Writing";
  if (job.thinking) return "Thinking";
  return "Waiting for the model";
}

function tokensText(job) {
  return job.tokens ? `≈ ${Number(job.tokens).toLocaleString("en-US")} tokens` : "";
}

/**
 * The card of a write in progress, with handles to fill as the pieces come.
 * Its thinking stays folded until you open it, and stays open once you did.
 */
function liveCard(chat, job, label) {
  const phase = el("span", { text: phaseOf(job) });
  const tokens = el("span", { text: tokensText(job) });
  const status = el(
    "span",
    { class: "wpw-status" },
    el("span", { class: "wpw-spin" }),
    phase,
    secondsEl(job.startedAt),
    tokens
  );
  const key = `live:${chat.id}`;
  const open = view.thinking.has(key);
  const body = el("div", { class: "wpw-thinking-text", text: job.thinking });
  body.hidden = !open;
  const block = el(
    "div",
    { class: "wpw-meta" },
    metaToggle("Thinking", {
      open,
      onToggle: flip(view.thinking, key),
      title: "What the model thinks before it writes",
    })
  );
  block.hidden = !job.thinking;
  const text = el("div", { class: "wpw-text is-live", text: job.text });
  text.hidden = !job.text;
  view.live = { chatId: chat.id, phase, tokens, thinking: { block, body }, text };
  // A Retry writes in its version's place, under its title.
  const line = job.title ? { text: job.title } : requestText(job);
  return el(
    "div",
    { class: "wpw-card is-open is-writing" },
    cardHead({ label, ...line, status }),
    stepTurns(job),
    // Above the text, so opening it never moves the switch.
    block,
    body,
    text
  );
}

let livePaint = null;

/** Pieces come fast; the card catches up a few times a second. */
function paintLiveSoon() {
  livePaint ??= setTimeout(() => {
    livePaint = null;
    paintLive();
  }, LIVE_MS);
}

function paintLive() {
  const live = view?.live;
  const job = live ? jobs.get(live.chatId) : null;
  if (!job || view.chatId !== live.chatId) return;
  const convo = view.convo;
  const following = convo.scrollHeight - convo.scrollTop - convo.clientHeight < 60;
  live.phase.textContent = phaseOf(job);
  live.tokens.textContent = tokensText(job);
  if (job.thinking) {
    live.thinking.block.hidden = false;
    live.thinking.body.textContent = job.thinking;
    live.thinking.body.scrollTop = live.thinking.body.scrollHeight;
  }
  if (job.text) {
    live.text.hidden = false;
    live.text.textContent = job.text;
  }
  if (following) convo.scrollTop = convo.scrollHeight;
}

/**
 * A card's first line: its number, its title (or what was asked), what its
 * fold hides, how it stands, and its menu. One line of the same height closed
 * and open, so opening a card only adds what is under it. Where a version came
 * from is in the graph, and in its generation info.
 */
function cardHead({
  label,
  text,
  empty = false,
  status = null,
  folded = null,
  rewind = null,
  menu = null,
}) {
  return el(
    "div",
    { class: "wpw-card-head" },
    el("span", { class: "wpw-ver", text: label }),
    el("span", { class: `wpw-head-text${empty ? " is-empty" : ""}`, text, title: text }),
    folded,
    rewind,
    status,
    menu
  );
}

const versionsWord = (count) => `${count} ${count === 1 ? "version" : "versions"}`;

/**
 * The fold control in the graph's margin, beside a version's dot: − folds the
 * versions made from it that lead away from the kept ones, + shows them again.
 * Null when folding would hide nothing.
 */
function foldToggle(chat, version, keep) {
  if (!canFold(chat, version.id, keep)) return null;
  const folded = chat.folded.includes(version.id);
  const count = foldedCount(chat, version.id, keep);
  return iconButton(
    folded ? "pi-plus" : "pi-minus",
    () => {
      toggleFold(chat, version.id);
      save();
      renderConvo(false);
    },
    "wpw-fold",
    folded
      ? `Show the ${versionsWord(count)} folded under v${version.n}`
      : `Fold the other versions made from v${version.n}`
  );
}

/** How many versions a fold hides now. */
function foldedTag(chat, version, keep) {
  const count = foldedCount(chat, version.id, keep);
  if (!count) return null;
  return el("span", {
    class: "wpw-fold-count",
    text: `+${count}`,
    title: `${versionsWord(count)} folded`,
  });
}

/** The chat's target, by its label: the graph may have changed since it was chosen. */
function refreshTargets() {
  view.targets = listTargets(app.rootGraph) ?? [];
  const label = currentChat()?.targetLabel;
  view.targetNode = resolveTarget(view.targets, label, view.promptNode)?.node ?? null;
  renderTargets();
}

function targetIndex() {
  return view.targets.findIndex((target) => target.node === view.targetNode);
}

/** The options are rebuilt only when they change, so a re-render cannot close an open list. */
function renderTargets() {
  const select = view.target;
  const labels = view.targets.length
    ? view.targets.map((target) => target.label)
    : ["No Prompt + LoRAs"];
  const key = labels.join("\n");
  if (select.dataset.labels !== key) {
    select.dataset.labels = key;
    select.replaceChildren(...labels.map((label) => el("option", { text: label })));
  }
  select.disabled = !view.targets.length;
  select.selectedIndex = Math.max(0, targetIndex());
}

function chooseTarget() {
  const chat = currentChat();
  const target = view.targets[view.target.selectedIndex];
  if (!chat || !target) return;
  chat.targetLabel = target.label;
  save();
  renderConvo(false);
  renderChips();
}

function labelOfNode(node) {
  return (listTargets(app.rootGraph) ?? []).find((target) => target.node === node)?.label ?? null;
}

/** Send to ComfyUI, naming the group (or node) it writes into: the one chosen under For. */
function sendButton(version) {
  const where = targetNow()?.label;
  const use = el(
    "button",
    {
      type: "button",
      class: "wpw-btn wpw-primary wpw-send-to",
      title: where
        ? `Write v${version.n} into the prompt box of “${where}” (chosen under For, at the top)`
        : "There is no Prompt + LoRAs in this workflow to write into",
      onClick: () => useVersion(version),
    },
    icon("pi-send"),
    el("span", {
      class: "wpw-send-label",
      text: where ? `Send to ComfyUI (${where})` : "Send to ComfyUI",
    })
  );
  use.disabled = !view.targets.length;
  return use;
}

/** The LoRA boxes of the target that count: the list, and list 2 while it is on. */
function targetLoraWidgets() {
  const widgets = targetNow()?.node?.widgets ?? [];
  const named = (name) => widgets.find((w) => w.name === name);
  const list = [named(LORAS_WIDGET)];
  if (named("loras_2_on")?.value === true) list.push(named("loras_2"));
  return list.filter(Boolean);
}

/** A tag written in the prompt itself is on, but sits in a line of prose. */
function isInPromptText(tag) {
  const widgets = targetNow()?.node?.widgets ?? [];
  return isLoraListed(widgets.find((w) => w.name === TEXT_WIDGET)?.value, tag);
}

/** One LoRA under a version. `compact` is for one that was on when the version was written. */
function loraChip(chat, version, item, compact) {
  const listed = targetLoraWidgets().some((w) => isLoraListed(w.value, item.tag));
  const inText = !listed && isInPromptText(item.tag);
  const on = listed || inText;
  const where = targetNow()?.label ?? "the chosen node";
  const title = item.title || item.name || item.tag;
  const words = item.triggers.join(", ");
  const verb = compact ? (on ? "Switch off in" : "Switch on in") : on ? "Remove from" : "Add to";
  const toggle = iconButton(
    on ? "pi-check" : "pi-plus",
    () => toggleSuggestion(chat, version, item, { keepText: compact }),
    "wpw-lora-add",
    inText ? "Written in the prompt text" : `${verb} the LoRA list of ${where}`
  );
  toggle.disabled = inText;
  return el(
    "div",
    {
      class: `wpw-lora${on ? " is-added" : ""}${compact ? " is-compact" : ""}`,
      title: words ? `${title}\n${words}` : title,
    },
    item.thumbnail
      ? el("img", { class: "wpw-lora-thumb", src: item.thumbnail, alt: "", loading: "lazy" })
      : el("span", { class: "wpw-lora-thumb" }),
    el(
      "span",
      { class: "wpw-lora-body" },
      el(
        "span",
        { class: "wpw-lora-top" },
        el("span", { class: "wpw-lora-title", text: title }),
        el("span", { class: "wpw-lora-strength", text: item.strength.toFixed(2) })
      ),
      !compact && el("span", { class: "wpw-lora-reason", text: item.reason })
    ),
    toggle
  );
}

function loraRow(label, chips) {
  if (!chips.length) return null;
  return el(
    "div",
    { class: "wpw-suggest" },
    el("div", { class: "wpw-suggest-label", text: label }),
    el("div", { class: "wpw-suggest-row" }, ...chips)
  );
}

/** The LoRAs switch: how many were on, and how many the AI suggests. Null when there are none. */
function loraToggle(version) {
  const active = version.activeLoras?.length ?? 0;
  const suggested = version.suggestions?.length ?? 0;
  if (!active && !suggested) return null;
  const count = (text, extra = "") => el("span", { class: `wpw-toggle-count${extra}`, text });
  return metaToggle(
    "LoRAs",
    {
      open: view.loras.has(version.id),
      onToggle: flip(view.loras, version.id),
      title: "The LoRAs that were on when it was written, and the ones the AI suggests",
    },
    active ? count(`${active} on`) : null,
    suggested ? count(`${suggested} suggested`, " is-new") : null
  );
}

/** What was on when this version was written, then what the LLM would add. */
function loraPanel(chat, version) {
  const chips = (items, compact) =>
    (items ?? []).map((item) => loraChip(chat, version, item, compact));
  return el(
    "div",
    { class: "wpw-panel" },
    loraRow("On when it was written", chips(version.activeLoras, true)),
    loraRow("Suggested", chips(version.suggestions, false))
  );
}

const LORA_SCOPE_NAMES = { off: "Off", list: "List", all: "All" };

function thinkingName(level) {
  return REASONING_LEVELS.find(([value]) => value === level)?.[1] ?? level;
}

function writtenAt(when) {
  const at = new Date(when);
  return Number.isNaN(at.getTime())
    ? ""
    : at.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

/** One part of the generation info: its name, then what it holds. */
function infoPart(name, ...body) {
  return el(
    "section",
    { class: "wpw-info-part" },
    el("h3", { class: "wpw-info-name", text: name }),
    ...body
  );
}

function infoText(text, note = false) {
  return el("div", { class: `wpw-info-text${note ? " is-note" : ""}`, text });
}

function closeInfo() {
  if (!view?.info) return;
  view.info.remove();
  view.info = null;
}

/**
 * Everything about how a version came to be, in one place: where it came
 * from, when, the model and settings, what it cost, your request and the
 * questions on the way, the prompt it edited, the system prompt as it was
 * read for that write, and the model's thinking.
 */
function openInfo(chat, version) {
  closePopover();
  closeInfo();
  const written = version.kind === KIND_WRITTEN;
  const base = chat.versions.find((v) => v.id === version.baseId);
  const from = originLabel(chat, version);
  const usage = version.usage ?? {};
  const count = (n) => Number(n).toLocaleString("en-US");
  const rows = [
    ["Made from", from.charAt(0).toUpperCase() + from.slice(1)],
    [written ? "Written" : "Made", writtenAt(version.created)],
    written && ["Model", version.model],
    written && version.temperature != null && ["Temperature", String(version.temperature)],
    written &&
      version.thinkingLevel != null && ["Thinking level", thinkingName(version.thinkingLevel)],
    written && version.loraScope && ["LoRAs offered", LORA_SCOPE_NAMES[version.loraScope]],
    written && version.server && ["Server", version.server],
    version.cost != null && ["Cost", `${Number((version.cost * 100).toFixed(2))} ¢`],
    version.seconds != null && ["Time", `${Math.round(version.seconds)} s`],
    usage.prompt_tokens != null &&
      usage.completion_tokens != null && [
        "Tokens",
        `${count(usage.prompt_tokens)} in → ${count(usage.completion_tokens)} out`,
      ],
    version.sentAt != null && ["Sent to ComfyUI", writtenAt(version.sentAt)],
  ].filter((row) => row && row[1]);

  const parts = [];
  if (written) {
    parts.push(
      infoPart(
        "Your request",
        version.request ? infoText(version.request) : infoText("Optimise the prompt", true),
        stepTurns({ images: version.images })
      )
    );
    const turns = stepTurns({ questions: version.questions });
    if (version.questions?.length) parts.push(infoPart("Questions on the way", turns));
  }
  if (base) {
    parts.push(
      infoPart(
        written ? `The prompt it edited · v${base.n}` : `Edited from · v${base.n}`,
        infoText(base.text)
      )
    );
  }
  if (written) {
    const text = systemPromptOf(chat, version);
    parts.push(
      infoPart(
        version.systemPrompt ? `System prompt · ${version.systemPrompt}` : "System prompt",
        text
          ? infoText(text)
          : infoText(
              version.temperature == null
                ? "Written before versions kept their settings: only the model and the system prompt's name are known."
                : "The system prompt's text was not kept with this version.",
              true
            )
      )
    );
    if (version.reasoning) parts.push(infoPart("Thinking", infoText(version.reasoning)));
  }

  const panel = el(
    "div",
    {
      class: "wpw-info",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": `Generation info for v${version.n}`,
      tabIndex: -1,
    },
    el(
      "div",
      { class: "wpw-info-head" },
      el("span", { class: "wpw-ver", text: `v${version.n}` }),
      el("span", { class: "wpw-info-title", text: rowLine(chat, version) }),
      iconButton("pi-times", closeInfo, "wpw-icon", "Close (Esc)")
    ),
    el(
      "div",
      { class: "wpw-info-body" },
      el(
        "dl",
        { class: "wpw-info-grid" },
        ...rows.flatMap(([name, value]) => [el("dt", { text: name }), el("dd", { text: value })])
      ),
      ...parts
    )
  );
  const shade = el("div", { class: "wpw-info-shade" }, panel);
  shade.addEventListener("pointerdown", (event) => {
    if (event.target === shade) closeInfo();
  });
  view.modal.append(shade);
  view.info = shade;
  panel.focus();
}

/** The version this one was made from, as a text to compare it with. */
function versionBase(chat, version) {
  const base = chat.versions.find((v) => v.id === version.baseId);
  return base ? { text: base.text, name: `v${base.n}` } : null;
}

/**
 * version id → its last comparison; the texts say when it is out of date. The
 * line rows are worked out the first time the changes are shown. Emptied when
 * the pop-up closes.
 */
const comparisons = new Map();

function compare(version, before) {
  const known = comparisons.get(version.id);
  if (known?.before === before && known.after === version.text) return known;
  const parts = diffWords(before, version.text);
  const fresh = { before, after: version.text, parts, stats: diffStats(parts), rows: null };
  comparisons.set(version.id, fresh);
  return fresh;
}

/**
 * What changed from the version it was made from, in words added and removed.
 * Open, the prompt above shows its changes instead of the plain text.
 */
function changesToggle(chat, version) {
  const base = versionBase(chat, version);
  if (!base) return null;
  const { stats } = compare(version, base.text);
  if (!stats.added && !stats.removed) {
    return el("span", { class: "wpw-meta-note", text: `Same as ${base.name}` });
  }
  const words = stats.added + stats.removed === 1 ? "word" : "words";
  return metaToggle(
    stats.rewrite ? "Rewritten" : "Changes",
    {
      open: view.changes.has(version.id),
      onToggle: flip(view.changes, version.id),
      title: stats.rewrite
        ? `Mostly rewritten from ${base.name}: show the two side by side`
        : `Show what changed from ${base.name}, in ${words}`,
    },
    el("span", { class: "wpw-diff-added", text: `+${stats.added}` }),
    el("span", { class: "wpw-diff-removed", text: `−${stats.removed}` })
  );
}

/**
 * The row above the prompt: the changes and the LoRAs, and the LoRAs under it
 * when open. Above the prompt, so a switch never moves when the prompt below it
 * turns into its changes. Everything else is in the generation info.
 */
function metaParts(chat, version) {
  const request = requestToggle(version);
  const row = [request, changesToggle(chat, version), loraToggle(version)].filter(Boolean);
  if (!row.length) return [];
  return [
    el("div", { class: "wpw-meta" }, ...row),
    request && !view.requestClosed.has(version.id)
      ? view.recreating?.id === version.id
        ? recreatePanel(chat, version)
        : requestPanel(version)
      : null,
    view.loras.has(version.id) && loraToggle(version) ? loraPanel(chat, version) : null,
  ];
}

/** The Request switch of a version the AI wrote: open until you close it. */
function requestToggle(version) {
  if (version.kind !== KIND_WRITTEN) return null;
  return metaToggle("Request", {
    open: !view.requestClosed.has(version.id),
    onToggle: flip(view.requestClosed, version.id),
    title: "What you asked for, its images, and the questions the AI asked on the way",
  });
}

/** What you asked for, the questions and answers on the way, and the images it sent. */
function requestPanel(version) {
  const line = (who, text, extra = "") => [
    el("span", { class: `wpw-who${extra}`, text: who }),
    el("span", { class: `wpw-said${extra}`, text }),
  ];
  return el(
    "div",
    { class: "wpw-panel wpw-request" },
    el(
      "div",
      { class: "wpw-said-grid" },
      ...(version.request
        ? line("You", version.request)
        : line("You", "Optimise the prompt", " is-empty")),
      ...(version.questions ?? []).flatMap((turn) => [
        ...line("AI", turn.question, " is-question"),
        ...line("You", turn.answer),
      ])
    ),
    stepTurns({ images: version.images })
  );
}

/**
 * Recreate: the request in a field where it was shown, as it was, to change
 * or not. Recreate sends it as a new version from the same version, with the
 * same images and the settings chosen now.
 */
function recreatePanel(chat, version) {
  const editing = view.recreating;
  const waiting = jobs.has(chat.id)
    ? "Wait for the write in progress to finish"
    : chat.pending
      ? "Answer the question below first"
      : null;
  const send = () => writeAgainNow(chat, version, { replace: false, request: editing.text });
  const input = el("textarea", {
    class: "wpw-recreate-input",
    rows: 1,
    value: editing.text,
    placeholder: "Optimise the prompt",
    "aria-label": `Request for a new version beside v${version.n}`,
    onInput: () => {
      editing.text = input.value;
      fitRecreate(input);
    },
    onKeydown: (event) => {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      if (!waiting) send();
    },
  });
  const go = button(
    "Recreate",
    send,
    "wpw-btn wpw-primary",
    waiting ?? "Write a new version (Ctrl+Enter)"
  );
  go.disabled = !!waiting;
  view.recreateInput = input;
  return el(
    "div",
    { class: "wpw-panel wpw-request is-editing" },
    input,
    stepTurns({ images: version.images }),
    el("div", { class: "wpw-recreate-row" }, button("Cancel", endRecreate), go)
  );
}

// Ten lines; a longer request scrolls in its field.
const RECREATE_MAX_HEIGHT = 10 * 19 + 14;

function fitRecreate(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight + 2, RECREATE_MAX_HEIGHT)}px`;
}

/** Recreate from the ⋮: the version opens with its request ready to change. */
function beginRecreate(chat, version) {
  if (jobs.has(chat.id) || chat.pending) return;
  flushEdits();
  view.requestClosed.delete(version.id);
  keepInPlace(version.id, () => {
    view.open = version.id;
    view.recreating = { id: version.id, text: version.request ?? "" };
    renderConvo(false);
  });
  const input = view.recreateInput;
  if (!input?.isConnected) return;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

/** Cancel, Esc, or another version opened: the request is shown again as it was. */
function endRecreate() {
  const id = view.recreating?.id;
  if (!id) return;
  keepInPlace(id, () => {
    view.recreating = null;
    renderConvo(false);
  });
}

/** The changes: marked in the text, or side by side when most of it is new. */
function changesView(chat, version) {
  const base = versionBase(chat, version);
  const comparison = compare(version, base.text);
  if (comparison.stats.rewrite) {
    const column = (name, text) =>
      el(
        "div",
        { class: "wpw-diff-col" },
        el("div", { class: "wpw-diff-col-name", text: name }),
        el("div", { text })
      );
    return el(
      "div",
      { class: "wpw-diff-cols" },
      column(base.name, base.text),
      column(`v${version.n}`, version.text)
    );
  }
  // What was there before sits small above what replaced it, or above a mark where it went.
  const change = (old, now) =>
    el(
      "span",
      { class: "wpw-diff-change", title: `Was: ${old}` },
      el("span", { class: "wpw-diff-old", text: old }),
      now
        ? el("span", { class: "wpw-diff-add", text: now })
        : el("span", { class: "wpw-diff-mark" })
    );
  // A comma or a dot that went is not worth a label.
  const worthShowing = (old) => /[\p{L}\p{N}]/u.test(old);
  const annotated = (parts) => {
    const out = [];
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (part.type === "same") out.push(part.text);
      else if (part.type === "add")
        out.push(el("span", { class: "wpw-diff-add", text: part.text }));
      else {
        const next = parts[i + 1];
        const old = part.text.trim();
        if (next?.type === "add") {
          out.push(
            worthShowing(old)
              ? change(old, next.text)
              : el("span", { class: "wpw-diff-add", text: next.text })
          );
          i++;
        } else if (worthShowing(old)) {
          out.push(change(old, ""));
        }
      }
    }
    return out;
  };
  const line = (text) => text || " ";
  comparison.rows ??= diffLines(base.text, version.text);
  const rows = comparison.rows.map((row) => {
    if (row.kind === "same")
      return el("div", { class: "wpw-diff-row", text: line(row.now[0].text) });
    if (row.kind === "added") {
      return el("div", { class: "wpw-diff-row is-added", text: line(row.now[0].text) });
    }
    if (row.kind === "removed") {
      return el("div", { class: "wpw-diff-row is-removed", text: line(row.was[0].text) });
    }
    const pieces = annotated(row.parts);
    const hasOld = pieces.some((piece) => piece.classList?.contains("wpw-diff-change"));
    return el(
      "div",
      { class: hasOld ? "wpw-diff-row is-changed has-old" : "wpw-diff-row is-changed" },
      ...pieces
    );
  });
  const diff = el("div", { class: "wpw-diff" }, ...rows);
  requestAnimationFrame(() => keepLabelsInside(diff));
  return diff;
}

/** A label that would run past the right edge hangs to the left of its change instead. */
function keepLabelsInside(diff) {
  if (!diff.isConnected) return;
  const edge = diff.getBoundingClientRect().right - 8;
  for (const label of diff.querySelectorAll(".wpw-diff-old")) {
    if (label.getBoundingClientRect().right > edge) label.classList.add("is-flipped");
  }
}

/** A closed row's line: what was asked, or for a hand edit how many words it changed. */
function rowLine(chat, version) {
  const text = rowText(version);
  const base = version.kind === KIND_HAND ? versionBase(chat, version) : null;
  if (!base) return text;
  const { stats } = compare(version, base.text);
  return `${text} · +${stats.added} −${stats.removed}`;
}

/** Why this version cannot be deleted now: something under way starts from it or its follow-ups. */
function deleteBlock(chat, version) {
  const ids = new Set(branchOf(chat, version.id).map((v) => v.id));
  const touches = (step) => !!step && (ids.has(step.baseId) || ids.has(step.replaceId));
  if (touches(jobs.get(chat.id))) return "A write in progress starts from it. Stop it first.";
  if (touches(chat.pending))
    return "The question below starts from it. Answer it or take it back first.";
  if (touches(chat.failure))
    return "The failed request below starts from it. Retry it or take it back first.";
  return null;
}

/** One line of a version's menu: an icon, what it does, and a quiet line saying more. */
function menuItem(name, label, { sub = null, onClick, disabled = false, danger = false }) {
  return el(
    "button",
    {
      type: "button",
      class: `wpw-vmenu-item${danger ? " is-danger" : ""}`,
      role: "menuitem",
      disabled,
      onClick,
    },
    icon(name),
    el(
      "span",
      { class: "wpw-vmenu-words" },
      el("span", { class: "wpw-vmenu-label", text: label }),
      sub && el("span", { class: "wpw-vmenu-sub", text: sub })
    )
  );
}

/**
 * The ⋮ menu of a version: its generation info and using it as the input;
 * Retry and Recreate side by side, each saying what it does; and deleting it,
 * which asks first in the menu itself.
 */
function openVersionMenu(chat, version, anchor) {
  if (view.popover?.versionId === version.id) {
    closePopover();
    return;
  }
  const n = version.n;
  const written = version.kind === KIND_WRITTEN;
  // An answer to a question is typed in the input, so a request cannot go out meanwhile.
  const waiting = jobs.has(chat.id)
    ? "Wait for the write in progress to finish"
    : chat.pending
      ? "Answer the question below first"
      : null;
  const chosen = startValue(chat, draftOf(chat.id).base) === version.id;
  const followed = hasFollowUps(chat, version.id);
  const more = branchOf(chat, version.id).length - 1;
  const blocked = deleteBlock(chat, version);
  const root = el("div", { class: "wpw-pop wpw-vmenu", role: "menu" });
  const act = (work) => () => {
    closePopover();
    work();
  };
  const rule = () => el("div", { class: "wpw-vmenu-rule" });

  const items = () => [
    menuItem("pi-info-circle", "Generation info", {
      sub: "Request, prompts, model, cost and thinking",
      onClick: act(() => openInfo(chat, version)),
    }),
    menuItem(chosen ? "pi-check" : "pi-pencil", chosen ? "Used as input" : "Use as input", {
      sub: chosen
        ? `Your next request edits v${n}`
        : `Your next request edits v${n}. Sends nothing`,
      disabled: chosen,
      onClick: act(() => continueFrom(chat, version)),
    }),
    written && rule(),
    written &&
      menuItem("pi-refresh", "Retry", {
        sub:
          waiting ??
          (followed
            ? `Versions were made from v${n}: use Recreate`
            : `Write it again and replace v${n}`),
        disabled: !!waiting || followed,
        onClick: act(() => writeAgainNow(chat, version, { replace: true })),
      }),
    written &&
      menuItem("pi-clone", "Recreate", {
        sub: waiting ?? `Change the request if you like, then a new version. v${n} stays`,
        disabled: !!waiting,
        onClick: act(() => beginRecreate(chat, version)),
      }),
    rule(),
    menuItem("pi-trash", `Delete v${n}`, {
      sub: blocked ?? (more ? `With the ${versionsWord(more)} made from it` : "Asks first"),
      disabled: !!blocked,
      danger: true,
      onClick: () => draw(confirm),
    }),
  ];
  const confirm = () => [
    el("div", {
      class: "wpw-vmenu-confirm",
      text: `${more ? `Delete v${n} and the ${versionsWord(more)} made from it?` : `Delete v${n}?`} This cannot be undone.`,
    }),
    el(
      "div",
      { class: "wpw-vmenu-buttons" },
      button("Keep", () => draw(items)),
      button(
        "Delete",
        act(() => removeVersion(chat, version)),
        "wpw-btn wpw-danger"
      )
    ),
  ];
  // Under the ⋮, or above it when there is no room below.
  const place = () => {
    const at = anchor.getBoundingClientRect();
    const box = root.getBoundingClientRect();
    const below = at.bottom + 4 + box.height <= window.innerHeight - 8;
    root.style.top = `${below ? at.bottom + 4 : Math.max(8, at.top - 4 - box.height)}px`;
    root.style.left = `${Math.max(8, Math.min(at.right - box.width, window.innerWidth - box.width - 8))}px`;
  };
  const draw = (make) => {
    root.replaceChildren(...make().filter(Boolean));
    place();
  };
  openPopover(anchor, root);
  view.popover.versionId = version.id;
  anchor.classList.add("is-active");
  draw(items);
}

/** The open version: the one you opened, else the newest; none once you closed it. */
const NONE_OPEN = "none";

function openVersionId(chat) {
  if (view.open === NONE_OPEN) return null;
  if (view.open && chat.versions.some((v) => v.id === view.open)) return view.open;
  return chat.versions.at(-1)?.id ?? null;
}

/**
 * Whatever opens or closes, the card you pressed stays where it is on the
 * screen: the conversation scrolls by what moved above it.
 */
function keepInPlace(id, change) {
  const top = () =>
    view?.convo.querySelector(`.wpw-card[data-id="${CSS.escape(id)}"]`)?.getBoundingClientRect()
      .top;
  const before = top();
  change();
  const after = top();
  if (before != null && after != null) view.convo.scrollTop += after - before;
}

/** One version open at a time: opening one closes the one that was open. */
function toggleOpen(chat, version) {
  const next = openVersionId(chat) === version.id ? NONE_OPEN : version.id;
  keepInPlace(version.id, () => {
    view.open = next;
    // Closing the card you were recreating from lets the change go.
    view.recreating = null;
    renderConvo(false);
  });
}

/**
 * A version: closed, one line with its number and title; open, a card with
 * the changes and LoRAs above the prompt, and Send to ComfyUI under it. Its ⋮
 * holds everything else. Only one is open at a time; the newest open is
 * editable.
 */
function versionCard(chat, version, newest, keep) {
  const written = version.kind === KIND_WRITTEN;
  const open = openVersionId(chat) === version.id;
  const menu = iconButton(
    "pi-ellipsis-v",
    (event) => {
      event.stopPropagation();
      openVersionMenu(chat, version, menu);
    },
    "wpw-icon wpw-card-menu",
    `More for v${version.n}: generation info, retry, recreate, delete`
  );
  const head = cardHead({
    label: `v${version.n}`,
    text: rowLine(chat, version),
    empty: written && !version.title && !version.request,
    folded: foldedTag(chat, version, keep),
    menu,
  });
  head.classList.add("is-toggle");
  head.addEventListener("click", () => toggleOpen(chat, version));
  if (!open) return el("div", { class: "wpw-card", "data-id": version.id }, head);

  let body;
  if (view.changes.has(version.id) && versionBase(chat, version)) {
    body = changesView(chat, version);
  } else if (newest) {
    const entry = { chat, version, editor: null };
    body = el("textarea", {
      class: "wpw-editor",
      value: version.text,
      spellcheck: false,
      onInput: () => fitEditor(body),
      onBlur: () => saveEdit(entry),
    });
    entry.editor = body;
    view.editors.push(entry);
  } else {
    body = el("div", { class: "wpw-text", text: version.text });
  }
  return el(
    "div",
    { class: "wpw-card is-open", "data-id": version.id },
    head,
    ...metaParts(chat, version),
    body,
    el("div", { class: "wpw-actions is-block" }, sendButton(version))
  );
}

// The card of what is not a version yet: a write in progress, a question or a failure.
const NEXT = "next";
const LANE = 14;
// The first lane's centre, from the left edge of the lanes.
const GRAPH_EDGE = 8;
// The column of fold controls, left of the lanes.
const FOLD_COLUMN = 20;
const CONVO_WIDTH = 780;
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function renderConvo(stick) {
  flushEdits();
  const chat = currentChat();
  // Drawn again while you type in Recreate's field, the field keeps the caret.
  const typing =
    document.activeElement === view.recreateInput
      ? [view.recreateInput.selectionStart, view.recreateInput.selectionEnd]
      : null;
  view.recreateInput = null;
  view.editors = [];
  view.live = null;
  view.graph = null;
  view.graphWatch.disconnect();
  if (!chat) {
    view.convo.replaceChildren();
    return;
  }
  refreshTargets();
  const job = jobs.get(chat.id);
  const failure = chat.failure;
  const newest = chat.versions.at(-1);
  // Folding never hides where you are working: the newest version, the one the next
  // request edits, what a write, question or failure starts from, and their paths.
  const keep = keptPaths(chat, [
    newest?.id,
    startValue(chat, draftOf(chat.id).base),
    job?.baseId,
    job?.replaceId,
    chat.pending?.baseId,
    chat.pending?.replaceId,
    failure?.baseId,
    failure?.replaceId,
  ]);
  // A Retry writes in the place of the version it writes over.
  const inPlace = job?.replaceId ?? null;
  const entries = visibleVersions(chat, keep).map((version) => ({
    id: version.id,
    baseId: version.baseId,
    kind: version.id === inPlace ? NEXT : version.kind,
    card:
      version.id === inPlace
        ? liveCard(chat, job, `v${version.n}`)
        : versionCard(chat, version, version === newest, keep),
    toggle: foldToggle(chat, version, keep),
  }));
  const coming = (baseId, card) => entries.push({ id: NEXT, baseId, kind: NEXT, card });
  const next = `v${nextVersionNumber(chat)}`;
  // What a failed Retry would have written over: its number, not a new one.
  const failedOver = chat.versions.find((v) => v.id === failure?.replaceId);
  if (job) {
    if (!entries.some((entry) => entry.id === inPlace))
      coming(job.baseId, liveCard(chat, job, next));
  } else if (failure) {
    coming(
      failure.baseId,
      el(
        "div",
        { class: "wpw-card is-open is-error" },
        cardHead({
          label: failedOver ? `v${failedOver.n}` : next,
          ...(failedOver?.title ? { text: failedOver.title } : requestText(failure)),
          status: el("span", { class: "wpw-status", text: failedOver ? "Retry failed" : "Failed" }),
          rewind: rewindButton(() => rewindFailure(chat), REWIND_TITLE, true),
        }),
        stepTurns(failure),
        el("div", { class: "wpw-error-text", text: failure.message }),
        el(
          "div",
          { class: "wpw-actions is-block" },
          labelledButton(
            "pi-refresh",
            "Retry",
            () => launch(chat, retrySpec(chat, failure)),
            "wpw-btn wpw-primary"
          )
        )
      )
    );
  } else if (chat.pending) {
    coming(
      chat.pending.baseId,
      el(
        "div",
        { class: "wpw-card is-open is-question" },
        cardHead({
          label: next,
          ...requestText(chat.pending),
          status: el("span", { class: "wpw-status", text: "Waiting for your answer" }),
          rewind: rewindButton(() => rewindQuestion(chat), REWIND_TITLE, true),
        }),
        stepTurns(chat.pending, chat.pending.question),
        el("div", {
          class: "wpw-hint-line",
          text: "Answer in the input below, or take your request back with the undo arrow.",
        })
      )
    );
  }

  const top = view.convo.scrollTop;
  if (entries.length) {
    const svg = svgEl("svg", { class: "wpw-graph", "aria-hidden": "true" });
    const history = el(
      "div",
      { class: "wpw-history" },
      svg,
      ...entries.map((e) => e.toggle),
      ...entries.map((e) => e.card)
    );
    view.graph = { chat, entries, history, svg };
    view.convo.replaceChildren(history);
    view.graphWatch.observe(history);
  } else {
    view.convo.replaceChildren(
      el("div", {
        class: "wpw-hint",
        text: "Describe the prompt you want. To change the prompt that is in the node, choose Edit current prompt below.",
      })
    );
    alignComposer();
  }
  for (const { editor } of view.editors) fitEditor(editor);
  const field = view.recreateInput;
  if (field?.isConnected) {
    fitRecreate(field);
    if (typing) {
      field.focus({ preventScroll: true });
      field.setSelectionRange(...typing);
    }
  }
  drawGraph();
  view.convo.scrollTop = stick ? view.convo.scrollHeight : top;
}

let graphFrame = null;

/** Cards change height as they open, stream and are typed in; the graph follows once a frame. */
function scheduleGraph() {
  graphFrame ??= requestAnimationFrame(() => {
    graphFrame = null;
    drawGraph();
  });
}

/**
 * The graph beside the cards, measured from where their first lines are now:
 * a dot per version (filled: written, hollow: by hand, square: from the node),
 * a line to each version made from it, and the next request's version and its
 * path back to the start in the accent colour.
 */
function drawGraph() {
  const graph = view?.graph;
  if (!graph?.history.isConnected) return;
  const { chat, entries, history, svg } = graph;
  const layout = historyLayout(entries);
  // The fold controls get a column of their own, left of the lanes.
  const folds = entries.some((e) => e.toggle) ? FOLD_COLUMN : 0;
  const width = folds + GRAPH_EDGE * 2 + Math.max(0, layout.lanes - 1) * LANE;
  history.style.paddingLeft = `${width + 6}px`;
  history.style.maxWidth = `${CONVO_WIDTH + width + 6}px`;
  const box = history.getBoundingClientRect();
  // At the middle of a card's first line, which an open card's request may run past.
  const ys = entries.map(({ card }) => {
    const rect = (card.querySelector(".wpw-card-head") ?? card).getBoundingClientRect();
    return rect.top - box.top + Math.min(rect.height / 2, HEAD_LINE / 2);
  });
  entries.forEach(({ toggle }, i) => {
    if (toggle) toggle.style.top = `${ys[i] - 8}px`;
  });
  const x = (lane) => folds + GRAPH_EDGE + lane * LANE;
  const chosen = startValue(chat, draftOf(chat.id).base);
  const path = pathTo(chat, chosen);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(box.height));
  const edges = [];
  const dots = [];
  layout.rows.forEach((row, i) => {
    const kind = entries[i].kind;
    const mark = kind === NEXT ? " is-next" : path.has(row.id) ? " is-path" : "";
    const cx = x(row.lane);
    const cy = ys[i];
    if (row.parentRow != null) {
      const x1 = x(layout.rows[row.parentRow].lane);
      const y1 = ys[row.parentRow];
      const d =
        x1 === cx ? `M${x1} ${y1}V${cy}` : `M${x1} ${y1}Q${cx} ${y1} ${cx} ${y1 + LANE}V${cy}`;
      edges.push(svgEl("path", { class: `wpw-edge${mark}`, d }));
    }
    const cls = `wpw-node${mark}${kind === KIND_HAND ? " is-hand" : ""}`;
    const dot =
      kind === KIND_NODE
        ? svgEl("rect", { class: cls, x: cx - 4, y: cy - 4, width: 8, height: 8, rx: 1.5 })
        : svgEl("circle", { class: cls, cx, cy, r: 4.5 });
    const version = kind === NEXT ? null : chat.versions.find((v) => v.id === row.id);
    if (!version) {
      dots.push(dot);
      return;
    }
    // A version's dot chooses it as the input of the next request; the chosen one has a ring.
    const pick = svgEl("g", { class: `wpw-pick${row.id === chosen ? " is-chosen" : ""}` });
    const tip = svgEl("title");
    tip.textContent =
      row.id === chosen
        ? `v${version.n} is the input: your next request edits it`
        : `Use v${version.n} as the input of your next request`;
    pick.append(
      tip,
      svgEl("circle", { class: "wpw-ring", cx, cy, r: 8 }),
      dot,
      svgEl("circle", { class: "wpw-hit", cx, cy, r: 10 })
    );
    pick.addEventListener("click", () => continueFrom(chat, version));
    dots.push(pick);
  });
  svg.replaceChildren(...edges, ...dots);
  alignComposer(width + 6);
}

/**
 * The input lines up with the cards above it: the same left edge past the
 * graph, and the same right edge however wide the scroll bar is.
 */
function alignComposer(gutter = 0) {
  const scrollbar = view.convo.offsetWidth - view.convo.clientWidth;
  view.composer.style.paddingLeft = `${COMPOSER_PAD + gutter}px`;
  view.composer.style.paddingRight = `${COMPOSER_PAD + scrollbar}px`;
}

function chip(className, children, onClear, clearTitle) {
  return el(
    "span",
    { class: `wpw-chip ${className}` },
    ...children,
    onClear &&
      el("button", {
        type: "button",
        class: "wpw-chip-x",
        text: "×",
        title: clearTitle,
        "aria-label": clearTitle,
        onClick: onClear,
      })
  );
}

/** The options are rebuilt only when they change, so a re-render cannot close an open list. */
function renderStart(chat, draft) {
  // One version on offer: the chosen one. Another is chosen by its dot in the graph.
  const options = startOptions(chat, targetNow()?.label, draft.base);
  const key = options.map((option) => `${option.value}\t${option.label}`).join("\n");
  if (view.start.dataset.options !== key) {
    view.start.dataset.options = key;
    view.start.replaceChildren(
      ...options.map((option) => el("option", { value: option.value, text: option.label }))
    );
  }
  view.start.value = startValue(chat, draft.base);
  view.startIcon.className = `pi ${view.start.value === NEW_PROMPT ? "pi-sparkles" : "pi-pencil"}`;
  // The graph lights up the chosen version and its path.
  scheduleGraph();
}

function chooseStart() {
  const chat = currentChat();
  if (!chat) return;
  const value = view.start.value;
  draftOf(chat.id).base = value;
  // A version picked from under a fold stays shown after the next pick too.
  const folded =
    chat.versions.some((v) => v.id === value) && !visibleVersions(chat).some((v) => v.id === value);
  if (folded) {
    unfoldTo(chat, value);
    save();
  }
  // What folding keeps shown follows the choice.
  renderConvo(false);
  renderStart(chat, draftOf(chat.id));
}

function renderChips() {
  const chat = currentChat();
  if (!chat) {
    view.attached.replaceChildren();
    view.toolsLeft.replaceChildren();
    return;
  }
  const draft = draftOf(chat.id);
  // An answer goes with the request's own images and starting point.
  const answering = !!chat.pending;
  const images = answering
    ? []
    : draft.images.map((name) =>
        chip(
          "",
          [el("img", { src: thumbUrl(name), alt: "", title: name })],
          () => {
            draft.images = draft.images.filter((other) => other !== name);
            renderChips();
          },
          "Remove this image"
        )
      );
  const uploading = draft.uploading > 0 ? [chip("", ["Uploading…"])] : [];
  renderStart(chat, draft);
  view.attached.replaceChildren(...images, ...uploading);
  const answeringChip = chip("is-base", [icon("pi-question-circle"), "Answering the question"]);
  const tools = [
    !answering && view.attach,
    answering ? answeringChip : view.startChip,
    view.loraScope,
  ];
  view.toolsLeft.replaceChildren(...tools.filter(Boolean));
  renderLoraScope();
}

const LORA_SCOPE_TITLES = {
  off: "The LLM is told nothing about LoRAs.",
  list: "The LLM may suggest LoRAs switched off in this node's LoRA list.",
  all: "The LLM may suggest any of your LoRAs for this model.",
};

function drawLoraScope(counts) {
  const shown = loraScopeCounts(counts);
  const names = { off: "Off", list: "List", all: "All" };
  const scope = loraScope();
  for (const [key, control] of view.loraScopeButtons) {
    const count = key === "off" ? [] : [el("span", { class: "wpw-seg-count", text: shown[key] })];
    control.replaceChildren(names[key], ...count);
    control.title = LORA_SCOPE_TITLES[key];
    control.classList.toggle("is-on", key === scope);
  }
}

/** The counts follow the target, its model and what is in its boxes; the same ones are not counted twice. */
let loraCounts = { key: null, counts: undefined };

function loraCountsKey(node) {
  if (!node) return "";
  const values = (node.widgets ?? [])
    .filter((w) => BOX_WIDGETS.includes(w.name) || w.name === "loras_2_on")
    .map((w) => String(w.value ?? ""));
  return JSON.stringify([node.id, connectedModelName(node), ...values]);
}

async function renderLoraScope() {
  const node = view.targetNode;
  const key = loraCountsKey(node);
  if (loraCounts.key === key && loraCounts.counts) {
    drawLoraScope(loraCounts.counts);
    return;
  }
  const run = ++loraRun;
  const [list, all] = await Promise.all([loraListsFor(node, "list"), loraListsFor(node, "all")]);
  if (!view || run !== loraRun) return;
  const counts = { list: list?.available.length ?? 0, all: all?.available.length ?? 0 };
  // A failed read is asked again next time.
  loraCounts = list && all ? { key, counts } : { key: null, counts: undefined };
  drawLoraScope(counts);
}

function chooseLoraScope(scope) {
  setSetting(SETTING_LORA_SCOPE, scope);
  drawLoraScope(undefined);
  renderLoraScope();
}

function renderSend() {
  const job = jobs.get(view.chatId);
  const answering = !!currentChat()?.pending;
  view.send.classList.toggle("is-stop", !!job);
  view.sendIcon.className = `pi ${job ? "pi-stop" : "pi-arrow-up"}`;
  const label = job ? "Stop" : `${answering ? "Answer" : "Write"} (Ctrl+Enter)`;
  view.send.title = label;
  view.send.setAttribute("aria-label", label);
  view.input.placeholder = answering ? PLACEHOLDER_ANSWER : PLACEHOLDER_WRITE;
}

function renderNote() {
  const note = view.chatId ? draftOf(view.chatId).note : null;
  view.note.textContent = note?.text ?? "";
  view.note.classList.toggle("is-error", !!note?.isError);
}

function growInput() {
  const input = view.input;
  const least = INPUT_MIN_LINES * INPUT_LINE + INPUT_CHROME;
  const most = INPUT_MAX_LINES * INPUT_LINE + INPUT_CHROME;
  input.style.height = "auto";
  const wanted = input.scrollHeight + 2;
  input.style.height = `${Math.min(most, Math.max(least, wanted))}px`;
  input.style.overflowY = wanted > most ? "auto" : "hidden";
}

function fitEditor(editor) {
  editor.style.height = "auto";
  editor.style.height = `${Math.min(editor.scrollHeight + 2, window.innerHeight * 0.45)}px`;
}

/**
 * Typed text goes to its version, or into a hand edit under it (editVersion
 * decides). A new hand edit takes the composer's choice with it, and the
 * conversation is drawn again only once the press that ended the typing is
 * over, so the button it pressed still gets its click.
 */
function saveEdit(entry) {
  const { chat, version, editor } = entry;
  if (editor.value === version.text) return;
  const out = editVersion(chat, version, editor.value, {
    id: uid(),
    now: Date.now(),
    writingBaseId: jobs.get(chat.id)?.baseId ?? null,
  });
  if (!out) {
    editor.value = version.text;
    fitEditor(editor);
    return;
  }
  if (out.made) {
    const draft = draftOf(chat.id);
    if (draft.base === version.id) draft.base = out.version.id;
    view.successor.set(version.id, out.version.id);
    entry.version = out.version;
    renderAfterPress();
  }
  save();
}

function flushEdits() {
  for (const entry of view?.editors ?? []) saveEdit(entry);
}

/** The version that holds a version's text now: the hand edit typing made of it, if any. */
function current(chat, version) {
  let id = version.id;
  while (view?.successor.has(id)) id = view.successor.get(id);
  return chat.versions.find((v) => v.id === id) ?? version;
}

// Whether a mouse button or a finger is down, anywhere.
let pressing = false;
window.addEventListener("pointerdown", () => (pressing = true), true);
window.addEventListener("pointerup", () => (pressing = false), true);
window.addEventListener("pointercancel", () => (pressing = false), true);

function renderAfterPress() {
  const later = () =>
    setTimeout(() => {
      if (!view) return;
      renderConvo(false);
      renderChips();
    }, 0);
  if (pressing) window.addEventListener("pointerup", later, { once: true, capture: true });
  else later();
}

// --- What the controls do ------------------------------------------------------

/**
 * A chat that has written nothing and holds nothing typed is for the node the
 * Prompt Writer was opened from, as New chat would make it. One that has a
 * version, or a draft, keeps the node it was for.
 */
function followOpenedNode(chat) {
  const draft = drafts.get(chat.id);
  const untouched =
    !chat.versions.length &&
    !chat.pending &&
    !chat.failure &&
    !jobs.has(chat.id) &&
    !chat.seedPrompt &&
    !draft?.text?.trim() &&
    !draft?.images?.length;
  const label = labelOfNode(view.promptNode);
  if (!untouched || !label || chat.targetLabel === label) return;
  chat.targetLabel = label;
  save();
}

function selectChat(id) {
  flushEdits();
  view.chatId = id;
  view.confirmDelete = null;
  // A chat opens on its newest version.
  view.open = null;
  view.recreating = null;
  closeInfo();
  const chosen = store.chats.find((chat) => chat.id === id);
  if (chosen) followOpenedNode(chosen);
  if (unseen.has(id)) {
    const node = unseen.get(id);
    unseen.delete(id);
    syncButton(node);
  }
  view.input.value = draftOf(id).text;
  growInput();
  renderTitle();
  renderRail();
  renderConvo(true);
  renderChips();
  renderSend();
  renderNote();
}

/**
 * A chat with nothing in it is reused, so "New chat" cannot pile them up. One
 * that holds a draft or a seed is only reused for its own target: those were
 * written for that node.
 */
function reusableChat(label) {
  const empty = sortedChats(store).filter(
    (chat) => !chat.versions.length && !chat.pending && !jobs.has(chat.id) && !chat.failure
  );
  const untouched = (chat) => {
    const draft = drafts.get(chat.id);
    return !chat.seedPrompt && !draft?.text && !draft?.images.length && !draft?.uploading;
  };
  return empty.find((chat) => chat.targetLabel === label) ?? empty.find(untouched) ?? null;
}

function newChat() {
  const label = labelOfNode(view.promptNode);
  const chat =
    reusableChat(label) ??
    createChat(store, { id: uid(), now: Date.now(), seedPrompt: null, targetLabel: label });
  chat.targetLabel = label;
  save();
  selectChat(chat.id);
  view.input.focus();
}

function removeChat(chat) {
  const job = jobs.get(chat.id);
  if (job) {
    job.dropped = true;
    job.controller.abort();
  }
  const node = unseen.get(chat.id);
  unseen.delete(chat.id);
  drafts.delete(chat.id);
  syncButton(node);
  deleteChat(store, chat.id);
  save();
  view.confirmDelete = null;
  if (view.chatId !== chat.id) {
    renderRail();
    return;
  }
  view.editors = [];
  const next = sortedChats(store)[0];
  if (next) selectChat(next.id);
  else newChat();
}

/**
 * Use as input: the version is chosen under "Edit …" in the input, and that
 * choice lights up. Nothing is sent.
 */
function continueFrom(chat, version) {
  flushEdits();
  // The hand edit typing just made, if it made one.
  draftOf(chat.id).base = current(chat, version).id;
  renderConvo(false);
  renderChips();
  const chip = view.startChip;
  chip.classList.remove("is-flash");
  // Read once, so the animation starts again on a second press.
  void chip.offsetWidth;
  chip.classList.add("is-flash");
  view.input.focus();
}

/** A version goes, with every version made from it; deleteBlock said nothing depends on them. */
function removeVersion(chat, version) {
  flushEdits();
  if (deleteBlock(chat, version)) {
    renderConvo(false);
    return;
  }
  const gone = deleteVersion(chat, version.id, Date.now());
  const draft = draftOf(chat.id);
  if (gone.includes(draft.base)) draft.base = null;
  if (gone.includes(view.open)) view.open = null;
  if (gone.includes(view.recreating?.id)) view.recreating = null;
  for (const id of gone) {
    for (const set of [view.changes, view.thinking, view.loras, view.requestClosed]) set.delete(id);
    view.successor.delete(id);
    comparisons.delete(id);
  }
  save();
  renderRail();
  renderConvo(false);
  renderChips();
}

function targetNow() {
  return view.targets[targetIndex()] ?? null;
}

function useVersion(pressed) {
  flushEdits();
  const chat = currentChat();
  const version = chat ? current(chat, pressed) : pressed;
  const target = targetNow();
  const widget = target?.node?.widgets?.find((w) => w.name === TEXT_WIDGET);
  if (!widget) {
    setNote(view.chatId, "That node has no text box to write into.", true);
    return;
  }
  if (!version.text.trim()) {
    setNote(view.chatId, "This version is empty.", true);
    return;
  }
  widget.value = version.text;
  widget.callback?.(version.text);
  markSent(version, Date.now());
  save();
  flash(target.node);
  redraw();
  checkpoint();
  toast("success", `v${version.n} sent to ComfyUI`, target.label);
  closeView();
}

/** The textarea showing a widget, if one is on screen. Nodes 2.0 gives the widget no handle. */
function textareaOf(node, widget) {
  const own = widget.inputEl ?? widget.element;
  if (own?.tagName === "TEXTAREA" && own.isConnected) return own;
  const root = document.querySelector(`[data-node-id="${node.id}"]`);
  const shown = (node.widgets ?? []).filter((w) => BOX_WIDGETS.includes(w.name) && !w._wpeHidden);
  return root?.querySelectorAll("textarea")[shown.indexOf(widget)] ?? null;
}

/** The node colours its tags on `input`, which setting a widget's value does not fire. */
function writeLoraList(node, widget, value) {
  widget.value = value;
  widget.callback?.(value);
  const area = textareaOf(node, widget);
  if (!area) return;
  // A framework's listener reads the box back, so the box has to hold the new text first.
  area.value = value;
  area.dispatchEvent(new Event("input", { bubbles: true }));
}

/** The open editor is written too, or re-rendering would save its old text back. */
function setVersionText(version, text) {
  version.text = text;
  const open = view.editors.find((item) => item.version === version);
  if (open) open.editor.value = text;
}

/**
 * The only way a suggestion reaches a list: pressing its + adds it, pressing again
 * removes it. `keepText` leaves the version alone: a LoRA that was on when it was
 * written has its trigger words in there already, where the LLM put them.
 */
function toggleSuggestion(chat, version, suggestion, { keepText = false } = {}) {
  flushEdits();
  refreshTargets();
  const target = targetNow();
  const widgets = targetLoraWidgets();
  if (!widgets.length) {
    setNote(chat.id, "That node has no LoRA list to add to.", true);
    return;
  }
  const { tag, strength, triggers } = suggestion;
  const holder = widgets.find((w) => isLoraListed(w.value, tag));
  if (holder) {
    // Text this added goes away again; a line that was already there is only switched off.
    const value = String(holder.value ?? "");
    const next =
      suggestion.addedHow === "appended" ? removeLoraTag(value, tag) : disableLoraTag(value, tag);
    writeLoraList(target.node, holder, next);
    delete suggestion.addedHow;
  } else {
    const off = widgets
      .map((w) => ({ w, next: enableLoraTag(w.value, tag) }))
      .find((item) => item.next !== null);
    if (off) {
      writeLoraList(target.node, off.w, off.next);
      suggestion.addedHow = "enabled";
    } else {
      writeLoraList(target.node, widgets[0], appendLoraTag(widgets[0].value, tag, strength));
      suggestion.addedHow = "appended";
    }
  }
  if (!keepText) {
    // Into the hand edit typing made of this version, if it made one.
    const into = current(chat, version);
    const text = (holder ? removeTriggerLine : addTriggerLine)(into.text, triggers);
    if (text !== into.text) setVersionText(into, text);
  }
  save();
  redraw();
  checkpoint();
  renderConvo(false);
  renderChips();
}

function beginRename() {
  const chat = currentChat();
  if (!chat || view.renaming) return;
  let done = false;
  const finish = (commit) => {
    if (done || !view) return;
    done = true;
    view.renaming = null;
    input.replaceWith(view.title);
    const title = input.value.trim();
    if (commit && title && title !== chat.title) {
      renameChat(chat, title);
      save();
      renderRail();
    }
    renderTitle();
  };
  const input = el("input", {
    class: "wpw-title-input",
    value: chat.title ?? "",
    maxLength: 120,
    onBlur: () => finish(true),
    onKeydown: (event) => {
      if (event.key === "Enter") finish(true);
    },
  });
  view.renaming = { cancel: () => finish(false) };
  view.title.replaceWith(input);
  input.focus();
  input.select();
}

function closePopover() {
  if (!view?.popover) return;
  view.popover.anchor?.classList.remove("is-active");
  view.popover.root.remove();
  view.popover = null;
  view.model = view.prompts = null;
}

function openPopover(anchor, root) {
  closePopover();
  view.popover = { anchor, root };
  view.modal.append(root);
}

let peekRun = 0;

/** Show the selected system prompt, read-only, under its field in the settings. */
async function showPeek(panel) {
  panel.peek?.remove();
  const name = view.prompts?.value ?? "";
  const text = el("div", { class: "wpw-peek-text", text: "Loading…" });
  panel.peek = text;
  panel.promptRow.after(text);
  const run = ++peekRun;
  let out;
  try {
    out = await getJson(`/warppipe/llm/system_prompt?name=${encodeURIComponent(name)}`);
  } catch (error) {
    out = { error: String(error?.message ?? error) };
  }
  if (run !== peekRun) return;
  text.textContent = out?.error ?? out?.text ?? "";
}

function togglePeek(panel) {
  if (!panel.peek) {
    showPeek(panel);
    return;
  }
  panel.peek.remove();
  panel.peek = null;
}

/** The model list opens under the model field: type to filter, Enter takes the first. */
function toggleModels(panel) {
  if (panel.models) {
    panel.models.remove();
    panel.models = null;
    return;
  }
  const options = el("div", { class: "wpw-options" });
  const current = String(getSetting(SETTING_MODEL, ""));
  const matches = () => {
    const terms = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return lists.models.filter((model) => {
      const hay = `${modelLabel(model)} ${model.id}`.toLowerCase();
      return terms.every((term) => hay.includes(term));
    });
  };
  const choose = (model) => {
    setSetting(SETTING_MODEL, model.id);
    toggleModels(panel);
    renderModelButton();
    view.model?.focus();
  };
  const draw = () => {
    const rows = matches().map((model) =>
      el(
        "button",
        {
          type: "button",
          class: `wpw-option${model.id === current ? " is-current" : ""}`,
          title: model.id,
          onClick: () => choose(model),
        },
        el("span", { class: "wpw-option-name", text: model.name || model.id }),
        el("span", { class: "wpw-option-price", text: modelPrice(model) }),
        model.vision && icon("pi-eye")
      )
    );
    if (rows.length) options.replaceChildren(...rows);
    else options.replaceChildren(el("div", { class: "wpw-none", text: "No model matches." }));
  };
  const filter = el("input", {
    class: "wpw-field wpw-filter",
    type: "search",
    placeholder: `Filter ${lists.models.length} models`,
    onInput: draw,
    onKeydown: (event) => {
      if (event.key !== "Enter") return;
      const first = matches()[0];
      if (first) choose(first);
    },
  });
  panel.models = el("div", { class: "wpw-models" }, filter, options);
  panel.modelRow.after(panel.models);
  draw();
  filter.focus();
  options.querySelector(".is-current")?.scrollIntoView({ block: "center" });
}

async function refreshKeyState(state) {
  const out = await getJson(
    `/warppipe/llm/key_status?base_url=${encodeURIComponent(baseUrl())}`
  ).catch((error) => ({ error: String(error?.message ?? error) }));
  if (out?.error) {
    state.textContent = out.error;
    state.className = "wpw-key-state";
    return;
  }
  state.textContent = out.set ? "Key set" : "No key set";
  state.title = out.set && out.source ? `From ${out.source}` : "";
  state.className = `wpw-key-state${out.set ? " is-set" : ""}`;
}

/** One row of the settings: a name, and what sets it. */
function settingRow(name, ...controls) {
  return el(
    "div",
    { class: "wpw-set" },
    el("span", { class: "wpw-set-name", text: name }),
    el("div", { class: "wpw-set-control" }, ...controls)
  );
}

/** Everything that is set once and then left alone: the model, the system prompt, the server. */
function toggleGear() {
  if (view.popover?.anchor === view.gear) {
    closePopover();
    return;
  }
  const panel = { models: null, peek: null };
  const state = el("div", { class: "wpw-key-state" });
  const model = el("button", {
    type: "button",
    class: "wpw-field wpw-combo",
    onClick: () => toggleModels(panel),
  });
  const prompts = el("select", {
    class: "wpw-field wpw-prompts",
    onChange: () => {
      setSetting(SETTING_SYSTEM_PROMPT, prompts.value);
      if (panel.peek) showPeek(panel);
    },
  });
  const eye = iconButton("pi-eye", () => togglePeek(panel), "wpw-icon", "Read this system prompt");
  const url = el("input", {
    class: "wpw-field",
    type: "text",
    spellcheck: false,
    value: baseUrl(),
    onChange: () => {
      const value = url.value.trim() || DEFAULT_BASE_URL;
      url.value = value;
      setSetting(SETTING_BASE_URL, value);
      // Another server's models, prices and vision marks do not apply here.
      lists.models = [];
      refreshKeyState(state);
      loadLists(false);
    },
  });
  const temperature = el("input", {
    class: "wpw-field",
    type: "number",
    min: 0,
    max: 2,
    step: 0.05,
    value: clampTemperature(getSetting(SETTING_TEMPERATURE, DEFAULT_TEMPERATURE)),
    onChange: () => {
      const value = clampTemperature(temperature.value);
      temperature.value = value;
      setSetting(SETTING_TEMPERATURE, value);
    },
  });
  const thinking = el(
    "select",
    {
      class: "wpw-field wpw-prompts",
      title:
        "How much the model may think before it writes. More costs more and takes longer; not every model offers every level.",
      onChange: () => setSetting(SETTING_REASONING, thinking.value),
    },
    ...REASONING_LEVELS.map(([value, label]) => el("option", { value, text: label }))
  );
  thinking.value = reasoningLevel(getSetting(SETTING_REASONING, ""));
  const titleModel = el("input", {
    class: "wpw-field",
    type: "text",
    value: String(getSetting(SETTING_TITLE_MODEL, DEFAULT_TITLE_MODEL)),
    title: "Model id that names new chats. Empty keeps the first words of your message.",
    onChange: () => setSetting(SETTING_TITLE_MODEL, titleModel.value.trim()),
  });
  const key = el("input", {
    class: "wpw-field",
    type: "password",
    autocomplete: "off",
    placeholder: "Paste a key to save it",
  });
  const saveKey = async () => {
    const typed = key.value;
    key.value = "";
    if (!typed.trim()) {
      state.textContent = "Type the API key first";
      state.className = "wpw-key-state";
      return;
    }
    const out = await postJson("/warppipe/llm/key", { base_url: baseUrl(), key: typed }).catch(
      (error) => ({ error: String(error?.message ?? error) })
    );
    if (out?.error) {
      state.textContent = out.error;
      state.className = "wpw-key-state";
      return;
    }
    refreshKeyState(state);
  };
  key.addEventListener("keydown", (event) => {
    if (event.key === "Enter") saveKey();
  });
  panel.modelRow = settingRow("Model", model);
  panel.promptRow = settingRow("System prompt", prompts, eye);
  const server = el(
    "div",
    { class: "wpw-set-group" },
    settingRow("Server", url),
    el("div", { style: "height: 10px" }),
    settingRow("API key", key, button("Save", saveKey)),
    el("div", { style: "height: 6px" }),
    settingRow(
      "",
      state,
      el("span", { class: "wpw-spacer" }),
      button("Refresh lists", () => {
        refreshKeyState(state);
        loadLists(true);
      })
    )
  );
  const root = el(
    "div",
    { class: "wpw-pop wpw-settings" },
    el("div", { class: "wpw-settings-title", text: "Settings" }),
    panel.modelRow,
    panel.promptRow,
    settingRow("Temperature", temperature),
    settingRow("Thinking", thinking),
    settingRow("Title model", titleModel),
    server
  );
  openPopover(view.gear, root);
  view.model = model;
  view.prompts = prompts;
  renderModelButton();
  renderPrompts();
  refreshKeyState(state);
}

async function loadLists(refreshModels) {
  const run = ++listsRun;
  lists.loading = true;
  lists.error = "";
  if (view) renderModelButton();
  const query = `base_url=${encodeURIComponent(baseUrl())}${refreshModels ? "&refresh=1" : ""}`;
  let models;
  let prompts;
  try {
    [models, prompts] = await Promise.all([
      getJson(`/warppipe/llm/models?${query}`),
      getJson("/warppipe/llm/system_prompts"),
    ]);
  } catch (error) {
    models = { error: `Model list unreachable: ${error?.message ?? error}` };
    prompts = { error: "" };
  }
  if (run !== listsRun) return;

  const errors = [];
  if (models?.error) errors.push(models.error);
  else lists.models = Array.isArray(models?.models) ? models.models : [];
  if (prompts?.error) errors.push(prompts.error);
  else if (Array.isArray(prompts?.names)) lists.prompts = prompts.names;
  lists.loading = false;
  lists.error = errors.join(" · ");

  if (!getSetting(SETTING_SYSTEM_PROMPT, "") && lists.prompts.length) {
    setSetting(SETTING_SYSTEM_PROMPT, lists.prompts[0]);
  }
  if (!view) return;
  renderModelButton();
  renderPrompts();
  if (lists.error && view.chatId) setNote(view.chatId, lists.error, true);
}

// --- The pop-up ---------------------------------------------------------------

function closeView() {
  if (!view) return;
  flushEdits();
  view.renaming?.cancel();
  window.removeEventListener("keydown", view.onWindowKey, true);
  window.removeEventListener("keyup", view.onWindowKey, true);
  window.removeEventListener("paste", view.onWindowPaste, true);
  view.graphWatch.disconnect();
  view.backdrop.remove();
  view = null;
  comparisons.clear();
}

function dismiss() {
  if (view.popover) closePopover();
  else if (view.info) closeInfo();
  else if (view.renaming) view.renaming.cancel();
  else if (view.recreating) endRecreate();
  else closeView();
}

function buildView(promptNode) {
  const stop = (event) => event.stopPropagation();
  const title = button("New chat", beginRename, "wpw-title", "Click to rename");
  const target = el("select", {
    class: "wpw-target",
    title: "The Prompt + LoRAs this chat writes for",
    onChange: chooseTarget,
  });
  const gear = iconButton(
    "pi-cog",
    toggleGear,
    "wpw-icon",
    "Settings: model, system prompt, server"
  );
  const chatHead = el(
    "div",
    { class: "wpw-chat-head" },
    title,
    el("span", { class: "wpw-spacer" }),
    el("label", { class: "wpw-for" }, "For", target),
    iconButton("pi-times", closeView, "wpw-icon", "Close (Esc)")
  );

  const chats = el("div", { class: "wpw-chats" });
  const rail = el(
    "div",
    { class: "wpw-rail" },
    el(
      "div",
      { class: "wpw-rail-head" },
      el("span", { class: "wpw-brand" }, icon("pi-sparkles"), "Prompt Writer"),
      gear
    ),
    labelledButton("pi-plus", "New chat", () => newChat(), "wpw-btn wpw-new"),
    chats
  );

  const convo = el("div", { class: "wpw-convo" });
  const attached = el("div", { class: "wpw-attached" });
  const toolsLeft = el("div", { class: "wpw-tools-left" });
  const attach = iconButton("pi-image", pickImages, "wpw-tool", ATTACH_TITLE);
  const start = el("select", {
    class: "wpw-start",
    title: "What the next Write does: edit a version or the current prompt, or write a new prompt",
    onChange: () => chooseStart(),
  });
  const startIcon = icon("pi-pencil");
  const startChip = el("label", { class: "wpw-chip is-base wpw-start-chip" }, startIcon, start);
  const loraScopeButtons = new Map(
    LORA_SCOPES.map((scope) => [scope, button("", () => chooseLoraScope(scope), "wpw-seg-btn")])
  );
  const loraScopeEl = el(
    "span",
    { class: "wpw-seg" },
    el("span", { class: "wpw-seg-label", text: "LoRAs" }),
    ...loraScopeButtons.values()
  );
  const note = el("div", { class: "wpw-note" });
  const input = el("textarea", {
    class: "wpw-input",
    rows: INPUT_MIN_LINES,
    placeholder: PLACEHOLDER_WRITE,
    onInput: () => {
      draftOf(view.chatId).text = input.value;
      growInput();
    },
    onKeydown: (event) => {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      const chat = currentChat();
      if (chat) startWrite(chat);
    },
  });
  const sendIcon = icon("pi-arrow-up");
  const send = el(
    "button",
    {
      type: "button",
      class: "wpw-btn wpw-primary wpw-send",
      title: "Write (Ctrl+Enter)",
      "aria-label": "Write (Ctrl+Enter)",
      onClick: () => {
        const chat = currentChat();
        if (!chat) return;
        const job = jobs.get(chat.id);
        if (job) job.controller.abort();
        else startWrite(chat);
      },
    },
    sendIcon
  );
  // The settings of the next request on top, then its images and text, and send in the corner.
  const composer = el(
    "div",
    { class: "wpw-composer" },
    el(
      "div",
      { class: "wpw-box" },
      el("div", { class: "wpw-tools" }, toolsLeft),
      attached,
      input,
      send
    ),
    note
  );
  const main = el("div", { class: "wpw-main" }, chatHead, convo, composer);

  const modal = el(
    "div",
    {
      class: "wpw-modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Prompt Writer",
      tabIndex: -1,
    },
    rail,
    main
  );
  const backdrop = el("div", { class: "wpw-backdrop" }, modal);

  // Bubbling out of the pop-up would reach ComfyUI's hotkeys and the canvas.
  for (const type of ["keydown", "keyup", "wheel", "copy", "cut"]) {
    backdrop.addEventListener(type, stop);
  }
  modal.addEventListener("pointerdown", (event) => {
    const pop = view?.popover;
    if (pop && !pop.root.contains(event.target) && !pop.anchor.contains(event.target)) {
      closePopover();
    }
  });
  // A click on the shade closes the pop-up. Only a press that starts and ends
  // there counts: one that starts inside, selecting text say, may end out here.
  let pressedShade = false;
  backdrop.addEventListener("pointerdown", (event) => {
    pressedShade = event.target === backdrop;
  });
  backdrop.addEventListener("pointerup", (event) => {
    pressedShade &&= event.target === backdrop;
  });
  backdrop.addEventListener("click", (event) => {
    if (pressedShade && event.target === backdrop) closeView();
  });

  // ComfyUI loads a dropped file as a workflow. Text dragged between fields is left alone.
  const dragOver = (event) => {
    event.stopPropagation();
    if (!hasFiles(event)) return;
    event.preventDefault();
    composer.classList.add("is-drag");
  };
  backdrop.addEventListener("dragenter", dragOver);
  backdrop.addEventListener("dragover", dragOver);
  backdrop.addEventListener("dragleave", (event) => {
    event.stopPropagation();
    if (!backdrop.contains(event.relatedTarget)) composer.classList.remove("is-drag");
  });
  backdrop.addEventListener("drop", (event) => {
    event.stopPropagation();
    composer.classList.remove("is-drag");
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (view?.chatId) addImages(view.chatId, [...event.dataTransfer.files]);
  });

  // On the window, so these work with the focus anywhere: outside the pop-up
  // nothing in it would see them, and ComfyUI would act on them instead.
  const onWindowKey = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (event.type === "keydown") dismiss();
      return;
    }
    if (!backdrop.contains(event.target)) event.stopPropagation();
  };
  const onWindowPaste = (event) => {
    // Stopped, not prevented: pasted text still lands in the focused field.
    event.stopPropagation();
    const files = [...(event.clipboardData?.files ?? [])].filter((file) =>
      file.type.startsWith("image/")
    );
    if (!files.length || !view?.chatId) return;
    event.preventDefault();
    addImages(view.chatId, files);
  };
  window.addEventListener("keydown", onWindowKey, true);
  window.addEventListener("keyup", onWindowKey, true);
  window.addEventListener("paste", onWindowPaste, true);

  view = {
    backdrop,
    modal,
    title,
    target,
    model: null,
    prompts: null,
    gear,
    chats,
    convo,
    composer,
    attached,
    toolsLeft,
    attach,
    loraScope: loraScopeEl,
    start,
    startIcon,
    startChip,
    loraScopeButtons,
    note,
    input,
    send,
    sendIcon,
    onWindowKey,
    onWindowPaste,
    promptNode,
    targetNode: promptNode,
    targets: [],
    chatId: null,
    editors: [],
    // The one open version: its id, null for the newest, NONE_OPEN once you closed it.
    open: null,
    changes: new Set(),
    // The thinking of a write in progress, "live:<chat id>", once you opened it.
    thinking: new Set(),
    // Versions whose LoRAs you opened.
    loras: new Set(),
    // Versions whose request you closed: it is open on every version until then.
    requestClosed: new Set(),
    // Recreate's field while it is open: { id of the version, the request as typed }.
    recreating: null,
    recreateInput: null,
    // The generation info, while it is open.
    info: null,
    // version id → the hand edit typing made of it, so a press on its card acts on that.
    successor: new Map(),
    graph: null,
    graphWatch: new ResizeObserver(scheduleGraph),
    popover: null,
    renaming: null,
    confirmDelete: null,
  };
  document.body.appendChild(backdrop);
  drawLoraScope(undefined);
  renderModelButton();
  renderPrompts();
  growInput();
  input.focus();
}

/** The chat to show: one this node is waiting on, else its most recent, else null for a new one. */
function initialChat(promptNode) {
  const finished = [...unseen].find(([, node]) => node === promptNode)?.[0];
  const writing = [...jobs.values()].find((job) => job.originNode === promptNode)?.chatId;
  return finished ?? writing ?? latestChatFor(store, labelOfNode(promptNode))?.id ?? null;
}

function showChatOf(promptNode) {
  const id = initialChat(promptNode);
  if (id) selectChat(id);
  else newChat();
}

function showLoadFailure(promptNode, error) {
  view.convo.replaceChildren(
    el("div", {
      class: "wpw-hint is-error",
      text: `Could not read the saved chats (${CHATS_FILE} in the ComfyUI user folder): ${error?.message ?? error}`,
    }),
    el(
      "div",
      { class: "wpw-actions" },
      button("Try again", () => {
        closeView();
        openPromptWriter(promptNode);
      })
    )
  );
}

export async function openPromptWriter(promptNode) {
  ensureStyle();
  if (view) {
    view.promptNode = promptNode;
    if (store) showChatOf(promptNode);
    return;
  }
  buildView(promptNode);
  try {
    await ensureStore();
  } catch (error) {
    console.error("[WarpPipe] reading chats", error);
    if (view) showLoadFailure(promptNode, error);
    return;
  }
  if (!view) return;
  showChatOf(view.promptNode);
  growInput();
  loadLists(false);
  // The versions from before titles get theirs, once.
  nameUntitled();
}

app.registerExtension({
  name: "WarpPipe.PromptWriter",
  settings: [
    {
      id: SETTING_DIR,
      name: "System prompt folder",
      type: "text",
      defaultValue: "",
      category: ["WarpPipe", "Prompt Writer", "System prompt folder"],
      tooltip:
        "Folder of system prompts for 🌀 Prompt Writer. Only .txt files directly in this folder are listed.",
    },
    {
      id: SETTING_BASE_URL,
      name: "Prompt Writer base URL",
      type: "hidden",
      defaultValue: DEFAULT_BASE_URL,
    },
    { id: SETTING_MODEL, name: "Prompt Writer model", type: "hidden", defaultValue: "" },
    {
      id: SETTING_TITLE_MODEL,
      name: "Prompt Writer title model",
      type: "hidden",
      defaultValue: DEFAULT_TITLE_MODEL,
    },
    {
      id: SETTING_SYSTEM_PROMPT,
      name: "Prompt Writer system prompt",
      type: "hidden",
      defaultValue: "",
    },
    {
      id: SETTING_TEMPERATURE,
      name: "Prompt Writer temperature",
      type: "hidden",
      defaultValue: DEFAULT_TEMPERATURE,
    },
    {
      id: SETTING_REASONING,
      name: "Prompt Writer thinking level",
      type: "hidden",
      defaultValue: "",
    },
    {
      id: SETTING_LORA_SCOPE,
      name: "Prompt Writer LoRA suggestions",
      type: "hidden",
      defaultValue: "all",
    },
  ],
});
