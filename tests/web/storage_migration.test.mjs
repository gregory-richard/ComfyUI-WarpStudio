import assert from "node:assert/strict";
import { test } from "node:test";

import { STORAGE_KEYS, carryOverStorage } from "../../web/storage_migration.js";

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
  };
}

test("copies an old key", () => {
  const [oldKey, newKey] = STORAGE_KEYS[0];
  const storage = fakeStorage({ [oldKey]: "job-1" });
  carryOverStorage(storage);
  assert.equal(storage.getItem(newKey), "job-1");
  assert.equal(storage.getItem(oldKey), "job-1");
});

test("never overwrites", () => {
  const [oldKey, newKey] = STORAGE_KEYS[1];
  const storage = fakeStorage({ [oldKey]: "old", [newKey]: "new" });
  carryOverStorage(storage);
  assert.equal(storage.getItem(newKey), "new");
  assert.equal(storage.getItem(oldKey), "old");
});

test("survives a throwing storage", () => {
  const storage = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };
  assert.doesNotThrow(() => carryOverStorage(storage));
  assert.doesNotThrow(() => carryOverStorage(null));
});
