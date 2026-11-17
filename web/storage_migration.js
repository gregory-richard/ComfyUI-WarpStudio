/* Carries the Rename view's remembered choices over from Civitai Updater's
   browser-storage keys to Warp Studio's. Pure module: no ComfyUI imports. */

export const STORAGE_KEYS = [
  ["civitai-updater.rename.dryRunJob", "warp-studio.rename.dryRunJob"],
  ["civitai-updater.rename.selection", "warp-studio.rename.selection"],
  ["civitai-updater.rename.unticked", "warp-studio.rename.unticked"],
];

/* Copies each old key to its new key when the new one is absent. Old keys are kept.
   Blocked or private-window storage throws; that just means nothing is carried over. */
export function carryOverStorage(storage) {
  for (const [oldKey, newKey] of STORAGE_KEYS) {
    try {
      if (storage.getItem(newKey) !== null) continue;
      const value = storage.getItem(oldKey);
      if (value !== null) storage.setItem(newKey, value);
    } catch {
      /* storage unavailable or full: skip this key */
    }
  }
}
