import { app } from "../../scripts/app.js";

// The Browse LoRAs button on the Prompt + LoRAs node.
//
// The library itself is the Gallery in the Warp Studio sidebar panel. The
// button only asks the panel to open on it, with this node as the target.

const NODE_ID = "Warp Lora Prompt";

app.registerExtension({
  name: "warpstudio.loraBrowser",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_ID) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      this.addWidget("button", "Browse LoRAs", null, () => {
        window.dispatchEvent(
          new CustomEvent("warpstudio:open-gallery", { detail: { nodeId: this.id } })
        );
      });
      return result;
    };
  },
});
