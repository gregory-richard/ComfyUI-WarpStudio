import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHATS_FILE,
  CURRENT_PROMPT,
  KIND_HAND,
  KIND_NODE,
  KIND_WRITTEN,
  NEW_PROMPT,
  addHandVersion,
  addNodeVersion,
  addVersion,
  adoptText,
  editVersion,
  canFold,
  foldedCount,
  historyLayout,
  isUsed,
  keptPaths,
  markSent,
  matchText,
  originLabel,
  pathTo,
  rowText,
  toggleFold,
  unfoldTo,
  visibleVersions,
  clearFailure,
  recordFailure,
  dropQuestion,
  openQuestion,
  startLabel,
  startOptions,
  startValue,
  baseText,
  createChat,
  deleteChat,
  emptyStore,
  isStore,
  latestChatFor,
  mergedDraft,
  normalizeStore,
  renameChat,
  setAutoTitle,
  sortedChats,
  titleFrom,
  branchOf,
  deleteVersion,
  hasFollowUps,
  keepSystemPrompt,
  nextVersionNumber,
  replaceVersion,
  setVersionTitle,
  systemPromptOf,
  untitledVersions,
} from "../../web/prompt_chats.js";

function version(id, now, extra = {}) {
  return {
    id,
    now,
    request: `ask ${id}`,
    text: `text ${id}`,
    model: "m",
    systemPrompt: "s",
    ...extra,
  };
}

function chatWith(...ids) {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  ids.forEach((id, i) => addVersion(chat, version(id, 10 + i)));
  return chat;
}

const ids = (chats) => chats.map((c) => c.id);

test("CHATS_FILE and emptyStore", () => {
  assert.equal(CHATS_FILE, "warppipe_prompt_chats.json");
  assert.deepEqual(emptyStore(), { version: 1, chats: [] });
  assert.notEqual(emptyStore().chats, emptyStore().chats);
});

test("createChat adds a fresh chat to the store", () => {
  const store = emptyStore();
  const chat = createChat(store, { id: "a", now: 5 });
  assert.deepEqual(chat, {
    id: "a",
    title: "New chat",
    titleEdited: false,
    created: 5,
    updated: 5,
    seedPrompt: null,
    targetLabel: null,
    versions: [],
    pending: null,
    failure: null,
    folded: [],
    systemPrompts: {},
  });
  assert.equal(store.chats[0], chat);
  assert.equal(createChat(store, { id: "b", now: 6, seedPrompt: "seed" }).seedPrompt, "seed");
  assert.equal(store.chats.length, 2);
});

test("createChat accepts the label of its target", () => {
  const chat = createChat(emptyStore(), { id: "a", now: 5, targetLabel: "Hero" });
  assert.equal(chat.targetLabel, "Hero");
});

test("normalizeStore keeps a chat's targetLabel and defaults a missing one", () => {
  const raw = {
    version: 1,
    chats: [
      { id: "a", versions: [], targetLabel: "Hero" },
      { id: "b", versions: [] },
      { id: "c", versions: [], targetLabel: 7 },
    ],
  };
  assert.deepEqual(
    normalizeStore(raw).chats.map((c) => c.targetLabel),
    ["Hero", null, null]
  );
});

test("latestChatFor finds the most recently updated chat with that label", () => {
  const store = emptyStore();
  createChat(store, { id: "old", now: 1, targetLabel: "Hero" });
  createChat(store, { id: "new", now: 3, targetLabel: "Hero" });
  createChat(store, { id: "other", now: 9, targetLabel: "Sky" });
  createChat(store, { id: "none", now: 10 });
  assert.equal(latestChatFor(store, "Hero").id, "new");
  assert.equal(latestChatFor(store, "Sky").id, "other");
  assert.equal(latestChatFor(store, "Gone"), null);
});

test("latestChatFor never matches an unknown label to a chat without one", () => {
  const store = emptyStore();
  createChat(store, { id: "none", now: 10 });
  assert.equal(latestChatFor(store, null), null);
  assert.equal(latestChatFor(store, undefined), null);
});

const SUGGESTION = {
  id: "sub/soft.safetensors",
  name: "soft",
  title: "Soft Light",
  tag: "soft",
  thumbnail: "/t?name=soft",
  triggers: ["softlight"],
  strength: 0.75,
  reason: "adds light",
};

test("addVersion stores its suggestions as copies", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const given = [{ ...SUGGESTION, triggers: ["softlight"] }];
  const v = addVersion(chat, version("v1", 2, { suggestions: given }));
  assert.deepEqual(v.suggestions, [SUGGESTION]);
  given[0].triggers.push("changed");
  assert.deepEqual(v.suggestions[0].triggers, ["softlight"]);
});

test("a version remembers which LoRAs were active when it was written", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.deepEqual(addVersion(chat, version("v1", 2)).activeLoras, []);
  const v = addVersion(chat, version("v2", 3, { activeLoras: [SUGGESTION] }));
  assert.deepEqual(v.activeLoras, [SUGGESTION]);
  const raw = { version: 1, chats: [{ id: "c", versions: [{ id: "v", text: "t" }] }] };
  assert.deepEqual(normalizeStore(raw).chats[0].versions[0].activeLoras, []);
  raw.chats[0].versions[0].activeLoras = [SUGGESTION, { id: "x" }];
  assert.deepEqual(normalizeStore(raw).chats[0].versions[0].activeLoras, [SUGGESTION]);
});

test("addVersion defaults to no suggestions", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.deepEqual(addVersion(chat, version("v1", 2)).suggestions, []);
});

function storeWithSuggestions(suggestions) {
  return {
    version: 1,
    chats: [{ id: "c", versions: [{ id: "v", text: "t", suggestions }] }],
  };
}

test("normalizeStore keeps well-formed suggestions", () => {
  const out = normalizeStore(storeWithSuggestions([SUGGESTION]));
  assert.deepEqual(out.chats[0].versions[0].suggestions, [SUGGESTION]);
});

test("normalizeStore tolerates versions without suggestions", () => {
  const out = normalizeStore({
    version: 1,
    chats: [{ id: "c", versions: [{ id: "v", text: "t" }] }],
  });
  assert.deepEqual(out.chats[0].versions[0].suggestions, []);
  const odd = normalizeStore(storeWithSuggestions("nope"));
  assert.deepEqual(odd.chats[0].versions[0].suggestions, []);
});

test("normalizeStore drops malformed suggestions and repairs the optional fields", () => {
  const out = normalizeStore(
    storeWithSuggestions([
      null,
      "x",
      { ...SUGGESTION, tag: "" },
      { ...SUGGESTION, id: 3 },
      { ...SUGGESTION, strength: "0.8" },
      { id: "a", tag: "a", strength: 1, triggers: ["t", 4], thumbnail: 7 },
    ])
  );
  assert.deepEqual(out.chats[0].versions[0].suggestions, [
    {
      id: "a",
      name: "",
      title: "",
      tag: "a",
      thumbnail: null,
      triggers: ["t"],
      strength: 1,
      reason: "",
    },
  ]);
});

test("addVersion fills every field and numbers from 1", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const v = addVersion(chat, {
    id: "v1",
    now: 7,
    request: "a red fox",
    images: ["fox.png [input]"],
    text: "PROMPT",
    model: "m",
    systemPrompt: "s.txt",
    cost: 0.1,
    seconds: 2,
    usage: { prompt_tokens: 1, completion_tokens: 2 },
  });
  assert.deepEqual(v, {
    id: "v1",
    n: 1,
    created: 7,
    kind: "written",
    request: "a red fox",
    title: "",
    images: ["fox.png [input]"],
    baseId: null,
    text: "PROMPT",
    model: "m",
    systemPrompt: "s.txt",
    temperature: null,
    thinkingLevel: null,
    loraScope: null,
    server: null,
    systemPromptKey: null,
    cost: 0.1,
    seconds: 2,
    usage: { prompt_tokens: 1, completion_tokens: 2 },
    suggestions: [],
    activeLoras: [],
    questions: [],
    reasoning: "",
    editedIn: null,
    source: null,
    sentAt: null,
  });
  assert.equal(chat.versions[0], v);
  assert.equal(chat.updated, 7);
  assert.equal(chat.created, 1);
  assert.equal(addVersion(chat, version("v2", 8)).n, 2);
});

test("addVersion defaults", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const v = addVersion(chat, version("v", 2));
  assert.deepEqual(v.images, []);
  assert.equal(v.baseId, null);
  assert.equal(v.cost, null);
  assert.equal(v.seconds, null);
  assert.deepEqual(v.usage, {});
});

test("addVersion keeps its own copy of images and usage", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const images = ["a.png"];
  const usage = { prompt_tokens: 1 };
  const v = addVersion(chat, version("v", 2, { images, usage }));
  images.push("b.png");
  usage.prompt_tokens = 9;
  assert.deepEqual(v.images, ["a.png"]);
  assert.deepEqual(v.usage, { prompt_tokens: 1 });
});

test("the first version names the chat, a later one does not", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  addVersion(chat, version("v1", 2, { request: "a red fox in snow" }));
  assert.equal(chat.title, "a red fox in snow");
  addVersion(chat, version("v2", 3, { request: "make it blue instead" }));
  assert.equal(chat.title, "a red fox in snow");
});

test("a chat renamed before its first version keeps that name", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  renameChat(chat, "Mine");
  addVersion(chat, version("v1", 2));
  assert.equal(chat.title, "Mine");
});

test("titleFrom", () => {
  assert.equal(titleFrom("one two three"), "one two three");
  assert.equal(titleFrom("one two three four five six seven eight"), "one two three four five six");
  assert.equal(titleFrom("  spaced \n\t out   words "), "spaced out words");
  assert.equal(titleFrom(""), "New chat");
  assert.equal(titleFrom("   "), "New chat");
  assert.equal(titleFrom(null), "New chat");
  assert.equal(titleFrom(undefined), "New chat");
  assert.equal(titleFrom("x".repeat(60)), "x".repeat(40));
  const words = titleFrom("abcdefghij abcdefghij abcdefghij abcdefghij abcdefghij");
  assert.ok(words.length <= 40);
  assert.equal(words, words.trim());
});

test("titleFrom never ends on half an emoji", () => {
  assert.equal(titleFrom("x".repeat(39) + "🦊"), "x".repeat(39));
});

test("baseText: seed", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1, seedPrompt: "SEED" });
  addVersion(chat, version("v1", 2));
  assert.equal(baseText(chat, "seed"), "SEED");
  assert.equal(baseText(chatWith("v1"), "seed"), null);
});

test("baseText: a version id, including an older one", () => {
  const chat = chatWith("v1", "v2", "v3");
  assert.equal(baseText(chat, "v1"), "text v1");
  assert.equal(baseText(chat, "v2"), "text v2");
  assert.equal(baseText(chat, "nope"), null);
});

test("baseText: null means newest, else seed, else nothing", () => {
  assert.equal(baseText(chatWith("v1", "v2"), null), "text v2");
  assert.equal(baseText(chatWith("v1", "v2"), undefined), "text v2");
  const seeded = createChat(emptyStore(), { id: "c", now: 1, seedPrompt: "SEED" });
  assert.equal(baseText(seeded, null), "SEED");
  assert.equal(baseText(chatWith(), null), null);
});

test("a version based on an older one becomes the newest", () => {
  const chat = chatWith("v1", "v2");
  const v3 = addVersion(chat, version("v3", 20, { baseId: "v1" }));
  assert.equal(v3.baseId, "v1");
  assert.equal(v3.n, 3);
  assert.equal(baseText(chat, null), "text v3");
  assert.equal(baseText(chat, v3.baseId), "text v1");
});

test("renameChat", () => {
  const chat = chatWith();
  renameChat(chat, "  Named  ");
  assert.equal(chat.title, "Named");
  assert.equal(chat.titleEdited, true);
  const untouched = chatWith();
  renameChat(untouched, "   ");
  renameChat(untouched, "");
  renameChat(untouched, null);
  assert.equal(untouched.title, "New chat");
  assert.equal(untouched.titleEdited, false);
});

test("deleteChat", () => {
  const store = emptyStore();
  createChat(store, { id: "a", now: 1 });
  createChat(store, { id: "b", now: 2 });
  assert.equal(deleteChat(store, "a"), true);
  assert.deepEqual(ids(store.chats), ["b"]);
  assert.equal(deleteChat(store, "a"), false);
});

test("sortedChats: newest updated first, without changing the store", () => {
  const store = emptyStore();
  const a = createChat(store, { id: "a", now: 1 });
  createChat(store, { id: "b", now: 2 });
  createChat(store, { id: "c", now: 3 });
  addVersion(a, version("v", 10));
  assert.deepEqual(ids(sortedChats(store)), ["a", "c", "b"]);
  assert.deepEqual(ids(store.chats), ["a", "b", "c"]);
});

test("sortedChats: ISO timestamps sort by time too", () => {
  const store = emptyStore();
  createChat(store, { id: "old", now: "2026-11-08T09:00:00.000Z" });
  createChat(store, { id: "new", now: "2026-11-08T10:00:00.000Z" });
  assert.deepEqual(ids(sortedChats(store)), ["new", "old"]);
});

test("normalizeStore: junk gives an empty store", () => {
  const junk = [
    null,
    undefined,
    0,
    "x",
    true,
    [],
    {},
    { chats: "x" },
    { chats: [] },
    { version: 2, chats: [] },
    { version: "1", chats: [] },
    { version: 1 },
    { version: 1, chats: {} },
  ];
  for (const raw of junk) assert.deepEqual(normalizeStore(raw), emptyStore());
});

test("isStore tells a store this version can read from anything else", () => {
  assert.equal(isStore(emptyStore()), true);
  for (const raw of [null, [], {}, { version: 2, chats: [] }, { version: 1, chats: {} }]) {
    assert.equal(isStore(raw), false);
  }
});

test("mergedDraft: a request put back joins what was already typed", () => {
  const empty = { text: "", images: [] };
  assert.deepEqual(mergedDraft(empty, { text: "back", images: ["a"] }, 8), {
    text: "back",
    images: ["a"],
    overflow: false,
  });
  assert.deepEqual(
    mergedDraft({ text: "typed", images: ["b", "a"] }, { text: "back", images: ["a"] }, 8),
    { text: "back\n\ntyped", images: ["a", "b"], overflow: false }
  );
  assert.equal(
    mergedDraft({ text: "  ", images: [] }, { text: "back", images: [] }, 8).text,
    "back"
  );
  assert.equal(
    mergedDraft({ text: "typed", images: [] }, { text: "", images: [] }, 8).text,
    "typed"
  );
});

test("mergedDraft says when the images no longer fit", () => {
  const out = mergedDraft({ text: "", images: ["c", "d"] }, { text: "", images: ["a", "b"] }, 3);
  assert.deepEqual(out.images, ["a", "b", "c"]);
  assert.equal(out.overflow, true);
});

test("normalizeStore: a valid store survives unchanged", () => {
  const store = emptyStore();
  const chat = createChat(store, { id: "c", now: 1, seedPrompt: "S" });
  addVersion(chat, version("v1", 2, { images: ["a.png"], cost: 0.5, seconds: 3 }));
  addVersion(chat, version("v2", 3, { baseId: "v1", usage: { prompt_tokens: 4 } }));
  renameChat(chat, "Named");
  assert.deepEqual(normalizeStore(JSON.parse(JSON.stringify(store))), store);
});

test("normalizeStore: drops malformed chats", () => {
  const out = normalizeStore({
    version: 1,
    chats: [
      null,
      1,
      "x",
      [],
      {},
      { id: 5, versions: [] },
      { id: "", versions: [] },
      { id: "noVersions" },
      { id: "badVersions", versions: "x" },
      { id: "ok", versions: [] },
    ],
  });
  assert.deepEqual(ids(out.chats), ["ok"]);
});

test("normalizeStore: drops malformed versions and keeps the saved numbers", () => {
  const out = normalizeStore({
    version: 1,
    chats: [
      {
        id: "c",
        versions: [
          { id: "v1", text: "one", n: 1 },
          null,
          { id: "noText" },
          { text: "noId" },
          // A deleted version left a gap: the number stays.
          { id: "v3", text: "three", n: 5 },
          // No number, or one that does not rise: the next one.
          { id: "v4", text: "four" },
          { id: "v5", text: "five", n: 2 },
          { id: "v6", text: "six", n: 1.5 },
        ],
      },
    ],
  });
  assert.deepEqual(
    out.chats[0].versions.map((v) => [v.id, v.n]),
    [
      ["v1", 1],
      ["v3", 5],
      ["v4", 6],
      ["v5", 7],
      ["v6", 8],
    ]
  );
});

test("normalizeStore: repairs the fields it can", () => {
  const out = normalizeStore({
    version: 1,
    chats: [
      {
        id: "c",
        title: 5,
        titleEdited: "yes",
        created: {},
        updated: null,
        seedPrompt: 7,
        versions: [
          {
            id: "v",
            text: "t",
            request: 4,
            images: "x",
            baseId: 3,
            model: null,
            systemPrompt: {},
            cost: "free",
            seconds: "long",
            usage: [],
          },
        ],
      },
    ],
  });
  const [chat] = out.chats;
  assert.equal(chat.title, "New chat");
  assert.equal(chat.titleEdited, false);
  assert.equal(chat.created, 0);
  assert.equal(chat.updated, 0);
  assert.equal(chat.seedPrompt, null);
  assert.deepEqual(chat.versions[0], {
    id: "v",
    n: 1,
    created: 0,
    kind: "written",
    request: "",
    title: "",
    images: [],
    baseId: null,
    text: "t",
    model: "",
    systemPrompt: "",
    temperature: null,
    thinkingLevel: null,
    loraScope: null,
    server: null,
    systemPromptKey: null,
    cost: null,
    seconds: null,
    usage: {},
    suggestions: [],
    activeLoras: [],
    questions: [],
    reasoning: "",
    editedIn: null,
    source: null,
    sentAt: null,
  });
});

test("normalizeStore: drops junk image entries, keeps names and objects", () => {
  const images = ["a.png", { name: "b.png" }, 3, null, "", []];
  const out = normalizeStore({
    version: 1,
    chats: [{ id: "c", versions: [{ id: "v", text: "t", images }] }],
  });
  assert.deepEqual(out.chats[0].versions[0].images, ["a.png", { name: "b.png" }]);
});

test("normalizeStore: duplicate ids keep the first", () => {
  const out = normalizeStore({
    version: 1,
    chats: [
      {
        id: "c",
        title: "first",
        versions: [
          { id: "v", text: "a" },
          { id: "v", text: "b" },
        ],
      },
      { id: "c", title: "second", versions: [] },
    ],
  });
  assert.equal(out.chats.length, 1);
  assert.equal(out.chats[0].title, "first");
  assert.deepEqual(
    out.chats[0].versions.map((v) => v.text),
    ["a"]
  );
});

test("normalizeStore does not share objects with its input", () => {
  const raw = {
    version: 1,
    chats: [{ id: "c", versions: [{ id: "v", text: "t", images: ["a"] }] }],
  };
  const out = normalizeStore(raw);
  out.chats[0].versions[0].images.push("b");
  out.chats[0].title = "changed";
  assert.deepEqual(raw.chats[0].versions[0].images, ["a"]);
  assert.equal(raw.chats[0].title, undefined);
});

test("setAutoTitle replaces an automatic title but never one the user typed", () => {
  const store = emptyStore();
  const chat = createChat(store, { id: "c", now: 1 });
  assert.equal(setAutoTitle(chat, "Red bicycle"), true);
  assert.equal(chat.title, "Red bicycle");
  assert.equal(chat.updated, 1);
  assert.equal(setAutoTitle(chat, "  "), false);
  renameChat(chat, "Mine");
  assert.equal(setAutoTitle(chat, "Other"), false);
  assert.equal(chat.title, "Mine");
});

// --- steps, questions and where a step starts -------------------------------------

test("a version keeps the questions on the way", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const turns = [{ question: "Q", answer: "A" }];
  const v = addVersion(chat, version("v1", 2, { questions: turns }));
  assert.deepEqual(v.questions, [{ question: "Q", answer: "A" }]);
  turns[0].answer = "changed";
  assert.equal(v.questions[0].answer, "A");
  const raw = JSON.parse(JSON.stringify({ version: 1, chats: [chat] }));
  raw.chats[0].versions[0].questions.push(
    { question: "", answer: "x" },
    { question: "Q2" },
    "junk"
  );
  assert.deepEqual(normalizeStore(raw).chats[0].versions[0].questions, [
    { question: "Q", answer: "A" },
  ]);
});

test("openQuestion puts a step on hold, dropQuestion takes it back", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const step = { request: "warmer", images: ["a.png"], baseId: "v1", turns: [] };
  const pending = openQuestion(chat, step, "Sun in frame?", 5);
  assert.deepEqual(pending, {
    request: "warmer",
    images: ["a.png"],
    baseId: "v1",
    turns: [],
    question: "Sun in frame?",
    replaceId: null,
  });
  assert.equal(chat.updated, 5);
  step.images.push("b.png");
  assert.deepEqual(chat.pending.images, ["a.png"]);
  assert.equal(dropQuestion(chat), pending);
  assert.equal(chat.pending, null);
  assert.equal(dropQuestion(chat), null);
});

test("normalizeStore keeps a step that waits for an answer, and drops a broken one", () => {
  const store = emptyStore();
  const chat = createChat(store, { id: "c", now: 1 });
  const turns = [{ question: "Q", answer: "A" }];
  openQuestion(chat, { request: "r", images: [], baseId: null, turns }, "Q2", 2);
  assert.deepEqual(normalizeStore(JSON.parse(JSON.stringify(store))), store);
  for (const pending of [null, "x", {}, { question: "" }, { request: "r" }]) {
    const raw = { version: 1, chats: [{ id: "c", versions: [], pending }] };
    assert.equal(normalizeStore(raw).chats[0].pending, null);
  }
});

test("startOptions: the chosen version (else the newest), the current prompt, or a new one", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.deepEqual(startOptions(chat, "Krea 2"), [
    { value: CURRENT_PROMPT, label: "Edit current prompt (Krea 2)" },
    { value: NEW_PROMPT, label: "New prompt" },
  ]);
  addVersion(chat, version("v1", 2, { request: "a red bicycle by the lake at dawn please" }));
  addVersion(chat, version("v2", 3, { request: "" }));
  addVersion(chat, version("v3", 4));
  const labels = (base) => startOptions(chat, null, base).map((o) => o.label);
  assert.deepEqual(labels(null), [
    "Edit v3 (newest)",
    "Edit current prompt (the For node)",
    "New prompt",
  ]);
  assert.deepEqual(
    startOptions(chat, null, "v1").map((o) => o.value),
    ["v1", CURRENT_PROMPT, NEW_PROMPT]
  );
  assert.equal(labels("v1")[0], "Edit v1 (a red bicycle by the lake)");
  assert.equal(labels("v2")[0], "Edit v2 (optimised)");
  // With the current prompt or a new one chosen, the version on offer is the newest.
  assert.equal(labels(CURRENT_PROMPT)[0], "Edit v3 (newest)");
  assert.equal(labels(NEW_PROMPT)[0], "Edit v3 (newest)");
  // A version's title names it, once it has one.
  chat.versions[0].title = "Bicycle at dawn";
  assert.equal(labels("v1")[0], "Edit v1 (Bicycle at dawn)");
});

test("startValue: a draft's choice while it exists, else the newest version, else nothing", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.equal(startValue(chat, null), NEW_PROMPT);
  assert.equal(startValue(chat, CURRENT_PROMPT), CURRENT_PROMPT);
  addVersion(chat, version("v1", 2));
  addVersion(chat, version("v2", 3));
  assert.equal(startValue(chat, null), "v2");
  assert.equal(startValue(chat, "v1"), "v1");
  assert.equal(startValue(chat, "gone"), "v2");
  assert.equal(startValue(chat, NEW_PROMPT), NEW_PROMPT);
});

test("startLabel says where a step started", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  addVersion(chat, version("v1", 2));
  assert.equal(startLabel(chat, "v1"), "edit of v1");
  assert.equal(startLabel(chat, CURRENT_PROMPT), "new prompt");
  assert.equal(startLabel(chat, null), "new prompt");
  assert.equal(startLabel(chat, "gone"), "new prompt");
});

test("a version keeps what the model thought, and an old one has nothing", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.equal(addVersion(chat, version("v1", 2, { reasoning: "why" })).reasoning, "why");
  const raw = { version: 1, chats: [{ id: "c", versions: [{ id: "v", text: "t" }] }] };
  assert.equal(normalizeStore(raw).chats[0].versions[0].reasoning, "");
  raw.chats[0].versions[0].reasoning = 5;
  assert.equal(normalizeStore(raw).chats[0].versions[0].reasoning, "");
});

// --- a failed request survives a reload ---------------------------------------------

const FAILED = {
  request: "warmer light",
  images: ["a.png"],
  baseId: "v1",
  turns: [{ question: "Q", answer: "A" }],
  answer: "A",
  originNode: { id: 7 },
  loraRefs: new Map(),
  payload: { idea: "warmer light" },
};

test("recordFailure keeps what a Retry needs, and nothing that only lives in the page", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  recordFailure(chat, FAILED, "Insufficient balance", 5);
  assert.deepEqual(chat.failure, {
    request: "warmer light",
    images: ["a.png"],
    baseId: "v1",
    turns: [{ question: "Q", answer: "A" }],
    answer: "A",
    message: "Insufficient balance",
    replaceId: null,
  });
  assert.equal(chat.updated, 5);
  FAILED.images.push("b.png");
  assert.deepEqual(chat.failure.images, ["a.png"]);
  FAILED.images.pop();
});

test("clearFailure says whether there was one to clear", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  assert.equal(clearFailure(chat), false);
  recordFailure(chat, FAILED, "x", 2);
  assert.equal(clearFailure(chat), true);
  assert.equal(chat.failure, null);
});

test("normalizeStore keeps a saved failure, and drops a broken one", () => {
  const store = emptyStore();
  const chat = createChat(store, { id: "c", now: 1 });
  recordFailure(chat, { request: "r" }, "Timed out", 2);
  assert.deepEqual(normalizeStore(JSON.parse(JSON.stringify(store))), store);
  assert.deepEqual(chat.failure, {
    request: "r",
    images: [],
    baseId: null,
    turns: [],
    answer: null,
    message: "Timed out",
    replaceId: null,
  });
  for (const failure of [
    null,
    "x",
    {},
    { request: "r" },
    { message: "m" },
    { request: 3, message: "m" },
  ]) {
    const raw = { version: 1, chats: [{ id: "c", versions: [], failure }] };
    assert.equal(normalizeStore(raw).chats[0].failure, null);
  }
});

// --- kinds: written, hand and node versions -----------------------------------------

test("addHandVersion and addNodeVersion", () => {
  const chat = chatWith("v1");
  const hand = addHandVersion(chat, { id: "h", now: 20, baseId: "v1", text: "tweaked" });
  assert.equal(hand.kind, KIND_HAND);
  assert.equal(hand.n, 2);
  assert.equal(hand.baseId, "v1");
  assert.equal(hand.request, "");
  assert.equal(hand.model, "");
  assert.equal(hand.cost, null);
  assert.equal(hand.editedIn, null);
  const node = addNodeVersion(chat, { id: "n", now: 21, text: "pasted", source: "🌀 Hero" });
  assert.equal(node.kind, KIND_NODE);
  assert.equal(node.n, 3);
  assert.equal(node.baseId, null);
  assert.equal(node.source, "🌀 Hero");
  assert.equal(chat.updated, 21);
});

test("the first written version names the chat, not a node or hand version", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  addNodeVersion(chat, { id: "n", now: 2, text: "from the node" });
  assert.equal(chat.title, "New chat");
  addVersion(chat, version("v2", 3, { request: "make it morning", baseId: "n" }));
  assert.equal(chat.title, "make it morning");
  addVersion(chat, version("v3", 4, { request: "add fog" }));
  assert.equal(chat.title, "make it morning");
});

test("markSent remembers when a version went to a node", () => {
  const chat = chatWith("v1");
  markSent(chat.versions[0], 99);
  assert.equal(chat.versions[0].sentAt, 99);
});

test("normalizeStore keeps kinds, sentAt and folded, and repairs junk", () => {
  const raw = {
    version: 1,
    chats: [
      {
        id: "c",
        folded: ["a", "gone", "a", 5],
        versions: [
          { id: "a", text: "A", kind: "node", source: "Hero", sentAt: 7 },
          { id: "b", text: "B", kind: "hand", baseId: "a", editedIn: "node" },
          { id: "d", text: "D", kind: "magic", editedIn: "box", sentAt: "now", source: 3 },
        ],
      },
      { id: "e", versions: [] },
    ],
  };
  const [chat, empty] = normalizeStore(raw).chats;
  assert.deepEqual(
    chat.versions.map((v) => [v.kind, v.editedIn, v.source, v.sentAt]),
    [
      ["node", null, "Hero", 7],
      ["hand", "node", null, null],
      ["written", null, null, null],
    ]
  );
  assert.deepEqual(chat.folded, ["a"]);
  assert.deepEqual(empty.folded, []);
});

test("originLabel says where a version came from", () => {
  const chat = chatWith("v1", "v2");
  chat.versions[1].baseId = "v1";
  const hand = addHandVersion(chat, { id: "h", now: 20, baseId: "v2", text: "x" });
  const inNode = addHandVersion(chat, {
    id: "i",
    now: 21,
    baseId: "h",
    text: "y",
    editedIn: "node",
  });
  const node = addNodeVersion(chat, { id: "n", now: 22, text: "z", source: "🌀 Hero" });
  const bare = addNodeVersion(chat, { id: "m", now: 23, text: "w" });
  assert.equal(originLabel(chat, chat.versions[0]), "new prompt");
  assert.equal(originLabel(chat, chat.versions[1]), "edit of v1");
  assert.equal(originLabel(chat, hand), "hand edit of v2");
  assert.equal(originLabel(chat, inNode), "hand edit of v3, in the node");
  assert.equal(originLabel(chat, node), "from 🌀 Hero");
  assert.equal(originLabel(chat, bare), "from the node");
});

test("rowText: the title, else the request, else how a version came to be", () => {
  const chat = chatWith("v1");
  assert.equal(rowText(chat.versions[0]), "ask v1");
  assert.equal(rowText({ ...chat.versions[0], request: "" }), "Optimise the prompt");
  assert.equal(rowText({ ...chat.versions[0], title: "Red bike" }), "Red bike");
  const hand = addHandVersion(chat, { id: "h", now: 20, baseId: "v1", text: "x" });
  assert.equal(rowText(hand), "Edited by hand");
  hand.editedIn = "node";
  assert.equal(rowText(hand), "Edited in the node");
  assert.equal(
    rowText(addNodeVersion(chat, { id: "n", now: 21, text: "z" })),
    "Taken from the node"
  );
});

test("startOptions names hand and node versions", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  addNodeVersion(chat, { id: "n", now: 2, text: "from the node" });
  addVersion(chat, version("w", 3, { request: "add fog", baseId: "n" }));
  addHandVersion(chat, { id: "h", now: 4, baseId: "w", text: "fog!" });
  addHandVersion(chat, { id: "i", now: 5, baseId: "h", text: "fog!!", editedIn: "node" });
  addVersion(chat, version("x", 6, { request: "wider", baseId: "i" }));
  const first = (base) => startOptions(chat, "Hero", base)[0].label;
  assert.equal(first("x"), "Edit v5 (newest)");
  assert.equal(first("i"), "Edit v4 (edited in the node)");
  assert.equal(first("h"), "Edit v3 (edited by hand)");
  assert.equal(first("w"), "Edit v2 (add fog)");
  assert.equal(first("n"), "Edit v1 (taken from the node)");
  assert.deepEqual(
    startOptions(chat, "Hero")
      .map((o) => o.label)
      .slice(1),
    ["Edit current prompt (Hero)", "New prompt"]
  );
});

test("setVersionTitle keeps a trimmed title, and nothing empty", () => {
  const chat = chatWith("v1");
  const v = chat.versions[0];
  assert.equal(v.title, "");
  assert.equal(setVersionTitle(v, "  Red bike  "), true);
  assert.equal(v.title, "Red bike");
  assert.equal(setVersionTitle(v, "   "), false);
  assert.equal(setVersionTitle(null, "x"), false);
  assert.equal(v.title, "Red bike");
  const [saved] = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] }))).chats;
  assert.equal(saved.versions[0].title, "Red bike");
  // A Retry keeps the title: the request is the same.
  replaceVersion(chat, "v1", { now: 30, text: "again" });
  assert.equal(v.title, "Red bike");
});

// --- a node prompt matched to the chat's versions -----------------------------------

const DUSK = "A red bike by a lake at dusk, soft light, reeds in the water.";

function textChat(...texts) {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  texts.forEach((text, i) => addVersion(chat, version(`v${i + 1}`, 10 + i, { text })));
  return chat;
}

test("matchText finds the same text, newest first", () => {
  const chat = textChat(DUSK, "other", DUSK);
  const match = matchText(chat.versions, DUSK);
  assert.equal(match.how, "same");
  assert.equal(match.version.id, "v3");
});

test("matchText calls a small tweak close, to the version it is closest to", () => {
  const chat = textChat(DUSK, "Portrait of an old sailor with a pipe, harbour behind him.");
  const match = matchText(
    chat.versions,
    "A red bike by a calm lake at dusk, soft light, reeds in the water."
  );
  assert.equal(match.how, "close");
  assert.equal(match.version.id, "v1");
});

test("matchText calls whitespace and punctuation changes close", () => {
  const chat = textChat(DUSK);
  const match = matchText(
    chat.versions,
    "A red bike by a lake at dusk  soft light;\nreeds in the water"
  );
  assert.equal(match.how, "close");
  assert.equal(match.version.id, "v1");
});

test("matchText calls a different prompt nothing", () => {
  const chat = textChat(DUSK);
  assert.equal(matchText(chat.versions, "Portrait of an old sailor with a pipe."), null);
  assert.equal(matchText([], DUSK), null);
});

test("adoptText: the same version, a hand edit in the node, or a node version", () => {
  const chat = textChat(DUSK);
  assert.equal(adoptText(chat, DUSK, { id: "a", now: 50, source: "Hero" }).id, "v1");
  assert.equal(chat.versions.length, 1);
  assert.equal(chat.updated, 10);

  const tweak = adoptText(chat, DUSK.replace("dusk", "dawn"), { id: "b", now: 51, source: "Hero" });
  assert.equal(tweak.kind, KIND_HAND);
  assert.equal(tweak.editedIn, "node");
  assert.equal(tweak.baseId, "v1");
  assert.equal(tweak.n, 2);
  assert.equal(chat.updated, 51);

  const other = adoptText(chat, "Portrait of an old sailor.", { id: "c", now: 52, source: "Hero" });
  assert.equal(other.kind, KIND_NODE);
  assert.equal(other.baseId, null);
  assert.equal(other.source, "Hero");
  assert.equal(chat.versions.length, 3);
});

// --- old chats: the quoted node prompt becomes a version ----------------------------

const old = (chat) => normalizeStore({ version: 1, chats: [chat] }).chats[0];

test("migration: a quoted node prompt equal to an earlier version adds nothing", () => {
  const chat = old({
    id: "c",
    versions: [
      { id: "v1", text: DUSK, request: "a bike" },
      { id: "v2", text: "B", request: "morning", baseId: "prompt", basePrompt: DUSK },
    ],
  });
  assert.deepEqual(
    chat.versions.map((v) => [v.id, v.n, v.baseId]),
    [
      ["v1", 1, null],
      ["v2", 2, "v1"],
    ]
  );
  assert.equal("basePrompt" in chat.versions[1], false);
});

test("migration: a close node prompt becomes a hand edit inserted before", () => {
  const chat = old({
    id: "c",
    targetLabel: "Hero",
    versions: [
      { id: "v1", text: DUSK, request: "a bike", created: 5 },
      {
        id: "v2",
        text: "B",
        request: "morning",
        created: 9,
        baseId: "prompt",
        basePrompt: DUSK + " Fog.",
      },
      { id: "v3", text: "C", request: "wider", baseId: "v2" },
    ],
  });
  assert.deepEqual(
    chat.versions.map((v) => [v.id, v.n, v.kind, v.baseId]),
    [
      ["v1", 1, "written", null],
      ["v2~node", 2, "hand", "v1"],
      ["v2", 3, "written", "v2~node"],
      ["v3", 4, "written", "v2"],
    ]
  );
  assert.equal(chat.versions[1].editedIn, "node");
  assert.equal(chat.versions[1].created, 9);
  assert.equal(chat.versions[1].text, DUSK + " Fog.");
});

test("migration: another node prompt becomes a node version from the chat's target", () => {
  const chat = old({
    id: "c",
    targetLabel: "Hero",
    seedPrompt: "Portrait of an old sailor.",
    versions: [{ id: "v1", text: "A", request: "warmer", baseId: "seed" }],
  });
  assert.deepEqual(
    chat.versions.map((v) => [v.id, v.kind, v.source, v.baseId, v.text]),
    [
      ["v1~node", "node", "Hero", null, "Portrait of an old sailor."],
      ["v1", "written", null, "v1~node", "A"],
    ]
  );
});

test("migration: seed without a seed prompt, 'prompt' without text and unknown bases become roots", () => {
  const chat = old({
    id: "c",
    versions: [
      { id: "v1", text: "A", baseId: "seed" },
      { id: "v2", text: "B", baseId: "prompt" },
      { id: "v3", text: "C", baseId: "v9" },
      { id: "v4", text: "D", baseId: "v4" },
      { id: "v5", text: "E", baseId: "prompt", basePrompt: "  " },
    ],
  });
  assert.deepEqual(
    chat.versions.map((v) => v.baseId),
    [null, null, null, null, null]
  );
});

test("migration: a waiting question and a failure get their node prompt as a version at the end", () => {
  const chat = old({
    id: "c",
    updated: 77,
    versions: [{ id: "v1", text: DUSK }],
    pending: {
      request: "r",
      question: "Q?",
      baseId: "prompt",
      basePrompt: "Portrait of a sailor.",
    },
    failure: { request: "r", message: "m", baseId: "prompt", basePrompt: DUSK + " Fog." },
  });
  assert.deepEqual(
    chat.versions.map((v) => [v.id, v.kind, v.baseId, v.created]),
    [
      ["v1", "written", null, 0],
      ["c~pending", "node", null, 77],
      ["c~failure", "hand", "v1", 77],
    ]
  );
  assert.equal(chat.pending.baseId, "c~pending");
  assert.equal(chat.versions[1].text, "Portrait of a sailor.");
  assert.equal(chat.failure.baseId, "c~failure");
  assert.equal("basePrompt" in chat.pending, false);
  assert.equal("basePrompt" in chat.failure, false);
});

test("migration is idempotent", () => {
  const raw = {
    version: 1,
    chats: [
      {
        id: "c",
        seedPrompt: "Portrait of an old sailor.",
        versions: [
          { id: "v1", text: DUSK },
          { id: "v2", text: "B", baseId: "prompt", basePrompt: DUSK + " Fog." },
          { id: "v3", text: "C", baseId: "seed" },
        ],
        pending: { request: "r", question: "Q?", baseId: "prompt", basePrompt: "Other text." },
      },
    ],
  };
  const once = normalizeStore(raw);
  assert.deepEqual(normalizeStore(JSON.parse(JSON.stringify(once))), once);
});

// --- the graph, folding and the path -------------------------------------------------

const items = (...pairs) => pairs.map(([id, baseId = null]) => ({ id, baseId }));
const lanesOf = (layout) => Object.fromEntries(layout.rows.map((r) => [r.id, r.lane]));

test("historyLayout: a chain stays in one lane", () => {
  const layout = historyLayout(items(["a"], ["b", "a"], ["c", "b"], ["d", "c"]));
  assert.equal(layout.lanes, 1);
  assert.deepEqual(
    layout.rows.map((r) => [r.lane, r.parentRow]),
    [
      [0, null],
      [0, 0],
      [0, 1],
      [0, 2],
    ]
  );
});

test("historyLayout: branches get lanes of their own", () => {
  const layout = historyLayout(
    items(
      ["v1"],
      ["v2", "v1"],
      ["v3", "v2"],
      ["v4", "v3"],
      ["v5", "v2"],
      ["v6", "v5"],
      ["v7", "v3"],
      ["v8", "v4"]
    )
  );
  const lane = lanesOf(layout);
  for (const id of ["v1", "v2", "v3", "v4", "v8"]) assert.equal(lane[id], 0, id);
  assert.equal(lane.v5, lane.v6);
  assert.ok(lane.v5 > 0 && lane.v7 > 0 && lane.v5 !== lane.v7);
  assert.equal(layout.lanes, 3);
  assert.equal(layout.rows[4].parentRow, 1);
});

test("historyLayout: a lane is reused once its chain ended", () => {
  const layout = historyLayout(
    items(["a"], ["b", "a"], ["c", "a"], ["d", "b"], ["e", "d"], ["f", "d"])
  );
  const lane = lanesOf(layout);
  assert.equal(lane.c, 1);
  assert.equal(lane.f, 1);
  assert.equal(layout.lanes, 2);
});

test("historyLayout: a new start begins its own chain, and an unknown base is a start", () => {
  const layout = historyLayout(items(["a"], ["b", "a"], ["n"], ["m", "n"], ["next", "gone"]));
  const lane = lanesOf(layout);
  assert.equal(lane.a, 0);
  assert.equal(lane.b, 0);
  assert.equal(lane.n, 0);
  assert.equal(lane.m, 0);
  assert.equal(layout.rows[4].parentRow, null);
  assert.deepEqual(historyLayout([]), { lanes: 0, rows: [] });
});

function treeChat() {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const add = (id, baseId) => addVersion(chat, version(id, 10, { baseId }));
  add("v1", null);
  add("v2", "v1");
  add("v3", "v2");
  add("v4", "v3");
  add("v5", "v2");
  add("v6", "v5");
  return chat;
}

test("visibleVersions hides what came from a folded version, foldedCount counts it", () => {
  const chat = treeChat();
  assert.equal(canFold(chat, "v2"), true);
  assert.equal(canFold(chat, "v6"), false);
  assert.equal(foldedCount(chat, "v2"), 0);
  toggleFold(chat, "v5");
  assert.equal(foldedCount(chat, "v5"), 1);
  assert.deepEqual(
    visibleVersions(chat).map((v) => v.id),
    ["v1", "v2", "v3", "v4", "v5"]
  );
  toggleFold(chat, "v2");
  assert.deepEqual(
    visibleVersions(chat).map((v) => v.id),
    ["v1", "v2"]
  );
  assert.equal(foldedCount(chat, "v2"), 4);
  toggleFold(chat, "v2");
  assert.deepEqual(chat.folded, ["v5"]);
});

test("folding never hides the kept versions or their path, only what leads elsewhere", () => {
  const chat = treeChat();
  const keep = keptPaths(chat, ["v4", "gone"]);
  assert.deepEqual([...keep].sort(), ["v1", "v2", "v3", "v4"]);
  assert.equal(canFold(chat, "v2", keep), true);
  assert.equal(canFold(chat, "v3", keep), false);
  toggleFold(chat, "v2");
  assert.deepEqual(
    visibleVersions(chat, keep).map((v) => v.id),
    ["v1", "v2", "v3", "v4"]
  );
  assert.equal(foldedCount(chat, "v2", keep), 2);
  const both = keptPaths(chat, ["v4", "v6"]);
  assert.equal(visibleVersions(chat, both).length, 6);
  assert.equal(foldedCount(chat, "v2", both), 0);
});

test("folding may hide the composer's choice; unfoldTo shows it again", () => {
  const chat = treeChat();
  toggleFold(chat, "v2");
  toggleFold(chat, "v5");
  assert.equal(startValue(chat, "v6"), "v6");
  unfoldTo(chat, "v6");
  assert.deepEqual(chat.folded, []);
  assert.ok(visibleVersions(chat).some((v) => v.id === "v6"));
  toggleFold(chat, "v6");
  unfoldTo(chat, "v6");
  assert.deepEqual(chat.folded, ["v6"]);
});

test("pathTo walks back to the start", () => {
  const chat = treeChat();
  assert.deepEqual([...pathTo(chat, "v6")], ["v6", "v5", "v2", "v1"]);
  assert.deepEqual([...pathTo(chat, "gone")], []);
});

// --- hand edits -----------------------------------------------------------------------

test("editVersion keeps what the AI wrote and adds a hand edit under it", () => {
  const chat = chatWith("v1");
  const out = editVersion(chat, chat.versions[0], "my words", { id: "h", now: 30 });
  assert.equal(out.made, true);
  assert.equal(out.version.kind, KIND_HAND);
  assert.equal(out.version.baseId, "v1");
  assert.equal(out.version.text, "my words");
  assert.equal(chat.versions[0].text, "text v1");
  assert.equal(chat.versions[0].kind, KIND_WRITTEN);
});

test("editVersion changes an unused hand edit in place, a used one gets a follow-up", () => {
  const chat = chatWith("v1");
  const hand = editVersion(chat, chat.versions[0], "mine", { id: "h", now: 30 }).version;
  const again = editVersion(chat, hand, "mine, more", { id: "x", now: 31 });
  assert.deepEqual([again.made, again.version, hand.text], [false, hand, "mine, more"]);
  assert.equal(chat.versions.length, 2);

  const writing = editVersion(chat, hand, "while writing", {
    id: "y",
    now: 32,
    writingBaseId: "h",
  });
  assert.equal(writing.made, true);
  assert.equal(hand.text, "mine, more");

  const sent = writing.version;
  markSent(sent, 33);
  const after = editVersion(chat, sent, "after sending", { id: "z", now: 34 });
  assert.equal(after.made, true);
  assert.equal(after.version.baseId, sent.id);
  assert.equal(sent.text, "while writing");
});

test("editVersion ignores an empty or whitespace-only change", () => {
  const chat = chatWith("v1");
  assert.equal(editVersion(chat, chat.versions[0], "   ", { id: "h", now: 30 }), null);
  assert.equal(editVersion(chat, chat.versions[0], " text \n v1 ", { id: "h", now: 30 }), null);
  assert.equal(chat.versions.length, 1);
});

test("isUsed: sent, followed up, waiting, failed or being written from", () => {
  const chat = chatWith("v1", "v2");
  const [v1, v2] = chat.versions;
  assert.equal(isUsed(chat, v2), false);
  assert.equal(isUsed(chat, v2, "v2"), true);
  v2.baseId = "v1";
  assert.equal(isUsed(chat, v1), true);
  chat.pending = { baseId: "v2" };
  assert.equal(isUsed(chat, v2), true);
  chat.pending = null;
  chat.failure = { baseId: "v2" };
  assert.equal(isUsed(chat, v2), true);
  chat.failure = null;
  markSent(v2, 5);
  assert.equal(isUsed(chat, v2), true);
});

// --- Delete, Retry over a version, what a version was written with --------------

/** v1 → v2 → v3, and v4 from v1 again. */
function branchedChat() {
  const chat = chatWith("v1", "v2");
  addVersion(chat, version("v3", 20, { baseId: "v2" }));
  addVersion(chat, version("v4", 21, { baseId: "v1" }));
  chat.versions[1].baseId = "v1";
  return chat;
}

test("branchOf is a version and everything made from it, in order", () => {
  const chat = branchedChat();
  assert.deepEqual(ids(branchOf(chat, "v1")), ["v1", "v2", "v3", "v4"]);
  assert.deepEqual(ids(branchOf(chat, "v2")), ["v2", "v3"]);
  assert.deepEqual(ids(branchOf(chat, "v4")), ["v4"]);
  assert.deepEqual(branchOf(chat, "nope"), []);
});

test("hasFollowUps says whether a version was built on", () => {
  const chat = branchedChat();
  assert.equal(hasFollowUps(chat, "v1"), true);
  assert.equal(hasFollowUps(chat, "v2"), true);
  assert.equal(hasFollowUps(chat, "v3"), false);
});

test("deleteVersion takes the version and what was made from it; the other numbers stay", () => {
  const chat = branchedChat();
  chat.folded = ["v2", "v1"];
  assert.deepEqual(deleteVersion(chat, "v2", 99), ["v2", "v3"]);
  assert.deepEqual(
    chat.versions.map((v) => [v.id, v.n]),
    [
      ["v1", 1],
      ["v4", 4],
    ]
  );
  assert.deepEqual(chat.folded, ["v1"]);
  assert.equal(chat.updated, 99);
  assert.deepEqual(deleteVersion(chat, "nope", 100), []);
  assert.equal(chat.updated, 99);
});

test("numbers go on from the newest version: a gap stays, the newest's number comes back", () => {
  const chat = branchedChat();
  deleteVersion(chat, "v2", 30);
  assert.equal(nextVersionNumber(chat), 5);
  assert.equal(addVersion(chat, version("v5", 31)).n, 5);
  deleteVersion(chat, "v5", 32);
  assert.equal(addVersion(chat, version("v6", 33)).n, 5);
  const saved = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] })));
  assert.deepEqual(
    saved.chats[0].versions.map((v) => v.n),
    [1, 4, 5]
  );
  assert.equal(nextVersionNumber(createChat(emptyStore(), { id: "e", now: 1 })), 1);
});

test("replaceVersion writes a Retry's answer over the version, in its place", () => {
  const chat = chatWith("v1", "v2");
  const v2 = chat.versions[1];
  v2.baseId = "v1";
  markSent(v2, 3);
  const out = replaceVersion(chat, "v2", {
    now: 50,
    text: "again",
    model: "m2",
    systemPrompt: "t.txt",
    temperature: 0.9,
    thinkingLevel: "high",
    loraScope: "list",
    server: "nano-gpt.com",
    systemPromptKey: "k",
    cost: 0.2,
    seconds: 4,
    usage: { prompt_tokens: 3 },
    reasoning: "why",
  });
  assert.equal(out, v2);
  assert.deepEqual(ids(chat.versions), ["v1", "v2"]);
  assert.equal(v2.n, 2);
  assert.equal(v2.request, "ask v2");
  assert.equal(v2.baseId, "v1");
  assert.equal(v2.created, 11);
  assert.equal(v2.text, "again");
  assert.equal(v2.model, "m2");
  assert.equal(v2.temperature, 0.9);
  assert.equal(v2.thinkingLevel, "high");
  assert.equal(v2.loraScope, "list");
  assert.equal(v2.server, "nano-gpt.com");
  assert.equal(v2.systemPromptKey, "k");
  assert.equal(v2.reasoning, "why");
  assert.deepEqual(v2.suggestions, []);
  // The new text has not been sent anywhere yet.
  assert.equal(v2.sentAt, null);
  assert.equal(chat.updated, 50);
  assert.equal(replaceVersion(chat, "gone", { now: 51, text: "x" }), null);
});

test("a version keeps the settings it was written with, through a save", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  addVersion(
    chat,
    version("v1", 2, {
      temperature: 0.4,
      thinkingLevel: "",
      loraScope: "all",
      server: "localhost:1234",
      systemPromptKey: keepSystemPrompt(chat, "SYS"),
    })
  );
  const [again] = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] }))).chats;
  const v = again.versions[0];
  assert.equal(v.temperature, 0.4);
  assert.equal(v.thinkingLevel, "");
  assert.equal(v.loraScope, "all");
  assert.equal(v.server, "localhost:1234");
  assert.equal(systemPromptOf(again, v), "SYS");
});

test("normalizeStore repairs settings it cannot read", () => {
  const [chat] = normalizeStore({
    version: 1,
    chats: [
      {
        id: "c",
        versions: [
          {
            id: "v",
            text: "t",
            temperature: "hot",
            thinkingLevel: 3,
            loraScope: "some",
            server: {},
            systemPromptKey: "",
          },
        ],
      },
    ],
  }).chats;
  const v = chat.versions[0];
  assert.equal(v.temperature, null);
  assert.equal(v.thinkingLevel, null);
  assert.equal(v.loraScope, null);
  assert.equal(v.server, null);
  assert.equal(v.systemPromptKey, null);
  assert.deepEqual(chat.systemPrompts, {});
});

test("keepSystemPrompt keeps each text once per chat", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const a = keepSystemPrompt(chat, "You write prompts.");
  assert.equal(keepSystemPrompt(chat, "You write prompts."), a);
  const b = keepSystemPrompt(chat, "You write short prompts.");
  assert.notEqual(a, b);
  assert.deepEqual(Object.keys(chat.systemPrompts).sort(), [a, b].sort());
  assert.equal(keepSystemPrompt(chat, ""), null);
  assert.equal(keepSystemPrompt(chat, "   "), null);
  assert.equal(keepSystemPrompt(chat, undefined), null);
});

test("keepSystemPrompt gives a second text with the same key a key of its own", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const a = keepSystemPrompt(chat, "one");
  // Another text already held under the key "one" would get.
  chat.systemPrompts = { [a]: "something else" };
  const b = keepSystemPrompt(chat, "one");
  assert.notEqual(b, a);
  assert.equal(chat.systemPrompts[a], "something else");
  assert.equal(chat.systemPrompts[b], "one");
});

test("only system prompts a version still uses are kept", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const a = keepSystemPrompt(chat, "A");
  const b = keepSystemPrompt(chat, "B");
  addVersion(chat, version("v1", 2, { systemPromptKey: a }));
  addVersion(chat, version("v2", 3, { systemPromptKey: b }));
  keepSystemPrompt(chat, "never used");
  const [saved] = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] }))).chats;
  assert.deepEqual(saved.systemPrompts, { [a]: "A", [b]: "B" });
  deleteVersion(chat, "v2", 4);
  assert.deepEqual(chat.systemPrompts, { [a]: "A" });
  assert.equal(systemPromptOf(chat, { systemPromptKey: null }), null);
  assert.equal(systemPromptOf(chat, { systemPromptKey: "missing" }), null);
});

test("a Retry's question and failure remember the version they write over", () => {
  const chat = chatWith("v1", "v2");
  openQuestion(chat, { request: "r", baseId: "v1", replaceId: "v2" }, "Which?", 5);
  assert.equal(chat.pending.replaceId, "v2");
  recordFailure(chat, { request: "r", baseId: "v1", replaceId: "v2" }, "Timed out", 6);
  assert.equal(chat.failure.replaceId, "v2");
  const [saved] = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] }))).chats;
  assert.equal(saved.pending.replaceId, "v2");
  assert.equal(saved.failure.replaceId, "v2");
  // One that names no version any more writes a new one.
  chat.versions.pop();
  const [gone] = normalizeStore(JSON.parse(JSON.stringify({ version: 1, chats: [chat] }))).chats;
  assert.equal(gone.pending.replaceId, null);
  assert.equal(gone.failure.replaceId, null);
});

test("startLabel says which version a Retry writes over", () => {
  const chat = chatWith("v1", "v2");
  assert.equal(startLabel(chat, "v1", "v2"), "edit of v1 · retry of v2");
  assert.equal(startLabel(chat, null, "v2"), "new prompt · retry of v2");
  assert.equal(startLabel(chat, "v1", "gone"), "edit of v1");
  assert.equal(startLabel(chat, "v1"), "edit of v1");
});

test("a Retry's new system prompt takes the place of one no version uses any more", () => {
  const chat = createChat(emptyStore(), { id: "c", now: 1 });
  const old = keepSystemPrompt(chat, "OLD");
  addVersion(chat, version("v1", 2, { systemPromptKey: old }));
  const fresh = keepSystemPrompt(chat, "NEW");
  replaceVersion(chat, "v1", { now: 3, text: "again", systemPromptKey: fresh });
  assert.deepEqual(chat.systemPrompts, { [fresh]: "NEW" });
  assert.equal(systemPromptOf(chat, chat.versions[0]), "NEW");
});

test("untitledVersions: written versions with a request and no title, newest chat first", () => {
  const store = emptyStore();
  const old = createChat(store, { id: "old", now: 1 });
  addVersion(old, version("a", 2));
  addVersion(old, version("b", 3, { title: "Has one" }));
  addVersion(old, version("c", 4, { request: "  " }));
  addHandVersion(old, { id: "h", now: 5, baseId: "a", text: "by hand" });
  const fresh = createChat(store, { id: "fresh", now: 1 });
  addVersion(fresh, version("d", 9));
  assert.deepEqual(
    untitledVersions(store).map(({ chat, version: v }) => [chat.id, v.id]),
    [
      ["fresh", "d"],
      ["old", "a"],
    ]
  );
  assert.deepEqual(untitledVersions(emptyStore()), []);
});
