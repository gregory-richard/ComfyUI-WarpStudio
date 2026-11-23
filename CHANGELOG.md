# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-11-23

### Security

- **The Prompt Writer's and the Gallery's routes now need the panel's header**, as the updater's always did. Before, another web page open in the same browser could save a Prompt Writer key, or have ComfyUI send a prompt with pictures from its output folder to an address of that page's choosing. All `/warp-studio/llm/*` routes, `/warp-studio/loras`, `/models`, `/embeddings`, `/loras/missing` and `/model/base` refuse a request without `X-Civitai-Updater: 1`, a cross-site request, and a POST that isn't `application/json`
- **The Prompt Writer sends only to providers you chose**: NanoGPT, a server on this computer, or a host you saved a key for under ⚙. ComfyUI's settings can be changed by other code, so an address there alone is no longer enough. Another provider shows "… has no saved key"; save its key once (any text, for one that needs none) and it works as before. Addresses with a user name, a backslash, `?` or `#` are refused, and so is a redirect from a provider to this computer or the local network
- **Model names stay inside the model folders**: a name with a drive, a leading slash or a `..` step finds nothing, and a lookup that answers with a path outside ComfyUI's folders for that type is refused. This covers `/warp-studio/model/base`, the thumbnails and the LoRA tags in prompts
- **Thumbnails** refuse requests from other sites, so a web page can't use them to learn which models you have (when ComfyUI is opened on this computer or over https; browsers don't say where a request came from over plain http on a LAN), and answer only for a model file's name
- A **Network and privacy** section in the README lists every host Warp Studio talks to and what it sends

### Added

- A **Gallery** tab, first in the sidebar panel: your LoRAs as cards with previews, search, and filters by type and base model.
- A target node: the selected Prompt + LoRAs node, or else the last one you used. The cards' marks show which one. A line shows only when nothing can be added: **Select a Prompt + LoRAs node** or **No Prompt + LoRAs node in this workflow**.
- **A click on a Gallery card goes in three steps**, and never deletes a line. It adds `<lora:name:1.00>` as a new line at the end of the node's LoRA list (✓), parks it as `// <lora:…>` (a hollow mark), then switches it on again. The marks follow the list when you edit it by hand. The second-pass list gets the LoRA only when it is on and was the last list you typed in
- **Models and Embeddings** in the Gallery, with a switch at the top. A click on a model sets the selected Load Checkpoint or Load Diffusion Model node; a click on an embedding adds `embedding:name` to the prompt once
- **Stacked versions**: one card per Civitai model per base, with a **n versions ▾** chip to pick one. Its click, marks and Delete act for that version
- **Bands fold** when you click the base header, and stay folded after a reload. The band matching the target node's model is marked **for this workflow** and follows workflow switches
- **A toolbar on every tab**: 🔍, **Filter** and **Sort** in the Gallery; **Filter**, **Sort** and ⓘ in Updates and Issues; **Filter** in Rename. The ⓘ holds when it was last checked, the counts, "Changes detected" and **Check again**. The Gallery's cards use the file's `creator - title - version (base)` name, and the search also finds Civitai's old model name
- **A download clicked while another job runs waits** as "Waiting for the check" (or the dry run, the rename, the metadata fetch, the naming model download) and starts when that job ends. **Cancel** removes it. Waiting downloads are kept in memory, so a ComfyUI restart drops them
- **Browse LoRAs** on a Prompt + LoRAs node opens the panel on the Gallery with that node as the target.
- **Download** and **Replace** on the Updates cards. Downloads run on the server, one at a time, with a queue, **Stop**, progress in the job bar and a SHA-256 check. Replace sends the old version to the Recycle Bin only after the new file is checked and in place. A release in early access shows **Early access**. Most adult models need the Civitai API key.
- **Include downloadable** in the Gallery's **Filter**, offered only when some LoRAs have an info file here but no weights: they show as dimmed cards with **Download**, which puts the file next to the info file under the same name.
- **Delete** on Gallery cards: **Delete?** for four seconds, then the weights and their sidecars go to the Recycle Bin, weights first.

### Changed

- The sidebar panel is called **Warp Studio**, not Civitai.
- The Browse LoRAs pop-up is gone; the Gallery replaces it.
- The panel has a header with the title and a ⚙. The settings card moved behind the ⚙: model scope, **Force rehash**, **Refetch metadata** and the resolved roots. The subtitle and the Gallery's "Adds to" line are gone, and **Added** is no longer flashed
- **Check for Updates** and **Fetch Missing Metadata** moved to the top of Updates and Issues. On fresh saved results, Check for Updates shows "Results from … ago" with **Check again** for about five seconds. The "cached" pill and the status line are gone
- The job bar sits under the tabs, in every tab, only while a job runs. Errors stay inline
- Updates rows are one line again: **Download**, **Replace** and **Hide** sit at the right end, and a long version name ends in "…"

### Fixed

- The Updates list no longer jumps during a check: the thin loading bar stays off for the reloads a check makes, the list reloads only when the count changes, and unchanged cards stay in place

## [1.0.0] - 2026-11-18

Warp Studio is WarpPipe and Civitai Updater in one package. Nothing looks
different: the nodes, the Prompt Writer, the LoRA browser and the **Civitai**
sidebar panel work as before.

What changed is the name. The package is `warp-studio`. Settings sit under
**Warp Studio** in ComfyUI's settings, and the updater's groups are "Civitai
Network", "Civitai General", "Civitai Path Sources" and so on. HTTP routes start
with `/warp-studio/`, and the updater's with `/warp-studio/updater/`. Data is kept in
`ComfyUI/user/warp-studio/`: on the first start, settings, chats, API keys, the
request log, hashes and the updater's data are copied over from the old packages
(the old files stay where they were). Warp Studio switches itself off, with a
message, while WarpPipe before 5.1.0 or Civitai Updater before 1.10.0 is still
active.

### New for Civitai Updater users

Everything since 1.9.2.

#### Added

- **A Rename view** next to **Updates** and **Issues** renames your model files after their current Civitai releases, as `creator - title - version [precision] (base)`, all lowercase. Precision is given only to checkpoints and UNets, never to LoRAs. It is read from the file, or for a metadata-only entry taken from Civitai's file info when the release has a single main model file. Sidecars, previews and any other file sharing the model's name are renamed with it
- **A local naming model** cleans the names. ComfyUI's built-in **Generate Text** node runs it through ComfyUI's queue, so its jobs show in the queue history. Pick it under **Civitai Rename** in the settings, or download Comfy-Org's `qwen3.5_9b_int8_convrot.safetensors` (9.75 GB) from the view. A ComfyUI without Generate Text cannot run the renamer, and the view says so
- **A dry run** lists every old name next to its new one and changes nothing. You review it, one card per model as in **Updates** (flags, edits, **Keep current name**, **Ask again**, extra copies to remove), and then apply it: the button says what it will do, as in **Rename 12 · remove 2**, and asks once before **Apply**. Edits and applied names are remembered per model; applied names stay locked until **Ask again**
- **Kept, not renamed**: models Civitai no longer serves, the VAE folder, files whose Civitai match is not the release's main model file or whose type does not fit the folder, and anything you keep
- **A rename cut off by a crash or restart** can be finished or undone from the Rename view. Extra copies you remove go to the Recycle Bin, through the new `send2trash` requirement
- **Clashes are asked about once more** before they are flagged: two versions of a model that would get the same name, and two models by one creator that would get the same title under the same base code. Still clashing, the rows are flagged and not ticked
- **The maker of a re-uploaded official model** is read from "originally posted/released ..." lines and the owners of its Hugging Face, GitHub or ModelScope links. An official file whose maker is not found is flagged, so you set the creator yourself; a maker from the description offers **Use <maker>** or **Use <uploader>**
- **Flagged rows** show the model's Civitai title and uploader, and a clash names the other file or model. **Keep this name** is now **Keep current name**
- **A preview fallback** for files Civitai can't serve. **Fetch Missing Metadata** asks other sources in order, and the first answer wins: your own **ai-toolkit** trainings (their sample images), **Hugging Face** authors and repos (matched by file hash) and **civarchive** (by hash; off until you turn it on). A hit writes a preview and fills only the empty fields of the info file; it never replaces a preview you have and never changes a file Civitai identifies. Hashes no source knew are not asked about again for 30 days. The sources are set under **Civitai Preview Fallback** in the settings, and the panel shows where a file was found
- **The renamer names those files too**: a file the preview fallback identified is named from the info it found. These rows start unticked, and a title that runs words together is asked about once more

#### Changed

- A card in **Updates** lists its versions as one list, newest on top. Saved files, new releases and hidden releases are in release-date order, so the newest release no longer sits under the oldest saved file
- The loading spinner and bar are calmer, and the bar stops when the panel is hidden. Cards rise in after a page reload
- There is no backwards compatibility in the renamer: saved workflows and images that use the old names show those models as missing until you re-pick them
- The naming model gets roughly three in four names exactly right on models it has not seen in testing. Review the list before you apply it

### New for WarpPipe users

#### Added

- **The Prompt Writer shows a chat's history as a graph**: the versions stay in the order they were made, numbered v1, v2, v3, with a line graph on their left like git history that shows which version came from which. The version the next request edits has a ring on its dot, and it and its path back to the start are lit. **A click on a dot makes that version the input** of the next request, so the list under **Edit …** holds only that one version, the current prompt and a new prompt
- **Branches fold**: the − beside a version's dot in the graph folds the versions made from it that lead away from where you are working, and its card shows how many (`+2`); + shows them again. The newest version, the one the next request edits and their paths never fold away. Folds are saved with the chat; picking a folded version under **What the next request does** shows it again
- **Hand edits are versions**: typing in the newest version and clicking away keeps what the AI wrote and adds your text as a new version under it (a hollow dot). Until it is sent or written from, more typing changes that hand edit itself
- **Edit current prompt makes the node's prompt a version**: if it is a version of the chat, that version is edited; if it is a tweak of one (at most 60% of the words changed), it becomes a hand edit of it, "in the node"; anything else starts a new line "from" the node (a square dot). So the prompt you edited can be chosen again after **Send to ComfyUI** has replaced it
- **A ⋮ menu on every version** with **Generation info**, **Use as input**, **Retry**, **Recreate** and **Delete**, each with a line that says what it does. **Retry** writes a version the AI wrote again at once, from the same request, and replaces it in place with the same number (not once other versions were made from it; a question or a failure on the way still ends in that version). **Recreate** opens the version's request in a field where it was shown, to change or leave as it is, and writes a new version beside it from the same version and images; it replaces **Change the request**, which went through the input. Both use the model and settings chosen now. **Delete** asks first, in the menu, and removes the version with every version made from it; the other versions keep their numbers
- **Generation info**: everything about how a version came to be, together: what it was made from, when, the model, temperature, thinking level, LoRAs offered and server, its cost, time and tokens, your request and the questions on the way, the prompt it edited, the system prompt's text as it was read for that write, and the thinking. The text is kept once per chat, and the request log still holds only the file's name. Versions from before show the model and the system prompt's name
- **Version titles**: while a version is written, the title model names it in a few words, like a chat: what it shows for a new prompt, what it changes for an edit. A Recreate with the request unchanged keeps the title. Versions from before are named once, in the background, when the Prompt Writer opens after ComfyUI was restarted with this version

#### Changed

- **The ✨ AI button is now ✨ AI Prompt Writer**, centred just above the prompt box in the empty middle of the node's last row of slots, so it no longer covers the text and takes no room from it. On a narrow node it says only **✨ AI**
- **Send to ComfyUI (Group)** (until now **Send to prompt**) is the one button under an open version, named for the group or node chosen under **For**. The rest moved into the version's ⋮, replacing the pencil and the Again arrow
- **Calmer versions**: each version is one line, its number and title, and only one is open at a time; opening one closes the other, and the line you clicked stays where it is. Where a version came from is in the graph and its generation info. Above an open version's prompt, **Request** (your request and the questions on the way, open until you close it), **Changes** (`+38 −12`; replaces **Show changes**) and **LoRAs** (how many were on and how many are suggested), so opening them moves nothing. The LoRA choice in the input is no longer filled in blue
- **The input box** has its settings (images, what the next request does, LoRAs) as a bar along its top and **↑** in the corner of the text, and lines up with the versions above it
- **Version numbers are saved with the chat**, so deleting one leaves a gap instead of renumbering the rest
- **Older chats are converted once when opened**: the node prompt each **Edit current prompt** started from, kept until now only as "The current prompt it edited", becomes a version (or points to the one it equals), so their numbers may shift once. A file converted this way still opens in 5.0.0, which shows the new versions without a request
- **A click outside the Prompt Writer closes it**, like Esc and ×. Only a press that starts and ends outside counts, so selecting text and letting go past the edge leaves it open. Writing goes on, and what you typed is still there when you open it again
- **A version's cost, time, tokens and thinking** are in its generation info, instead of a line at its top
- **Thinking stays closed** while the model writes too; open its switch to watch. Once opened, it stays open while the card updates

#### Fixed

- **An empty chat picked from the list** follows the Prompt + LoRAs you opened the Prompt Writer from, as **New chat** does. It kept the node it was first opened for, so **Edit current prompt** and **Send to ComfyUI** could name another group than the one you came from

---

The changelogs of the two packages before they merged are kept in
[docs/warppipe/CHANGELOG.md](docs/warppipe/CHANGELOG.md) and
[docs/civitai-updater/CHANGELOG.md](docs/civitai-updater/CHANGELOG.md).
