import { app } from "../../scripts/app.js";

// Warp Studio is switched off while an old WarpPipe or Civitai Updater is
// installed. This is the only thing it does: say why.
app.registerExtension({
  name: "warpstudio.standdown",
  async setup() {
    try {
      const response = await fetch("/warp-studio/standdown");
      if (!response.ok) return;
      const { message } = await response.json();
      app.extensionManager.toast.add({
        severity: "warn",
        summary: "Warp Studio is switched off",
        detail: message,
      });
    } catch (error) {
      console.warn("Warp Studio stand-down notice failed", error);
    }
  },
});
