import { app } from "../../scripts/app.js";
import { requestGallery } from "./prompt_target.js";

// The Browse LoRAs button on the Prompt + LoRAs node.
//
// The library itself is the Gallery in the Warp Studio sidebar panel. The
// button only asks the panel to open on it, with this node as the target:
// requestGallery selects the node first, because a button click does not.

const NODE_ID = "Warp Lora Prompt";

app.registerExtension({
  name: "warpstudio.loraBrowser",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_ID) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      this.addWidget("button", "Browse LoRAs", null, () => requestGallery(app, this));
      return result;
    };
  },
});
