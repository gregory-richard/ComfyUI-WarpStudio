# Warp Studio documentation

Everything beyond the [README](README.md). Each node also carries its own help
inside ComfyUI, behind the `?` on the node.

- [Getting started](#getting-started)
- [Usage](#usage)
- [Warp Studio panel](#warp-studio-panel)
- [Node reference](#node-reference)
- [HTTP API](#http-api)
- [Architecture](#architecture)
- [Development](#development)
- [Troubleshooting](#troubleshooting)


---

## Getting started

Three things, in one node pack.

**A prompt box that owns its LoRAs.** Write `<lora:name:weight>` in the list
under the prompt, the way A1111 does, and the tags *are* the loader. The text is
the only state: colouring, completion, weights, ordering and switching a LoRA
off are all edits to it.

**One wire instead of twenty.** A Warp bundles everything a generation needs;
an Unwarp gives it back. Whole model setups become a single connection you move.

**A sidebar panel for your model library.** The **Warp Studio** tab shows your LoRAs as a gallery, checks your models
against their Civitai releases, renames your files into one scheme and finds
previews for models Civitai no longer serves. It is described under
[Warp Studio panel](#warp-studio-panel).

Warp Studio is WarpPipe and Civitai Updater in one package. If you used either,
see [Coming from WarpPipe or Civitai Updater](#coming-from-warppipe-or-civitai-updater).

Each node also carries its own help inside ComfyUI. The `?` on a node opens the
matching page from [`web/docs/`](web/docs) without leaving the canvas.

### Installing

From the ComfyUI Registry:

```bash
comfy node install warp-studio
```

Or by hand, into `ComfyUI/custom_nodes`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/gregory-richard/ComfyUI-WarpStudio.git
```

Restart ComfyUI afterwards either way. If you keep the source elsewhere, link it
in rather than copying — see [Development](#setting-up).

### What it needs

ComfyUI and Python 3.9 or newer. The nodes use only what ships with ComfyUI
(`Pillow`, `aiohttp`, `torch`). The Warp Studio panel adds `requests`, `Pillow`,
`imageio-ffmpeg` (only to turn video previews into thumbnails) and `send2trash`
(only to move the renamer's extra copies to the Recycle Bin). The registry
installs them; for a clone, run `pip install -r requirements.txt` with ComfyUI's
Python.

### Coming from WarpPipe or Civitai Updater

1. Install Warp Studio.
2. Update the old package in the Manager, or disable it there. The old and new
   packages must not both be active: the nodes, the panel and the routes would
   register twice. While WarpPipe before 5.1.0 or Civitai Updater before 1.10.0
   is still active, Warp Studio switches itself off, with a message, until the
   old one is updated or disabled.
3. Restart ComfyUI.

Don't uninstall Civitai Updater until Warp Studio has started once: your updater data lives in its folder and is copied on that first start.

On the first start, Warp Studio copies your data to its own names. It copies and
never moves, never overwrites a file that is already there, and leaves the old
files in place. If a copy fails, it tries again on the next start.

| Was | Now |
| --- | --- |
| settings `WarpPipe.*` | `WarpStudio.*` |
| settings `CivitaiUpdater.*` | `WarpStudio.Updater.*` |
| `user/warppipe_llm_keys.json`, `user/warppipe_llm_log.jsonl`, `user/warppipe_hashes.json` | `user/warp-studio/llm_keys.json`, `user/warp-studio/llm_log.jsonl`, `user/warp-studio/hashes.json` |
| `warppipe_prompt_chats.json`, per ComfyUI user | `warp_studio_prompt_chats.json` |
| `<package>/.civitai_updater/` (the updater's package folder) | `user/warp-studio/updater/` |
| routes `/warppipe/...` | `/warp-studio/...` |
| routes `/civitai-updater/...` | `/warp-studio/updater/...` |

Settings appear under **Warp Studio** in ComfyUI's settings; the updater's
groups are "Civitai Network", "Civitai General", "Civitai Path Sources",
"Civitai Custom Paths", "Civitai Preview Fallback" and "Civitai Rename". The
Rename view's saved choices in the browser are carried over too. Saved workflows
keep working: node ids and the `WARPPIPE` link type did not change.

### A quick tour

The shortest useful workflow is three nodes:

```
Load Checkpoint ──► Prompt + LoRAs ──► KSampler
                    (model, clip, prompt)
```

Type a prompt, press `/`, pick a LoRA, press `Tab`. The tag lands on a line of
its own and the LoRA is applied to the model and CLIP that come out.

The bundling is worth adding once a workflow has more than one model setup in
it:

```
Load Checkpoint ─┐
Warp Provider  ──┼──► Warp ────────► Unwarp ──► KSampler ──► VAE Decode
Prompt + LoRAs ──┘      │                                        │
                        └────────────────► Save Image (Civitai) ◄┘
```

The middle link carries the lot. Build a second Warp with a different checkpoint
and different steps, and switching between them is one connection moved.

### Version

This documents **1.0.0**. See the [changelog](CHANGELOG.md) for what changed,
and [Development](#development) for how releases are cut.

---

## Usage

### Writing a prompt

The **Prompt + LoRAs** node is a pair of text boxes that understand what is in
them: the prose above, and the LoRAs it uses below.

![The prompt box, with every colour state showing](assets/docs/prompt-node.webp)

The prompt is prose. The LoRAs go in the short box under it, A1111 style, one
tag to a line:

```
Prompt   a portrait in a sunlit kitchen, golden hour, soft bokeh
         highly detailed, 35mm film      // pushed up from 0.6

LoRAs    <lora:atelier - Detail Tweaker - v2.0 (SDXL):0.80>
         <lora:northlight - Golden Hour Portrait - v3.1 (SDXL):0.65>
         // <lora:atelier - Film Photography - v1.4 (SDXL):0.40>   parked
```

A tag written into the prompt itself still loads — that is how an A1111 prompt
arrives — and the strip offers to move those down into the list; clicking
anywhere on that line does it.

Every box has a short bar at the middle of its bottom edge — the whole edge is
the handle — and each is sized on its own. Drag the prompt's, under the strip,
and the prompt grows or shrinks by exactly that; drag a list's and the list
does. Either way the boxes below move with the edge and the node grows or
shrinks by the same amount, so setting one box never squeezes another. Every
drag is one step of undo and is saved with the workflow: a list's height in the
node's properties, the prompt's as the node's own size, which is what it takes
up once the lists have theirs. Resizing the node from its corner still gives
the room to the prompt.

A list grows with what it holds, up to six lines, until its height is set by
hand; double-click its band to have it fit the list again. Double-click the
prompt's band and the prompt becomes just tall enough for its text, up to
forty lines.

The node's **model** and **clip** outputs come out patched with every tag it
resolved. Its **prompt** output is what you wrote with the tags and the notes
taken out — and nothing added.

Switching **Model 2** to *its own LoRAs* opens a second list for the second
pass of a two-model sampler — base then turbo, high noise then low. It feeds
the **model_2** output, the two lists are read independently, and while the
switch is off the second list is not read at all.

#### What the colours mean

| | |
| --- | --- |
| cyan | a tag matching a file, built for the model you have connected |
| red, wavy underline | a tag matching nothing — check the spelling |
| orange, dotted underline | a real file, but built for another base model |
| amber | an embedding |
| green | a trigger word belonging to one of the LoRAs in use |
| dim green | a trigger word whose LoRA is not loaded — switched off with `//`, at weight 0, or in the second list while it is off |
| grey italic | a note after `//`, which is not sent to the model |

Red and orange are different complaints. Red will not load at all. Orange loads
perfectly well and usually does nothing useful — you may know something the
sidecar does not. Hover either for the reason.

Dim green is still sent — it is prompt text like any other — but nothing it was
written for is loaded to answer it. A word two LoRAs declare stays bright while
either of them is loaded.

With nothing wired into **model**, or for a file whose sidecar declares no base,
nothing can be judged and nothing goes orange. A Turbo counts as the model it
was distilled from, so a Krea 2 LoRA is not orange on a Krea 2 Turbo checkpoint.

A checkpoint without a sidecar is known by its folder instead: `moss` for
`Unet\moss\kromoss.safetensors`, which came from Hugging Face and will never
have one. LoRAs filed in the folder of the same name, `Lora\moss\`, count as
made for it whatever their sidecars call the base, so a properly tagged
`MOSS Image` LoRA is not orange there. Everything else is compared with the
folder name, as before.

#### Naming a LoRA

A tag does not need the full filename, only enough to identify one file.
Matching is tried in this order, ignoring case and slash direction:

1. the full name — `sdxl/atelier - Detail Tweaker - v2.0 (SDXL).safetensors`
2. the filename without folder or extension — `atelier - Detail Tweaker - v2.0 (SDXL)`
3. every word, in any order — `sdxl detail tweaker` finds the above
4. a unique fragment — `detail tweaker`

Option 2 always works and is what the Gallery inserts. Option 3 is usually the
shortest thing worth typing.

**A tag that matches nothing, or matches several files, stops the run** and says
what it matched. That is deliberate: generating without a LoRA the prompt asked
for gives a wrong picture *and* wrong metadata, and a console warning is too easy
to miss. A near miss suggests the closest names, so a typo is a question rather
than a dead end.

#### Completing with `/`

![Typing slash completes against the library inline](assets/docs/inline-completion.webp)

The suggestion is drawn in grey rather than shown in a popover, so it never
covers what you are typing, and it is filtered to the base model you have wired
in. `Tab` takes it. Taking a LoRA that declares trigger words offers them
straight away as a second suggestion, so the common pair — add it, then say its
word — is `Tab` twice and no dialog.

Nothing is written into the box until you accept. The suggestion cannot be typed
over, cannot land in the undo history, and does not mark the workflow changed
while you are only looking.

#### Keys

| | |
| --- | --- |
| `/` | complete against the library, inline |
| `Tab` | take the suggestion; on an existing tag, offer that LoRA's trigger words |
| `↑` `↓` | walk the alternatives while a suggestion is showing |
| `Ctrl+↑` `Ctrl+↓` | change the weight of the tag the caret is in, in steps of 0.1 |
| `Ctrl+/` | comment the line, or every selected line, out or back in; a commented LoRA is off |
| `Alt+↑` `Alt+↓` | move the line, which reorders when LoRAs are applied |
| `Esc` | drop the suggestion |

Each of these is an ordinary edit to the text, which is why a tag gets a line to
itself. It also means `Ctrl+Z` and `Ctrl+Y` understand all of them, each as one
step.

#### Sections

A line starting with `////` is a section header, and the section runs to the next
one. Sections fold while you type: ▾ and ▸ sit in a slim margin beside each header,
and a folded one reads `//// Style ··· 6 lines`. A box without headers has no margin.
Folding only changes what is shown, so folded LoRAs still load and folded text still
reaches the model. To switch something off, select the lines and press `Ctrl+/`. The
folded sections are saved in the node.

The boxes are a code editor (CodeMirror 6) laid over ComfyUI's own text box, which
stays in place, hidden, and is still what ComfyUI saves and queues from. The setting
`WarpStudio.CodeEditor`, under Warp Studio, turns the editor off and takes effect at once.
Off, or if the editor cannot load, a box is a plain text box without folding.

#### Trigger words

Nothing is added to your prompt. `Tab` on a tag offers that LoRA's words, and
the strip lists the first six as buttons; either way you pick, and they land in
the prompt, at the end of the line the caret was last on, as ordinary prompt
text. The grey offer is drawn there too, so it is shown where it will land.

That is the point of choosing. Appending them silently would send words you never
saw, and creators do not always put a keyword in `trainedWords` — in one real
collection the longest entry ran to 655 characters.

Once they are in, any of them says whose it is. **Hover** a green word for the
LoRA it belongs to — and, with two lists, which model it is on. **Put the caret
in one** and the strip shows that LoRA as it would for its tag, led by the word,
with `off` when it is not loaded and `+1` when another LoRA declares the same
word. While the caret is in a word or a tag, that LoRA's tag and all of its
words are picked out with a soft background in every box, so either one shows
where the other went.

#### The gallery

**Browse LoRAs** opens the **Warp Studio** sidebar panel on its Gallery tab, with
this node as the target. The Gallery is described under
[The Gallery tab](#the-gallery-tab); there is no separate pop-up.

A click on a card adds its tag to the node's LoRA list, on a line of its own; a second click parks it, a third switches it on again.

### Bundling a generation

![Warp bundling three sources; Unwarp giving them back](assets/docs/warp-unwarp.webp)

Connect anything you like into a **Warp** — models, CLIP, VAE, conditioning,
images, masks, latents, prompts, and the sampling parameters. One `WARPPIPE`
link carries all of it. An **Unwarp** unpacks it into twenty-two outputs in a
fixed order.

Two things worth knowing:

- **Warps chain.** Give a Warp another warp on its `warp` input and it starts
  from that one's contents, then applies whatever else is connected. Build a
  base warp and extend it per branch.
- **Absent is not default.** A field nothing was connected to comes back as
  `None`, not as an invented `euler` or `512`. If a downstream node needs a
  value, connect one.

The intended shape is one Warp per model or style — each with its own steps,
CFG, sampler, scheduler and resolution — and switching between them by moving
the single link into your Unwarp.

#### Parameters and latents

**Warp Provider** is the other half: one node holding the seed, steps, CFG,
sampler, scheduler and size, and producing the empty latent to match.

![Warp Provider](assets/docs/warp-provider.webp)

Thirty-two presets cover every common aspect ratio from 9:16 to 16:9, sorted
from tallest to widest and then by size. Each says only what it is —
`3:4 · 1152×1536 · 1.8 MP` — so it can be read in a node 280 pixels wide. Pick
`Custom` to use the `custom_width` and `custom_height` boxes.

Every side is a multiple of 16, which is what every family after SDXL needs:
FLUX.2, MOSS-Image and Qwen-Image 2.1 work on a sixteenth-size latent, and
Qwen-Image, Z-Image, Krea 2 and Anima cut an eighth-size one into 2×2 patches.
What tells the families apart is the pixel count they were trained for, which
is why every label ends with it:

| Family | Comfortable at | Notes |
| --- | --- | --- |
| SDXL | ~1 MP | Trained on 1 MP buckets; above ~1.5 MP subjects start to double |
| Anima | ~1 MP | Up to ~2.4 MP, weaker the higher it goes |
| Krea 2 | ~1 MP (Raw), 1–2 MP (Turbo) | Turbo goes to 2048×2048 |
| FLUX.2 | 1–2 MP | Up to 4 MP |
| MOSS-Image | 1–2 MP | Up to 4 MP; Kromoss was tuned at 1 MP |
| Z-Image | 1–2 MP | Anything from 512×512 to 2048×2048 in area |
| Qwen-Image 2.1 | ~4 MP | Native at 2K; 1 MP still works |

The latent is four channels at an eighth scale, labelled as such, exactly as
ComfyUI's own Empty Latent Image makes it — and that label is what lets it feed
any model. KSampler and SamplerCustomAdvanced reshape an empty latent to the
model's own format: the channels always, and the size whenever the latent says
which scale it was made at. So the same Provider drives SDXL, FLUX.2 and
MOSS-Image alike, and each renders at the size the preset names. Samplers from
other packs that skip that step (RES4LYF's, Impact Pack's) leave a sixteenth-size
model at twice the size, exactly as they would with Empty Latent Image.

### Saving for Civitai

![Save Image (Civitai)](assets/docs/save-image-civitai.webp)

Civitai identifies a checkpoint or a LoRA by a hash of the file, not by its
name, and reads those hashes out of an A1111-style `parameters` text chunk.
**Save Image (Civitai)** writes that chunk alongside ComfyUI's own, so one PNG
both satisfies the site and still reopens as a workflow.

Connect your images, and a warp carrying the prompt, seed, steps, CFG and
sampler. The resources are worked out from the graph:

- It walks back from itself, so only nodes that actually fed this image count.
  A branch behind a switch that did not run is not credited.
- With several checkpoints upstream, the nearest one wins — a refiner in front
  of a base is the one recorded.
- LoRAs are found in ComfyUI's own loaders, in numbered stacker slots, in
  rgthree-style slot dicts, and as `<lora:...>` tags in any text input. A tag
  behind a `//` is skipped, because it never loaded.
- A LoRA the workflow names but which is not on disk is left out and logged,
  rather than credited to a picture it never touched.

#### The toggles

| | |
| --- | --- |
| **Save as** | `filename_prefix`. Understands `%date:yy-MM-dd hh-mm-ss%` as well as ComfyUI's `%width%`/`%height%`. A slash makes a subfolder. |
| **Generation info** | The `parameters` block: prompt, seed, sampler, size, model and LoRA hashes. |
| **Workflow** | The whole ComfyUI graph, so the image can be dragged back in — which also reveals your pipeline to anyone you send it to. PNG only. |
| **Format** | `png` is lossless and holds the workflow; `jpeg` is much smaller and keeps the generation info in EXIF, which Civitai also reads. |
| **Preview** | Whether the saved images appear on the node. They are saved either way. |

`%date:...%` is expanded here rather than in the frontend, so a prompt sent over
the API behaves the same as one run from the browser.

Choosing JPEG with **Workflow** on saves the file and tells you the workflow was
dropped, rather than silently omitting it.

### What the sidecars give you

Warp Studio reads `.civitai.info` files sitting beside your models — the sidecars
the [Warp Studio panel](#warp-studio-panel) writes and keeps current.

| Field | Used for | Without it |
| --- | --- | --- |
| `trainedWords` | trigger words: `Tab`, the strip, green colouring and whose word it is | no trigger words at all |
| `baseModel` | family grouping, the orange warning, filtering `/` | grouped by folder instead |
| `model.name` | the real model name on cards and in the strip | the filename |
| `modelId`, `id` | the ↗ link to its page on Civitai | no link |
| `files[].hashes.SHA256` | the AutoV2 hash Civitai matches on | computed by hashing the file, then cached |

Only the hash has a fallback. Everything else is simply absent, and the features
built on it go quiet — the LoRA still lists, still previews and still applies.

Preview images are read straight off the disk: any `.preview.png`, `.preview.jpg`
or bare `.png` next to the model file. They are cached down to 320px WebP
thumbnails on first request, because full previews are generations in their own
right — around 1.27 MB each in a real collection, against 6–9 KB cached.

Links point at `civitai.red` rather than `civitai.com`. Since the April 2026
split, `.com` serves an SFW-filtered catalogue, so a link to it for a mature
model arrives nowhere useful; `.red` serves everything, checked against a model
flagged safe.

---

<a id="civitai-panel"></a>

## Warp Studio panel

The sidebar tab **Warp Studio** has a header with the title and a ⚙, and four tabs under it: **Gallery**, **Updates**, **Issues** and **Rename**. The Gallery comes first and is described next; the rest is the updater. The ⚙ opens the settings that belong to the updater: **Model Scope**, **Force rehash**, **Refetch metadata that already exists** and the resolved roots. While any job runs, a job bar under the tabs shows its progress, **Pause** and **Stop**, in every tab. Errors show inline where they happen; routine status sits behind the ⓘ.

Each tab has one toolbar. The Gallery has 🔍, **Filter** and **Sort**; **Updates** and **Issues** have **Filter**, **Sort** and ⓘ; **Rename** has **Filter**. Tabs that cannot search have no 🔍. **Filter** shows a count when something is narrowed, and Types and Bases are one selection shared by the tabs.

### The Gallery tab

Your LoRAs as cards with their previews, grouped by base model. A switch at the top picks **LoRAs**, **Models** (checkpoints and diffusion models) or **Embeddings**. A type with no files is not offered, and the last type is remembered across a reload. Nothing matching shows an empty state, not the whole library.

A card shows the creator, title and version read from the file name when it follows `creator - title - version (base)` (for example `maker - glow - v2 (krea2)`); otherwise Civitai's model name; otherwise the file name. All three types use the same card, with its preview, **↗** and **Delete**.

**Toolbar.** 🔍 opens the search in place; **Esc**, or leaving it empty, closes it. It matches name, creator, version and Civitai's model name, so an old name still finds the card, and the 🔍 stays lit and shows the text while a search is active. **Filter** narrows by base model, and holds **Include downloadable** when it applies. **Sort** has **Group by** (**Base** or **None**, one flat grid) and **Sort** (**Name**, **Newest on disk**, **Most downloaded**, **Most liked** or **Most comments**); the choice is remembered. The three counts are Civitai's, per model (all its versions), saved by **Check for Updates** and read from `GET /warp-studio/updater/model-stats`. While one is the sort, each card shows its number, such as `↓ 11.6k`, `♥ …` or `❝ …`. Sorting stays inside each base band, and models with no numbers (not on Civitai, or not checked since the counts were added) come last, by name. The Gallery has no ⓘ.

**Bands.** Each base model is a band. A click on its header folds or unfolds it; folded bands are remembered per base across a reload, and a remembered base that is not there is ignored. The band whose base matches the model wired into the target node is marked **for this workflow** and comes first. Switching to another workflow tab, or opening a subgraph, re-targets and reorders, as selecting a node does. A click on a card never reorders the bands under the cursor. For Models there is no such band.

**Stacked versions.** One card stands for one Civitai model on one base, and shows its newest version on disk by the publish date in the info file. With more than one, a chip **n versions ▾** lists them by version name and date; picking one makes the card act for that version: its click, its marks and its **Delete**. The marks show when any version of the stack is in the list. Deleting the shown version falls back to the next; deleting the last removes the card. Files with no Civitai link stay single cards.

**Where a click goes.** The target is the selected Prompt + LoRAs node, or else the last one you used, or else the only one. The marks show which node is meant and change when you select another. A line shows only when nothing can be acted on, and the cards are disabled then: **Select a Prompt + LoRAs node** when there are several and none is picked, **No Prompt + LoRAs node in this workflow** when there is none.

**LoRAs.** A click goes through three states, and never deletes a line:

1. A card with no mark: a click adds `<lora:name:1.00>` as a new line at the end of the node's LoRA list, where you can see it, not at the caret.
2. A card marked ✓ (on): a click parks the line as `// <lora:…>`, keeping its weight. The mark becomes hollow.
3. A card marked hollow (parked): a click switches it back on.

A click changes only the line it names. A line matches a card by the tag's name with the weight ignored, case-insensitively; a name with a subfolder is matched whole. With two lines for one LoRA, the first switched-on line counts, and if none is on, the first parked line. `//// SECTION` headers and other `//` lines that hold no tag are ignored. The marks follow the list when you edit it by hand, and the next click acts on the text as it is then. The prose box never gets a LoRA from a click. The node's LoRA lists are `loras`, and `loras_2` for the second pass; the click goes to `loras_2` only when `loras_2_on` is on and it was the last list the caret was in.

**Models.** A click sets the model of the target loader: `ckpt_name` of Load Checkpoint, or `unet_name` of Load Diffusion Model. The target is the selected loader of the right kind, then the last one you used, then the only one in the graph. The card of the model it loads has a ✓. No other loader is supported.

**Embeddings.** A click adds `embedding:name` to the end of the target Prompt + LoRAs node's prompt, once; the card has a ✓ while the prompt holds it.

**Browse LoRAs** on a Prompt + LoRAs node opens the panel on this tab with that node as the target: it selects the node, then sends the `warpstudio:open-gallery` window event with the node and its id.

The tab lists the library the Prompt + LoRAs node already uses, and changes nothing until you click a card, **Delete** or **Download**.

**Include downloadable** is in **Filter**, and is offered only when some LoRAs have a `.civitai.info` in a LoRA folder but no weights, from `GET /warp-studio/loras/missing`. It is off by default and kept for the session, not across a reload. Those cards are dimmed, show their previews, and can't be added to a prompt. If the list can't be loaded, the tab says `Could not load the LoRAs that are not on this drive:` and the reason.

**Download** on one of these cards fetches the version its info file names, and writes it next to the info file under the same name, so no naming is needed. While it waits, **Cancel** is offered; while it runs, the card shows the percentage and **Stop**. An error is shown on the card as the server sent it.

**Delete** is on the cards whose weights are here, for every type. The first click turns it into **Delete?**; if you don't click again within four seconds it turns back. The second click shows **Deleting…** and sends the weights, then every sidecar that shares their name (info, preview and the rest), to the Recycle Bin. The weights go first: if they can't, nothing else is touched. A sidecar that can't go is a warning. On a drive without a Recycle Bin, or for a file too big for it, Windows deletes the file for good. Without the `send2trash` package, Delete is not offered. It is refused while a LoRA is waiting to be replaced by a download, and while a job other than a download runs.

### Updates, Issues and Rename

The Updates tab checks the models on your disk against their Civitai releases, and tells you which ones have moved on without you. It reads what you already have, model files and their `.civitai.info` sidecars, identifies each by SHA256 hash and lists only the models with a newer release. Checking downloads no model and touches no model file; it only writes `.civitai.info` and `.preview.png` sidecars next to your models. A download starts only when you click **Download** or **Replace** on a card, as described under [Reading a result card](#6-reading-a-result-card).

Base models are grouped by family, because Civitai records one per version and the spellings multiply: `Flux.1 D`, `Flux.2 Klein 9B` and `Flux.2 Klein 9B-base` all file under **Flux**, while every row still prints its exact base. A version counts as **new** when its `publishedAt` date (or `createdAt`, when `publishedAt` is missing) is later than the newest local version you have of that model, not by version number.


### 1. Open panel

In ComfyUI, open the sidebar tab `Warp Studio`.

### 2. Configure settings

Go to **Settings → Warp Studio**, the groups that start with "Civitai":

- API key (optional)
- cache duration (how long `Check for Updates` shows saved results instead of checking again)
- timeout/retries/request delay
- treat valid `.civitai.info` sidecars as installed models (enabled by default)
- path source toggles (Comfy defaults, `extra_model_paths.yaml`, custom paths)
- custom paths per model type
- mature content (show, blur previews, hide)

### 3. Quick workflow

1. Click `Check for Updates`.
2. Watch the job bar under the tabs. The list stays still while the check runs.
3. Review the `Results` cards. The `Updates` view lists models with a newer
   release; `Issues` lists the models Civitai could not match or check, with the
   reason under each file.
4. Use `Types`, `Bases` and `Show hidden` in **Filter** to narrow the result list.
5. Use `Pause/Resume` or `Stop` for long jobs. `Stop` waits at most for the
   request in flight; a file being hashed is abandoned.
6. Limit the **Model Scope** in the panel's ⚙ settings to re-check only some types; results for the other types are kept from the previous check.

Within the cache duration, `Check for Updates` shows the saved results instead
of checking again, unless Model Scope differs from the last check, files were
added or removed, `Force rehash` is ticked, or the last check was stopped.
With fresh saved results, the click asks first: "These results are from … ago. Check every file against Civitai again?" with **Check again** and **Keep these** (Escape or a click beside the box keeps them). **Check again** in the ⓘ, which also holds when it was last checked, the counts and "Changes detected" when files changed, always runs a check without asking.

While a check runs, the button is its progress: it reads "Checking 491 of 746" and fills from the left as files are checked, and the **⋯** square beside it is **Stop**. When the check ends, the button shows its result ("Done — 25 updates found") for a few seconds. The pill at the bottom stays in for a check on Updates and Issues; it shows the check's progress on the Gallery and Rename tabs, where the button is not on screen, and an error on any tab.

### 4. Metadata workflow (Advanced)

Run `Fetch Missing Metadata`.

- Downloads `.civitai.info` sidecars and preview images only.
- Models that already have a sidecar are skipped, unless `Refetch metadata that
  already exists` is ticked — that re-pulls them by sidecar version ID, with no
  re-hashing.
- Never queries the version list, so it never reports updates and never fills
  update cards.
- Report is compact (`total/refreshed/skipped/notFound/errors`).
- Use it to populate names, base models, and thumbnails after adding a batch of
  models, then run `Check for Updates` to see what is outdated.

### 5. Pagination and streaming

- Default page size is `25` (options: `25`, `50`, `100`).
- Metadata is polled every `800ms`.
- Page 1 auto-refreshes while a check job is running (streaming feel).
- While a check job is running, a card built from the previous check's results is dimmed until its files have been re-checked.
- If you navigate to another page, your page is preserved.

### 6. Reading a result card

Each card is a ledger of one model's releases. The dot on the left of every row
says where that version stands:

| Dot | Row | Meaning |
| --- | --- | --- |
| hollow | `Saved` | A version present on disk. Click the name to copy its file path. |
| hollow, dim | `Metadata` | Tracked from a `.civitai.info` sidecar with no weight file next to it. |
| red | `New` | Released after your newest local version. Click the name to open it on Civitai. |
| grey | `Hidden` | A new release you hid. Visible only with `Show hidden` enabled. |
| amber | `Error` | The lookup failed or Civitai refused it (a removed model, one that needs an API key). The reason is printed under the row. |
| dashed | `No match` | Civitai has no release with this file's hash. |

Red is used for one meaning only: a release exists that you do not have.
`New` rows are sorted by release date, newest first.

A `Buzz` badge on a `New` or `Hidden` row means downloading it costs Buzz:

- `Buzz · 5d left` (dashed) is early access. It is free after the date in its
  tooltip, and the badge disappears once that date passes.
- `Buzz · always` (solid) has no free date.

Civitai's public API does not give the price.

A paid or early-access release shows `Early access` in place of the buttons below.
The server refuses such a download too, with `This release is in early access.`

#### Download and Replace

A `New` or `Hidden` row has **Download**. A card whose current version has its weights on
this drive also gives the row **Replace**. Both start a download of that release
on the server.

- Downloads run one at a time, in a queue. A click while one runs shows `Waiting`
  with **Cancel**. A click while another job runs (a check, a dry run, a rename,
  a metadata fetch, the naming model download) shows `Waiting for the check`
  (or the dry run, and so on) in the same slot, and starts by itself when that job
  ends; the download takes the job slot before any job started later.
  Cancel removes it. It is checked again when it starts: if a rename that was cut off still needs
  recovering, or a Replace's old file is no longer where it was, the card shows an
  error saying so.
  Only a rename that was cut off and still needs recovering refuses a download. The running one shows its percentage (`Downloading` while the
  size is unknown) with **Stop**; Stop deletes its partial file. A finished one
  shows `Downloaded`. A stopped one shows no note, and **Download** comes back. The job bar follows the run, and ends with
  `Done — 2 downloaded`, plus `, 1 failed` and `, 1 stopped` when they are not
  zero. A reloaded panel finds the running job and paints every row again.
- The file is written as `<name>.part`, checked against the SHA-256 Civitai lists
  for it, and only then renamed into place. A wrong hash or an early stop deletes
  the `.part` and the row says so. An existing file is never overwritten.
- The new file is named as a rename dry run would name it, with the AI naming
  model, and goes into its base model's folder when that base has one; otherwise it
  stays beside the old version. With no naming model, a name that fails the checks,
  or a name already taken, it keeps Civitai's file name, and the next dry run offers
  a proper one. The download is refused only when Civitai's name is taken too. Its `.civitai.info` and preview
  are written the way a scan writes them.
- **Replace** adds one step: once the new file is checked and in place, the old
  version's weights go to the Recycle Bin (on a drive without one, or for a file too big
  for it, Windows deletes the file for good), then its sidecars. If the weights can't
  go, nothing else of the old version is touched, and the row says so. If the new
  version's info file can't be written, the old version is kept and the row says so. The new
  version is never the one sent away. Tags in prompts that name the old file go red,
  as after a rename. Replace needs the `send2trash` package, and a file can have one
  Replace queued at a time.
- A download or a delete is refused with `Another job is running. Try again when it's done.`
  while any other job runs, and the other way round, Check, Fetch Missing Metadata,
  dry runs and applies are refused while a download runs.
- Most adult models need a Civitai API key. Without one the row shows `Civitai needs an API key for this download: Settings → Warp Studio → Civitai Network → API Key`.
  The key goes to Civitai hosts over https only. A redirect to the storage host is
  followed, without the key.
- Waiting and running downloads are kept in the server's memory. After a restart of ComfyUI the queue is gone. A `.part` left behind is not
  listed as a model, and the next try replaces it.

`Version` means a specific Civitai release.
`New` uses Civitai release dates: `publishedAt` first, then `createdAt` when `publishedAt` is missing.

A sidecar-only version suppresses that same Civitai version from `New`. It is
shown only when a later version produces an update card. The sidecar must contain
valid `modelId` and version `id` fields; preview PNGs alone and invalid sidecars
are ignored.

### 7. Grouping and sorting

Open **Sort** in the toolbar to set two levels of grouping and the sort
applied inside them.

- `Group` / `Then`: `Nothing`, `Model type`, or `Base model`.
- Default is `Model type` then `Base model`, sorted `Name A–Z`.
- `Furthest behind` sorts by the gap between your newest local version and the
  newest release.
- Base models are grouped by family (`Flux.1 D` and `Flux.2 Klein 9B` both file
  under `Flux`); each row still shows its exact base.
- A card is filed under the base model of the release it is headlining, because
  one Civitai model page can publish on several base models.
- Group headers count the whole result set, not the current page. Click one to
  collapse it; a group continuing past a page break is marked `cont.`

### 8. Mature content

`Settings → Warp Studio -> Mature content`:

- `Show everything` (default).
- `Blur previews` blurs the preview image only, so titles stay readable.
- `Hide mature models` removes them from results and reports how many were
  hidden.

### 9. Hidden updates

- Use `Hide` on a remote version row if you do not want to download that version.
- Hidden versions stay hidden across future checks.
- Enable `Show hidden` to review them later; **Unhide** brings one back.

### 10. Check with archive

Models you moved to another drive can still be followed. Set **Settings → Warp
Studio → Civitai Path Sources → Archive Folder** to the folder that holds them,
laid out like the models folder (a top folder named like a model folder, such as
`loras`, belongs to that type). With no folder set, nothing below shows.

The toolbar of **Updates** and **Issues** has **Check for Updates** and a **⋯**
menu. The menu always holds **Fetch Missing Metadata**; with an archive folder
set it also holds **Check with archive**. That check runs the normal one, then
looks up each archived model on Civitai, at the same pace (one lookup per model,
`Delay between models` apart): about 7 minutes more for 1,100 models. Pause and
Stop work as for any job. A plain **Check for Updates** does not look at the
archive.

- It only reads. The `.civitai.info` files in the archive give each model and
  version id; nothing there is hashed, written, renamed or deleted. A model file
  with no info file is skipped and counted.
- A version you have in the archive is never offered as new: with v2 archived
  and v1 on this drive, v2 is not an update. A model on this drive is checked as
  usual, never as archived.
- A new version of an archived model shows in **Updates** marked **Archived**,
  with **Download** and **Hide**, and no **Replace**. **Download** puts the file on
  this drive, in the folder for that model type and the same subfolder as in the
  archive when there is one, else the type's folder.
- The same Civitai version on both drives is listed in **Issues** as
  **Duplicate**, with the archive file's name. An info file on this drive whose
  model is in the archive is listed as **Info only**. Removing them is up to you.
- Archived models Civitai removed or doesn't know never appear in **Issues**.
- The results are kept apart, in `archive_check.json` in the data folder, so a
  plain check keeps the archived rows. A row goes when its version is downloaded
  to this drive or hidden. The ⓘ says when the archive was last checked and how
  many archived models have a new version in **Updates**.
- The archive folder can't be a model folder, lie inside one or hold one: such
  a folder is not saved.
- If the archive folder can't be reached (an unplugged drive), holds no folder
  named like a model folder, or now overlaps a model folder, the check says so
  and the last results stay.

### Previews from other sources

Some models Civitai does not serve: removed models, merges, your own trainings.
**Fetch Missing Metadata** can ask other sources for a file that still has no
`.preview.png`. They are asked in this order, and the first answer wins:

1. **ai-toolkit.** Your own trainings. Set the output folders that hold your runs under **Civitai Preview Fallback → ai-toolkit Folders**. The sample images of a run become the previews of its files. **Own Creator** is the creator name those files are filed under.
2. **Hugging Face.** Authors or repos you name (`author` or `owner/name`, separated by `;` or new lines), matched by file hash. Leave it empty to skip Hugging Face.
3. **civarchive.** Looked up by the file's SHA256 hash; only the hash is sent. It is off until you turn on **Look up previews on civarchive**.

A hit writes the preview and fills only the empty fields of the `.civitai.info`.
It never replaces a preview you already have, and an info file that identifies a
Civitai model keeps every field it has. Images are downscaled before they are
saved, and a source that fails three times in a row is skipped for the rest of
the scan.

A hash that no source knew, or whose answer had no usable picture, is not asked
about again for 30 days. A failed lookup is never remembered. The result row says
the file was **Found**, and the line under it names the source. The renamer can
name these files too, from the info the fallback found; those rows start
unticked.

### Renaming your library

The **Rename** view, next to **Updates** and **Issues**, renames your model
files after their current Civitai releases, in one scheme:

```
creator - title - version [precision] (base)
```

All lowercase, for example `some_artist - watercolor style - v2 (sdxl)` for a
LoRA or `some_studio - photo mix - v3 fp16 (sdxl)` for a checkpoint.

- **creator** is the Civitai uploader. For a re-upload, a conversion or an
  official model it is the model's maker, when the description credits one.
- **title** and **version** come from Civitai's live data, cleaned up: no base
  model names, no "lora", no taglines. A name the creator coined stays as
  written.
- **precision** (`int8`, `fp8`, `bf16`, `q8_0`, …) is given only to
  checkpoints and UNets; LoRAs never get one. It is read from the model file
  itself. A metadata-only entry (a `.civitai.info` with no weights) takes it
  from Civitai's file info instead, and only when the release has a single
  main model file.
- **base** is a short code per base model, such as `sdxl` or `flux1d`. The
  codes are a table, seeded once from the `(tag)` at the end of your existing
  file names, and you can edit it in the Rename view (**Base codes**). A base
  the table does not know gets a suggested code, saved when you apply.

Checkpoints, LoRAs, UNets and embeddings are renamed where they are. Every
file next to the model that shares its name goes along: the `.civitai.info`,
the `.preview.png`, and anything another tool left there.

#### The naming model

The cleaning is done by a local text model, run by ComfyUI's built-in
**Generate Text** node. Nothing goes to the cloud and no other app is needed.
Pick the model under **Settings → Warp Studio → Civitai Rename → Naming model**:
any Qwen3, Qwen3.5, Qwen3-VL or Gemma text encoder file in your
`text_encoders` folders that ComfyUI can load. Until a usable naming model is
set, the Rename view offers **Download naming model (9.75 GB)**, which fetches
Comfy-Org's `qwen3.5_9b_int8_convrot.safetensors` into your first
`text_encoders` folder. If that file is already in one of those folders, it is
chosen instead of downloaded again.

The renamer's work goes through ComfyUI's queue like any other prompt, so it
takes turns with your own jobs and shows up in the queue history. A ComfyUI
too old to have Generate Text cannot run the renamer; the view says so.

#### Dry run, review, apply

1. **Start dry run.** It asks Civitai about every model and the naming model
   about every title, and lists each old name next to its new name. Nothing on
   disk changes yet. The answers are saved, so a second dry run only asks
   about models that changed. Each model is one card, as in **Updates**: its
   preview, Civitai title and uploader, and one line per file. Click a file's
   name to show it in its folder.
2. **Review.** Rows that need a look come first, unticked: a word the naming
   model could not find in Civitai's text, two files that would get the same
   name, two versions or two models by one creator that would get the same name
   or title (the naming model is asked once more first), a version whose own
   name names another base, a new base code. A maker taken from the
   description, a creator taken from your file name, or a file that is not in
   the Civitai release is only a hint: the row stays ticked. Some fixes have one
   right answer and are made without asking: "v1.0" becomes "v1", "high noise"
   becomes "high", and a version that only names the base is left out. The new
   name is under the current one, with the changed words marked. You can **Edit**
   any part, or **Ask again** to let the naming model name the model afresh on
   the next dry run. The checkbox decides whether a file is renamed: a file you
   untick keeps its name, also in later dry runs, until you tick it again.
3. **Apply.** The button says what it will do, as in **Rename 12 · remove 2**,
   and asks once; **Apply** then renames the ticked rows and moves the extra
   copies to the Recycle Bin.

The same Civitai file saved twice in one folder is listed once as an extra
copy. The copy kept is one with weights, if any, and then the one whose name
matches its Civitai info; a copy saved under the wrong name long ago is the
extra one. It is the line under the copy kept, and starts ticked (its name
struck through) when the copies are the same Civitai file or have the same
stored hash; untick it to keep both. An extra copy goes to
the Recycle Bin with its `.civitai.info` and `.preview.png`, and its weights
only when they are byte-identical to the kept copy's. Any other file of its name
(an archive, a backup) stays where it is.

The naming model gets roughly three in four names exactly right on models it
has not seen before. Read the list before you apply it.

Your edits are remembered per model and win over the naming model on every
later dry run. Applied names are locked: a creator retitling the model on
Civitai does not bring up a new name for files you already renamed, until you
click **Ask again**.

#### What is not renamed

The dry run lists these as kept, with the reason:

- Models or releases Civitai no longer serves. Their sidecars do not hold
  enough to rebuild a name from.
- Files whose Civitai match is not the release's main model file, or whose
  Civitai type does not fit the folder (a text encoder in `unet`, say).
- Files kept with **Keep current name** in an earlier version; ticking one renames it after all.

Two kinds are left alone without being listed:

- Files without Civitai metadata: no `.civitai.info` with a model and version
  id, as with merges, conversions and your own trainings. Run **Fetch Missing
  Metadata** first, so every file Civitai knows has one. A file that only
  the [preview fallback](#previews-from-other-sources) identified is named from the info it found.
- The VAE folder. One VAE serves several base models, and Civitai matches it by
  hash to whoever attached it to their release.

#### Before you apply

There is no backwards compatibility. Saved workflows, and images with their
workflow, that use the old names show those LoRAs and models as missing.
Re-pick them.

If ComfyUI stops in the middle of a rename, a journal remembers where it got
to, and the Rename view offers **Finish** or **Undo** on the next start. A file
that already has the new name is never overwritten, and a file Windows has
locked (a model loaded right now) is skipped and reported.

Every file the renamer moves is recorded in `ComfyUI/user/warp-studio/updater/rename_log.jsonl`: one JSON line per file with the time, the kind
(`rename`, `trash` for a removed copy, `undo` for a rename that **Undo** put
back), the old and the new path (for a removed copy, the kept copy's file),
and the run. The log is never cleared, so you can rename copies of the same files elsewhere (an
archive disk, say) to match.

### Settings

Under **Settings → Warp Studio** (the groups that start with "Civitai"):

- **API key** — optional, but some restricted resources need one.
- **Cache duration** — how long **Check for Updates** asks before checking the
  saved results again. Changing **Model Scope**, adding or removing model files,
  or **Check again** in the ⓘ runs a real check inside that window without asking.
- **Timeout, retries, delay between models** — for slow or rate-limited runs.
- **Treat `.civitai.info` as installed** — count a valid orphan sidecar as an
  installed version, so a model you deleted but still track keeps reporting
  updates. On by default.
- **Mature content** — **Show** everything, **Blur previews** (the image only;
  titles stay readable so you can still tell which model it is), or **Hide
  mature models**, which removes them from results and reports how many were
  left out rather than quietly shrinking the list.
- **Path sources** — ComfyUI's own folders, `extra_model_paths.yaml`, and
  custom roots per model type (checkpoint, LoRA, VAE, UNet, embedding).
- **Archive Folder** — one absolute folder of models you moved elsewhere, for
  **Check with archive** (see [Check with archive](#10-check-with-archive)). It
  must exist and not be a whole drive. Empty by default. Only read.
- **Naming model** — the text encoder the renamer runs. See
  [The naming model](#the-naming-model).

### Why civitai.red

Since Civitai split into two domains in April 2026, `civitai.com` serves
SFW-filtered API responses. Hash lookups for mature models come back empty
there, which showed up as models that could never be identified. This
extension talks to `civitai.red`, which serves the full catalogue, and links
to it as well. Set your API key if a resource still refuses to resolve.

---

## Node reference

Ten nodes (nine backend nodes plus the frontend-only Warp Select), all under **Custom/WarpPipe Nodes**. Node IDs are what saved
workflows and the API use; display names are what you see on the canvas. Warp
Select is the exception to the rest of this section: it exists only in the
browser, so the backend never sees it.

| ID | Display name |
| --- | --- |
| `Warp` | 🌀 Warp |
| `Unwarp` | 🌀 Unwarp |
| `Warp Provider` | 🌀 Warp Provider |
| `Warp Lora Prompt` | 🌀 Prompt + LoRAs |
| `Save Image Civitai` | 🌀 Save Image (Civitai) |
| `FD Scheduler Adapter` | 🌀 Scheduler Adapter for FaceDetailer |
| `Warp Send` | 🌀 Warp Send |
| `Warp Compare` | 🌀 Warp Compare |
| `Warp Select` | 🌀 Warp Select (frontend only) |
| `Dead End` | 🚫 Dead End |

---

### 🌀 Warp

Bundles data into a single `WARPPIPE` object.

**Inputs** — all optional.

| Input | Type | Notes |
| --- | --- | --- |
| `warp` | `WARPPIPE` | Start from another warp's contents, then add to them |
| `model_1`, `model_2` | `MODEL` | |
| `clip` | `CLIP` | |
| `clip_vision` | `CLIP_VISION` | |
| `vae` | `VAE` | |
| `conditioning_positive`, `conditioning_negative` | `CONDITIONING` | |
| `image` | `IMAGE` | |
| `mask` | `MASK` | |
| `latent` | `LATENT` | |
| `prompt_positive`, `prompt_negative` | `STRING` | link only |
| `batch_size`, `seed`, `steps_1`, `steps_2`, `steps_3`, `width`, `height` | `INT` | link only |
| `cfg` | `FLOAT` | link only |
| `sampler_name` | sampler enum | link only |
| `scheduler` | scheduler enum | link only |

**Output:** `warp` — `WARPPIPE`.

Three step counts exist because multi-pass workflows want them: a base pass, a
refiner, a detailer. Nothing forces you to use more than `steps_1`.

Sampler and scheduler are coerced on the way in. A name ComfyUI does not know
becomes `euler` or `karras` rather than travelling as something a sampler will
later reject. Linked enums from other node packs are accepted — see
[Architecture → Validation](#validation).

---

### 🌀 Unwarp

Unpacks a warp. Its one input, `warp`, is optional; with nothing connected every
output is `None`.

**Outputs**, in order:

`model_1`, `model_2`, `image`, `mask`, `clip`, `clip_vision`, `vae`,
`conditioning_positive`, `conditioning_negative`, `latent`, `prompt_positive`,
`prompt_negative`, `batch_size`, `seed`, `steps_1`, `steps_2`, `steps_3`, `cfg`,
`sampler_name`, `scheduler`, `width`, `height`.

A field the warp never carried comes back as `None`, not as a default. Inventing
`euler` for a sampler nobody set would produce an image with the wrong settings
and no indication that anything was missing.

---

### 🌀 Warp Provider

Sampling parameters and a matching empty latent, in one node.

| Input | Type | Default | Range |
| --- | --- | --- | --- |
| `batch_size` | `INT` | 1 | 1–64 |
| `seed` | `INT` | 0 | 0–2⁶⁴−1 |
| `steps_1` | `INT` | 20 | 1–200 |
| `steps_2`, `steps_3` | `INT` | 0 | 0–200 |
| `cfg` | `FLOAT` | 7.0 | 0.0–50.0 |
| `sampler_name` | sampler enum | `euler` | |
| `scheduler` | scheduler enum | `normal` | |
| `size_preset` (shown as **size**) | 32 presets and `Custom` | `1:1 · 1024×1024 · 1.0 MP` | |
| `custom_width`, `custom_height` | `INT` | 1024 | 64–8192, step 8 |

**Outputs:** `latent`, `batch_size`, `seed`, `steps_1`, `steps_2`, `steps_3`,
`cfg`, `sampler_name`, `scheduler`, `width`, `height`.

Presets are labelled `ratio · width×height · megapixels` and sorted by aspect
ratio (9:16 up to 16:9), then by size. Every side is a multiple of 16, the long
side at most 2048. Choose `Custom` to use the two custom boxes; keep those to
multiples of 16 too unless the model is SDXL, or a sixteenth-size model rounds
them.

Workflows saved by 4.1 and earlier hold labels like
`Portrait (high-res)         |  3:4   |  1152 ×  1536  |  1.77 MP`. They still
load, run and validate, at the same size — except four that were not multiples
of 16 and now give the nearest size that is: 1224→1232 and 1640→1632 wide
(4:5), 1368→1360 (2:3), and 1080→1088 (9:16 and 16:9). Opened in the editor,
an old Provider shows the new label straight away; queued as it was saved, over
the API, the old label is accepted as it stands.

The latent is four channels at an eighth scale with `downscale_ratio_spacial`
set to 8, matching ComfyUI's own Empty Latent Image. KSampler and
SamplerCustomAdvanced use that to resize an empty latent for a model on a
sixteenth-size grid, so one Provider serves every architecture.

Dimensions are validated before the tensor is made: a linked width that is not
an integer, is out of range, or is not divisible by 8 raises rather than
producing a latent nothing can sample.

---

### 🌀 Prompt + LoRAs

The prompt and its LoRAs on one node. Full walkthrough in
[Usage → Writing a prompt](#writing-a-prompt).

| Input | Type | Notes |
| --- | --- | --- |
| `text` | `STRING` | multiline. The prompt. Tags written in here still load |
| `loras` | `STRING` | multiline. The LoRAs applied to `model`, one `<lora:name:weight>` a line |
| `loras_2_on` | `BOOLEAN` | default off. Give `model_2` a list of its own, and show it |
| `loras_2` | `STRING` | multiline. The LoRAs applied to `model_2` |
| `apply_to_clip` | `BOOLEAN` | default on. Patch the text encoder as well as the model |
| `model` | `MODEL` | optional |
| `model_2` | `MODEL` | optional. The second pass of a two-model sampler |
| `clip` | `CLIP` | optional |

**Outputs:** `model`, `clip`, `prompt`, `model_2`.

`prompt` is what you wrote with tags and notes removed and nothing added. Blank
lines you wrote are kept — at most one in a row — but a line that held only a
tag or only a note is removed rather than left behind as a blank.

**The second list** is for a sampler that runs two models over one picture —
base then turbo, high noise then low. Each pass gets its own files at its own
weights, so the lists are read independently and an empty one applies nothing
rather than "the same again". With `loras_2_on` off the second list is not read
at all and `model_2` passes through untouched, so a box that is not on screen
can never be loading anything. `model_2` is the last output because outputs are
restored by position: in front of the other three it would land every link in a
workflow saved before it on the wrong socket.

The text encoder is patched by the first list only: one CLIP is encoded once
and shared by both passes, so patching it with two stacks would describe
neither.

**`apply_to_clip`** decides whether the text encoder is patched too. Whether it
matters depends entirely on the LoRA: in a survey of 761 files, every Krea2,
Flux2, Wan, Qwen, ZIT and LTX LoRA was model-only, so patching CLIP changed
nothing; among SDXL LoRAs, 76% carried text-encoder weights, where turning it off
discards half of what the LoRA does. Leave it on unless you have a reason.

Encode the prompt **after** the LoRAs are applied — take this node's `clip`
output into your text encoder, not the CLIP straight from the loader.

`loras` is the same input older workflows kept their list in, so one saved
before the box was shown finds its LoRAs already where they belong.

---

### 🌀 Save Image (Civitai)

Saves images with metadata Civitai reads. Details in
[Usage → Saving for Civitai](#saving-for-civitai).

| Input | Type | Default | Shown as |
| --- | --- | --- | --- |
| `filename_prefix` | `STRING` | `WarpPipe` | Save as |
| `embed_metadata` | `BOOLEAN` | on | Generation info |
| `embed_workflow` | `BOOLEAN` | on | Workflow |
| `file_format` | `png` / `jpeg` | `png` | Format |
| `preview` | `BOOLEAN` | on | Preview |
| `images` | `IMAGE` | — | optional |
| `warp` | `WARPPIPE` | — | optional |

Hidden inputs: `prompt`, `extra_pnginfo`, `unique_id`. No outputs — this is an
output node.

`images` is optional on purpose: bypassing an upstream branch leaves this node
idle instead of failing the whole prompt. When nothing arrives it saves nothing
and says so, on the node and in the log, rather than reporting a silent success.

---

### 🌀 Scheduler Adapter for FaceDetailer

**Input:** `scheduler` — a KSampler scheduler (required).
**Output:** `scheduler` — one FaceDetailer accepts.

FaceDetailer builds its list as ComfyUI's schedulers plus Impact Pack's own.
Exotic entries — `AYS SDXL`, `GITS[coeff=1.2]`, `OSS FLUX` and friends — pass
through when the target list has them and are mapped to `karras` when it does
not.

---

### 🌀 Warp Send

Marks the end of one model group and hands its warp on. One Send in each group,
one Warp Select in the hub, and the active Send is the group that runs.

| Input | Type | Notes |
| --- | --- | --- |
| `warp` | `WARPPIPE` | The group's warp |
| `index` | `INT` | 1–99, default 1. The number Generate's model switch uses. Unique per Send |
| `name` | `STRING` | default `Model`. Shown in Warp Select's list. Unique per Send |

**Outputs:** `warp`, `index` — both unchanged.

The backend only passes the two through. Everything else happens in the
browser: the big **activate** button (it reads **active** on the active Send)
makes this Send the active one (stored in its `properties.active`, so it is saved
with the workflow); any other Send stops being active. The active Send is fully
green, title and body. Inactive Sends are fully red and show a line
`active: <name>`. Sends and Selects are always coloured by their state, so a
colour you set by hand is overwritten. The rest of the node is a compare view: the live sampling preview while
the active group runs, then a before/after slider that follows the pointer, with
◀ i/n ▶ to step through pairs of a batch, and **no result yet** before the first
run.

The results arrive without links. **final** is the images Save Image (Civitai)
shows in that run; with several Save Image (Civitai) nodes the last one to run
is the final, and a Save Image (Civitai) with its preview turned off gives no
final. **before** is the images of the one node marked through its right-click
entry **Use as before image** (stored in `properties.warpBefore`;
only one node per workflow carries it, it shows a `before` badge, and **Remove
before-image mark** clears it). The mark goes on an output node such as Preview
Image; the entry is not offered on other nodes, nor on Warp Send or Warp Select.
Both go to the Send that was active when the run was queued, by its name, even
if you switch to another workflow tab while it runs. They live in the page only: they are
not saved with the workflow, mark nothing modified and leave no undo step, stay
until the page is reloaded, and the temp files they point at are gone after a
ComfyUI restart anyway. The live preview needs ComfyUI started with
`--preview-method auto`. The view is a DOM widget, so it works in the classic
canvas renderer and in Nodes 2.0 (Vue nodes).

A pasted copy of the active Send is not active. Converting a group to a subgraph
or unpacking one keeps the Send active, since the original goes away. If a
workflow somehow holds two active Sends, the one with the lower index keeps it.

---

### 🌀 Warp Select

Picks which Warp Send runs. **Frontend only** — there is no backend node, and it
never appears in the prompt ComfyUI receives.

**Widgets:** `active`, a dropdown of every Send's name in index order, including
Sends inside subgraphs — picking one activates it; **go to active group**, which
only moves the canvas to the active Send, opening its subgraph if need be. The
node is green while the route is valid and red otherwise.

**Outputs:** `warp` (`WARPPIPE`), `index` (`INT`) — wire them to Generate.

It is a virtual node: when the prompt is built, ComfyUI asks it where its outputs
really come from, and it names the active Send's outputs, wherever that Send
lives. So Generate reads straight from that one Send, and the inactive groups are
not referenced and never run.

A workflow with a Warp Select refuses to queue, with a message, when it has no
Warp Send, two Sends share a name or an index, no Send is active, the active one
is muted or bypassed — itself or a subgraph it sits in — or the active one has no
`warp` input. A workflow without a Warp Select is never checked.

---

### 🌀 Prompt Writer

Not a node: a chat-style pop-up opened from the **✨ AI Prompt Writer** button just
above the `text` box of every 🌀 Prompt + LoRAs, in the empty middle of the last row
of slots (**✨ AI** on a narrow node). An LLM writes and rewrites an image prompt, and **Send to ComfyUI** puts
a version into a Prompt + LoRAs. It lives in the
browser (`web/prompt_writer.js`), never appears in the prompt ComfyUI receives,
and writing a prompt never queues an image. Every call goes through the
`/warp-studio/llm` routes, so the browser never sees a key.

**Layout:** the rail holds **Prompt Writer** with the settings cog, **New chat** and the
chats; the open chat has its title (click to rename), **For** (the Prompt + LoRAs this
chat writes for, kept per chat) and close at the top, then the versions in a reading
column of at most 780 px and the input. The cog opens the settings: the model list with
a text filter (name, price per million tokens, an eye for vision), the system prompt
with a read-only preview, temperature, title model, server, the API key field with
**Save** (cleared after sending; it only reports whether a key is set) and **Refresh
lists** (the backend caches a model list for an hour).

**Settings** (`WarpStudio.PromptWriter.*`):

| Setting | Notes |
| --- | --- |
| `BaseUrl` | default `https://nano-gpt.com/api/v1`. Any OpenAI-compatible server; plain `http://` only for `localhost`, `127.0.0.1` and `::1` |
| `Model` | the model id; a model no longer listed stays selected and reads `(not listed)` |
| `SystemPrompt` | one of the `.txt` files directly in `SystemPromptDir` |
| `Temperature` | 0–2, default 0.6 |
| `ReasoningEffort` | `none` … `max`, sent as `reasoning_effort`; empty (the default) sends nothing |
| `SystemPromptDir` | Settings → Warp Studio → Prompt Writer |

**Chats and versions:** the left rail lists the chats, newest first. Each version is
one line, `v2` and its title (named by the title model while it is written: what it
shows for a new prompt, what it changes for an edit, `"kind": "edit"` in
`POST /warp-studio/llm/title`, which answers with the `kind` it named; the request until then;
versions from before titles are named once when the pop-up opens, only by a server that
answers with that `kind`). One version is open at a time,
and the line you click stays where it is on the screen. An open version has
**Request** (open by default), **Changes** (`+38 −12`) and **LoRAs** above its prompt and **Send to ComfyUI (Group)**
under it, which writes the text unchanged into the Prompt + LoRAs chosen under **For**,
as one undo step, flashes the node and closes the pop-up. The newest version, when
open, is editable (saved on blur). A version's **⋮** has **Generation info** (made
from, model, temperature, thinking level, LoRAs offered, server, cost, time, tokens,
the request and questions, the prompt it edited, the system prompt's text as the
server read it, kept once per chat in `systemPrompts`, and the thinking), **Use as
input** (also a click on its dot in the graph; the chosen dot has a ring),
**Retry** (writes it again at once and replaces it in place, not once others were made
from it), **Recreate** (opens its request in a field where it was shown, to change or
not, and writes a new version beside it from the same base and images; unchanged, the
answers and title go along; both use the settings chosen now) and **Delete** (with
those made from it, after asking). Version numbers are saved, so a deleted one leaves a gap.

**Questions:** the LLM is told to start a reply that asks instead of writing with
`===QUESTION===`. That reply is a **Question** card, not a version; the send button then
answers, and the answer goes with the request, what it edits and the earlier
questions and answers (`turns` in `POST /warp-studio/llm/write`).

**LoRAs:** **LoRAs** in the composer (setting `LoraScope`) is **Off**, **List**
(the LoRAs switched off in the target's own lists) or **All** (every LoRA that fits the
target's base model). The ones already active are sent with either. The LLM
uses the trigger words of active LoRAs and may suggest others (at most two while LoRAs are active, otherwise five), shown as
chips under the version. Nothing is added by itself: **+** on a chip switches on a
commented line that holds the tag, or appends the tag to the `loras` box, and puts
its trigger words on the first line of that version; pressing it again undoes both.

**Writing:** the input is one box, lined up with the versions, with its tools as a bar on top: an image button
(up to 8 images, also by drag and drop or Ctrl+V, uploaded to `input/`, left out with a
notice when the model has no vision), what the next request does, sent as
`previous_result` (**Edit v3 (newest)** or the version chosen by its dot, **Edit current prompt**,
the text of the **For** node read when you send, or **New prompt**) and the LoRA choice;
a round send button in the corner of the text stays the same size and turns into a yellow stop button while
it runs. Ctrl+Enter sends.
Writes are held per chat outside the pop-up, so several run at once and survive
closing it. The **✨ AI Prompt Writer** button on the node is yellow while a chat opened from
that node writes, and has a green dot when one finished unseen. While it writes,
the card shows waiting, thinking or writing, the seconds and an estimate of the tokens
so far, the model's thinking as it comes (for models that send it) and the prompt as it
is written; a finished version keeps the thinking, folded. A failed request shows a red
card with **Retry**; it is kept with the chat until it is retried, taken back or the chat
is deleted, and **Retry** uses the current settings.

**Storage:** chats are saved in the ComfyUI user folder as
`warp_studio_prompt_chats.json`, never in a workflow or in images. Each request is also
logged to `ComfyUI/user/warp-studio/llm_log.jsonl`: a `request` line when it is sent
and a `result` line with the same `id` when it ends (text, question, error or
stopped, with usage, cost and seconds). No key and no image data is written; the
file moves to `.1` at about 20 MB, and a log that cannot be written never stops a
request.

**Keys** are bound to hosts and used only when `base_url`'s host is the key's
host, so a workflow pointing at another server never receives your key.
The node's key field saves the typed key for `base_url`'s host (both NanoGPT
hosts at once) into `ComfyUI/user/warp-studio/llm_keys.json`; the browser can only
ask whether a key is set. `NANOGPT_API_KEY` covers `nano-gpt.com` and `api.nano-gpt.com`;
`ComfyUI/user/warp-studio/llm_keys.json` holds `{"host": "key"}` for others. Local
servers get no key unless the file names them. Warp Studio never logs or returns a
key and writes it only on **save key**.

---

### 🌀 Warp Compare

Compares two images with a slider. An output node, and the replacement for
rgthree's Image Comparer in Warp Studio workflows.

| Input | Type | Notes |
| --- | --- | --- |
| `before` | `IMAGE` | optional. The earlier image, shown on the left, labelled "before" |
| `after` | `IMAGE` | optional. The later image, shown on the right, labelled "after" |

**No outputs.** Both batches are saved as temp previews. The split follows the
mouse; ◀ i/n ▶ steps through pairs (before 1/after 1, before 2/after 2). A pair with only one side shows that image whole.
Warp Send uses the same compare view, with its sides labelled "first" (left) and "final" (right). It is a DOM widget, so it renders in the
classic canvas and in Nodes 2.0.

---

### 🚫 Dead End

**Input:** `input` — any type, optional. **No outputs.**

It is not an output node, so ComfyUI never executes it, which is the point: it
terminates a branch without running it. Useful for parking a path while you work
on another, or for tidying an unused output.

---

### Registration

Both registration paths are built from one table in `warp_pipe.py`, so every
node exists whichever path runs. Warp Select is not in it: it is registered by
`web/warp_send.js` in the browser.

- **Legacy** (default) — `NODE_CLASS_MAPPINGS` and `NODE_DISPLAY_NAME_MAPPINGS`.
- **V3** — set `WARPPIPE_ENABLE_V3=1` to register through
  `comfy_entrypoint()` and ComfyUI's V3 schema instead.

The two are mutually exclusive; the package exports one or the other, never
both. If a node were ever added without a V3 schema, startup logs an error
naming it rather than quietly dropping it.

---

## HTTP API

Every route starts with `/warp-studio/`, and is registered on ComfyUI's own
`PromptServer` at import. They exist for Warp Studio's frontend. The first group
serves the nodes and the Prompt Writer. The Warp Studio panel's routes, under
`/warp-studio/updater/`, are [further down](#warp-studio-panel-routes).

Every route but the thumbnail asks for the header `X-Civitai-Updater: 1`, and a
POST must be sent as `application/json`. A request from another site (by `Origin`
or `Sec-Fetch-Site`) is refused with 403 even with the header. A browser sends a
custom header to another site only after a CORS preflight, which ComfyUI refuses
unless it runs with `--enable-cors-header`. The thumbnail route can't ask for the
header, because an `<img>` sends none; it refuses `Sec-Fetch-Site: cross-site` and
`same-site` instead, and serves only the preview beside a model file (`name` must
end in a model extension) in one of ComfyUI's model folders. A `name` with a
drive, a leading slash or a `..` step finds nothing. The guard doesn't stop DNS
rebinding, and neither does ComfyUI's own server, so keep ComfyUI off untrusted
networks.

The Prompt Writer sends only to NanoGPT, to `localhost` / `127.0.0.1` / `::1`, or
to a host you saved a key for through `POST /warp-studio/llm/key`. Any other
`base_url` gets `<host> has no saved key.` A provider that needs no key works once any text is saved as its key; it is sent as `Authorization: Bearer <text>`, which such a server ignores.
A `base_url` with a user name, a backslash, `?` or `#` is refused, and so is a
redirect from a remote provider to this computer or the local network.

If `aiohttp` or `server` cannot be imported — running the module outside
ComfyUI, for tests or a lint — registration is skipped and the nodes still work.
The frontend degrades with it: tags go uncoloured and the Gallery says it could
not load.

```
GET /warp-studio/loras
GET /warp-studio/embeddings
GET /warp-studio/models
GET /warp-studio/model/base?name=<name>
GET /warp-studio/lora/thumbnail?name=<name>&kind=<loras|embeddings|checkpoints|diffusion_models|missing>[&full=1]
GET /warp-studio/loras/missing
GET  /warp-studio/llm/models?base_url=<url>[&refresh=1]
GET  /warp-studio/llm/system_prompts
GET  /warp-studio/llm/system_prompt?name=<name>
GET  /warp-studio/llm/key_status?base_url=<url>
POST /warp-studio/llm/key
POST /warp-studio/llm/title
POST /warp-studio/llm/write
```

The seven `llm` routes serve the 🌀 Prompt Writer pop-up. `models` returns
`{models: [{id, name, prompt_price, completion_price, vision}]}`,
`system_prompts` returns `{names: [...]}`, `system_prompt` returns `{name, text}`
of one of them, `title` takes `{base_url, model, source}` and returns `{title}`
for a new chat, `key_status` returns
`{set, source, host}`, `key` takes `{base_url, key}`, and `write` takes
`{base_url, model, system_prompt, temperature, reasoning_effort, idea, previous_result,
references, turns, loras, chat_id, lora_scope}` (`references` is a list of image names in `input/`; an empty `idea`
with a `previous_result` asks for that prompt to be optimised). It answers as
`application/x-ndjson`: `{"type": "delta", thinking, text, tokens}` lines while the
model writes (`tokens` is an estimate unless the provider counts), then one
`{"type": "done", ...}` line with `{text, reasoning, seconds, usage, cost, suggestions}`
or `{question, ...}`. The others answer with plain JSON. Each gives `{error}` instead
when something fails, and none ever returns a key. Closing the `write` connection
stops the request at the provider.

---

### `GET /warp-studio/loras`

Every LoRA in the configured folders, with everything the Gallery needs and no
image data.

```json
{
  "loras": [
    {
      "id": "sdxl/atelier - Detail Tweaker - v2.0 (SDXL).safetensors",
      "folder": "sdxl",
      "creator": "atelier",
      "name": "Detail Tweaker",
      "version": "v2.0",
      "tagged_base": "SDXL",
      "base_model": "SDXL 1.0",
      "structured": true,
      "title": "Detail Tweaker (SDXL)",
      "url": "https://civitai.red/models/58390?modelVersionId=583901",
      "triggers": ["highly detailed", "intricate detail"],
      "has_preview": true,
      "model_id": 58390,
      "version_id": 583901,
      "published": "2025-03-14T10:22:41.000Z",
      "added": "2025-04-02T18:05:09.123456+00:00",
      "kind": "loras",
      "thumbnail": "/warp-studio/lora/thumbnail?name=sdxl%2F...&kind=loras"
    }
  ]
}
```

| Field | Where it comes from |
| --- | --- |
| `id` | the exact string a `<lora:...>` tag has to resolve to. Uses the platform's separator, so a Windows entry reads `sdxl\name.safetensors`; tag matching ignores slash direction either way |
| `folder` | the first path segment inside the LoRA folder |
| `creator`, `name`, `version`, `tagged_base` | parsed from a `creator - name - version (base)` filename |
| `structured` | whether the filename actually had that shape — when false, `name` is the whole stem and callers should not pretend otherwise |
| `base_model` | the sidecar's `baseModel`, tidied |
| `title` | the sidecar's `model.name` |
| `url` | built from the sidecar's `modelId` and `id` |
| `model_id`, `version_id` | the sidecar's `modelId` and `id`, as integers; `null` when there is no sidecar or the value is not an integer |
| `published` | the sidecar's `publishedAt` as written, else its `createdAt`; `null` when neither is there |
| `added` | the weights file's modification time, ISO-8601 in UTC; `null` for an entry with no weights (a downloadable one) |
| `triggers` | the sidecar's `trainedWords`, split on commas |
| `has_preview`, `thumbnail` | whether an image sits beside the file; the server owns URL construction |

Sidecar-derived fields are `null` or empty when there is no `.civitai.info`.

Cost is one directory listing, one sidecar read and a handful of `stat` calls
per model. Against 761 files it answers in well under a second and returns a few
hundred kilobytes of JSON.

---

### `GET /warp-studio/loras/missing`

The LoRAs whose `.civitai.info` is in a LoRA folder but whose weights are not,
for the Gallery's **Not on this drive** filter. The same entries as
`GET /warp-studio/loras`, keyed `loras`, each with `"missing": true`. Their
`thumbnail` uses `kind=missing`. It reads the folders on each call, off the
event loop.

---

### `GET /warp-studio/embeddings`

Identical, keyed `embeddings`. Every entry carries `"kind": "embeddings"`, which
is how the frontend knows to insert `embedding:name` rather than a `<lora:>`
tag.

---

### `GET /warp-studio/models`

The checkpoints and diffusion models, in the same shape as the LoRA index and
keyed `models`. Each entry's `kind` is `checkpoints` or `diffusion_models`, and
its `thumbnail` uses that kind.

---

### `GET /warp-studio/model/base`

The base model of one file, for matching LoRAs against the checkpoint that is
wired in.

```
GET /warp-studio/model/base?name=demo%20-%20Illustrious%20Base%20-%20v1.0%20(SDXL).safetensors
```

```json
{ "name": "...", "base_model": "SDXL 1.0", "found": true }
```

Looked up across `loras`, `checkpoints`, `diffusion_models`, `unet` and
`embeddings`. `found` is whether the file resolved at all; `base_model` is
`null` when it did but declares nothing.

---

### `GET /warp-studio/lora/thumbnail`

The preview image beside a model.

| Parameter | |
| --- | --- |
| `name` | the model's `id` from the index |
| `kind` | `loras`, `embeddings`, `checkpoints`, `diffusion_models` or `missing` (the preview of a LoRA that is not on this drive); anything else is a 400 |
| `full` | `1` to serve the original instead of the thumbnail |

Without `full`, a WebP capped at 320px on the long side, built on first request
and cached by path, size and mtime — so replacing a preview refreshes it.
Measured against a real collection: previews average 1.27 MB, thumbnails 6–9 KB,
about 20 ms to build and under a millisecond to serve after that. Both are sent
with `Cache-Control: max-age=86400`.

404 when the model has no preview beside it, or when a thumbnail cannot be built
(no Pillow, or nowhere writable to cache it).

#### On paths

`name` is resolved through ComfyUI's `folder_paths`, which is what keeps this to
configured model folders — an arbitrary path can never be requested, and `..`
is neutralised before the lookup. The route serves preview images beside
configured models and nothing else.

These routes are unauthenticated, exactly as the rest of ComfyUI's are. Anyone
who can reach your ComfyUI can list your model library through them, which is
worth knowing if you have bound the server to something other than localhost.

<a id="civitai-panel-routes"></a>

### Warp Studio panel routes

The panel's routes all start with `/warp-studio/updater/`, and return JSON.

Every request must send the header `X-Civitai-Updater: 1`, and a POST body must
be sent as `Content-Type: application/json`. A request whose `Origin` is not the
ComfyUI page itself, or that the browser marks `Sec-Fetch-Site: cross-site`, is
refused. Refused requests get `403` with `{"error": "..."}`. This keeps other web
pages from using ComfyUI's unauthenticated server to change the settings or start
scans. Example:

```bash
curl -H "X-Civitai-Updater: 1" http://127.0.0.1:8188/warp-studio/updater/config
```

#### `GET /warp-studio/updater/config`

Returns:

- `config`: public config (`apiKey` is always empty; `hasApiKey` says whether one is stored)
- `supportedModelTypes`
- `effectiveRoots`: resolved roots per model type (Comfy defaults + `extra_model_paths.yaml` + configured custom paths)

#### `POST /warp-studio/updater/config`

Every field is optional; only the fields present in the request are updated.

- `apiKey`: string
- `cacheTtlMinutes`: integer, 0–10080 (used by the panel to decide when a re-check is suggested)
- `requestTimeoutSeconds`: integer, 5–300
- `maxRetries`: integer, 0–10
- `requestDelayMs`: integer, 0–3000
- `useComfyPaths`, `useExtraModelPaths`, `useCustomPaths`: boolean path-source toggles
- `treatSidecarsAsInstalled`: boolean (defaults to `true`)
- `matureMode`: `show` | `blur` | `hide` (defaults to `show`)
- `namingModel`: string, the text encoder file the renamer runs
- `fallbackCivarchive`: boolean, look up previews on civarchive (off by default)
- `fallbackHuggingFace`, `fallbackAiToolkitFolders`: a list or a string separated by `;` or new lines. Hugging Face authors or `owner/name` repos; folders that hold ai-toolkit runs
- `fallbackOwnCreator`: string, the creator name your own trainings are filed under
- `customPaths`: object keyed by model type (`checkpoint|lora|vae|unet|embedding`); a value is a list of paths or a string separated by `;` or new lines. Only the keys present in the request are updated.
- `archiveFolder`: string, one absolute folder of archived models (`""` clears it). It is checked like a custom path: it must exist, not be a whole drive, and not be, lie inside or hold a model folder, or it is left out and `pathWarnings` says why.

A newly added custom path must be an existing absolute folder and not a whole
drive or the filesystem root. Paths that fail this are left out of the saved
config; paths already saved are kept as they are.

Response: `config` (public) and `effectiveRoots`, as for `GET`, plus
`pathWarnings`: one message per custom path that was not saved.

#### `POST /warp-studio/updater/jobs/scan`

Starts a metadata scan job (the **Fetch Missing Metadata** button).

Request body:

- `modelTypes`: string array (defaults to all)
- `includeCustomPaths`: boolean (defaults to `true`)
- `refetchMetadata`: boolean
- `forceRehash`: boolean

Response: `jobId`. Returns HTTP 409 with `error` when another job is running.

#### `POST /warp-studio/updater/jobs/check-updates`

Starts an update-check job.

Request body: same as the scan job, plus

- `includeArchive`: boolean (defaults to `false`). When `true` and an archive
  folder is set, the archive is checked after the drive's check ran to its end
  (**Check with archive**).

Response: `jobId`, or HTTP 409 as above.

The archive pass only reads the archive's `.civitai.info` files. Its items are
saved in `archive_check.json` in the data folder, never in `last_check.json`, and
a check without `includeArchive` keeps them. The job's `summary` then carries
`archive` (when the archive was last checked, with its counts; `withUpdates` is
the archived-only models with a new version Updates shows, after Hide). When the
folder can't be reached, holds no folder named like a model folder, or overlaps a
model folder, it also carries `archiveError` and the saved results stay. A
stopped archive pass saves nothing.

The job is seeded with every item of the previous check so the panel keeps
showing them (dimmed, as provisional) while models are re-checked. Once the job has
listed the files of the checked types, those seeds are aligned with the disk:
seeds for deleted files are dropped, and new files with a usable
`.civitai.info` get a seed built from it, so a release downloaded since the
last check is not offered as new. When the job finishes, the items of the checked model types are replaced and the items of
any other type are carried over, then the whole set is saved as the new cache.

#### `GET /warp-studio/updater/jobs/active`

Returns `job` (the job record without items) for the running, queued, or
paused job, or `null`.

#### `GET /warp-studio/updater/jobs/{job_id}`

Returns job state:

- `status`: `queued|running|paused|completed|failed|cancelled`
- `progress`, `total`, `message`
- `summary`
- `itemCount`
- `items` (optional compatibility payload; avoid for UI paging path)
- `errors`

Query:

- `includeItems`: `0|1` (default `0`)

Summary shape depends on mode:

- scan: `total`, `refreshed`, `skipped`, `notFound`, `errors`
- check: `total`, `resolved`, `withUpdates`, `hiddenUpdates`, `notFound`, `errors`

Both carry `sidecarWarnings`, `modelTypes`, and `includeCustomPaths`.

#### `GET /warp-studio/updater/jobs/{job_id}/items`

Query parameters:

- `offset`, `limit`: paging over cards (limit is capped at 500)
- `mode`: `updates` to list only models with a newer release, `issues` to list
  only models with a file Civitai could not match (`not_found`) or check
  (`error`); omitted lists every model
- `modelType`, `baseModel`: repeatable filters
- `showHidden`: `1` to include releases you hid
- `sort`: `name` | `name-desc` | `type` | `latest-date` | `latest-date-desc` | `behind`
- `groupBy`, `thenBy`: `none` | `type` | `baseFamily` (a value equal to `groupBy` is ignored)
- `collapsed`: repeatable group key, either `Checkpoint` or `Checkpoint||SDXL`
- `mature`: `show` | `blur` | `hide`; omitted falls back to the stored setting

Response fields: `jobId`, `totalItems`, `offset`, `limit`, `mode`, `facets`,
`groups` (the outline with counts for the whole result set), `grouping`,
`startsMidPrimary` / `startsMidSecondary` (whether the page opens inside a
group that began earlier), `matureHidden`, `matureMode`, `modeCounts`
(`{updates, issues}`: how many models each view lists under the same filters),
`items`.

Each item is one model and carries `groupPrimary`, `groupSecondary`,
`groupPrimaryKey`, `groupPathKey`, and:

- `localVersions`
- `newVersions`
- `hiddenNewVersions`
- `isProvisional`: the card still comes from the previous check's results
  while a check runs
- `issueCount`: how many of its local versions are `error` or `not_found`

Each new or hidden version carries `paid`: `permanent` (downloading costs Buzz,
with no free date), `early` (early access, free from `paidUntil`, which is empty
when Civitai gives no end), or empty. An early-access window that has closed
since the check reads as empty.

Each local version carries the file's check `status` (`ok`, `error`,
`not_found`) and, for `error`, the `error` message. It also includes
`metadataOnly`, which is `true` when the installed
version is represented only by a valid `.civitai.info` sidecar. `modelPath` is
the file the entry was found through (the sidecar, for a metadata-only entry);
`filePath` is always the weights file, where it would be for a metadata-only
entry, with the extension the sidecar names (`.safetensors` when it names none).
`archived` marks a file in the archive folder; a version on this drive that is
also archived carries `alsoArchived` (the archive file's name), and `infoOnly`
when only its `.civitai.info` is on this drive.

#### `GET /warp-studio/updater/last-check`

Returns `sidecarWarnings` and `data`:

- `null` when no check has been saved; `cacheInvalid: true` is added when the
  saved file is from an older schema
- while a check is running: `jobId`, `checkedAt`, `summary`, `itemCount`, `inProgress: true`
- otherwise the saved check: `jobId` (always `cached`), `checkedAt`, `summary`,
  `itemCount`, `inProgress: false`, `filesChanged`, `filesAdded`, `filesRemoved`,
  and `archive`: when the archive was last checked (`checkedAt`) with its counts
  (`withUpdates` as in the check job's summary), or `null` when there is no
  archive folder or no saved archive check for it

The saved check is loaded as a job with id `cached`, so its items can be paged
through `GET /warp-studio/updater/jobs/cached/items`.

#### `GET /warp-studio/updater/model-stats`

Civitai's numbers for each model, for the Gallery's sorts. Returns
`{"stats": {"<modelId>": {"downloads": n, "likes": n, "comments": n}}}`, one
entry per model id the saved checks hold numbers for, and `{"stats": {}}` before
any. They come from the last check; the drive's results win over the archive's
for a model in both.

#### `POST /warp-studio/updater/archived-updates`

Persistently hides one or more remote version IDs for a model.

Request body:

- `modelId`: string
- `versionIds`: string array

Response: `modelId`, `archivedVersionIds`.

#### `POST /warp-studio/updater/archived-updates/restore`

Removes previously hidden remote version IDs for a model. Same body and response.

#### `POST /warp-studio/updater/jobs/{job_id}/pause`

Pauses a running job.

#### `POST /warp-studio/updater/jobs/{job_id}/resume`

Resumes a paused job.

#### `POST /warp-studio/updater/jobs/{job_id}/stop`

Requests cancellation of a running or paused job.

#### Rename and other panel routes

These serve the Rename view. Same guard as above; every one answers JSON.

```
GET  /warp-studio/updater/rename/status
POST /warp-studio/updater/jobs/rename-dry-run
GET  /warp-studio/updater/rename/rows
POST /warp-studio/updater/rename/decision
POST /warp-studio/updater/rename/base-code
POST /warp-studio/updater/jobs/rename-apply
POST /warp-studio/updater/rename/recover
POST /warp-studio/updater/rename/reveal
POST /warp-studio/updater/reveal
POST /warp-studio/updater/local-preview
POST /warp-studio/updater/jobs/download-naming-model
```

- `rename/status` says whether the renamer can run (a ComfyUI with **Generate Text**, a naming model) and, if not, why. It also carries the id of the last saved dry run.
- `jobs/rename-dry-run` and `jobs/rename-apply` start jobs like the update jobs, and answer HTTP 409 while another job runs or a rename journal is waiting to be finished or undone. Poll them through `jobs/{job_id}`.
- `rename/rows` pages the dry run's rows; `rename/decision` saves an edit, a keep or an **Ask again** for one model; `rename/base-code` saves a base code.
- `rename/recover` finishes or undoes an interrupted rename.
- `rename/reveal` and `reveal` show a file in its folder; `local-preview` returns a local preview image to the panel.
- `jobs/download-naming-model` downloads the default naming model into the first `text_encoders` folder, unless it is already in one.

#### Downloads and Delete

```
GET  /warp-studio/updater/downloads
POST /warp-studio/updater/downloads
POST /warp-studio/updater/downloads/{request_id}/stop
POST /warp-studio/updater/delete-model
POST /warp-studio/updater/delete-file
```

- `GET downloads` returns `{jobId, requests, canTrash}`: the running download job's id (or `null`) and every request with its state (`waiting`, `downloading`, `done`, `stopped` or `error`), progress and message. A reloaded panel reads it to paint its cards.
- `POST downloads` takes `{source: "updates", jobId, modelId, versionId, modelPath, replace}` for a release in a check job's results, or `{source: "gallery", name}` for a LoRA that is not on this drive. It answers `{request, jobId}`. A release in early access, a file not in the results, or a second Replace of the same file is refused. A request made while another kind of job runs is not refused: it waits (its record in `GET downloads` carries `waitingFor`, the job's type) and starts when that job ends. Only a rename that was cut off and still needs recovering answers HTTP 409.
- `POST downloads/{request_id}/stop` cancels a waiting request or stops the running one. `404` for an unknown id.
- `POST delete-model` takes `{name, kind}`: `name` is a model's `id` from the index, and `kind` is one of `loras` (the default), `embeddings`, `checkpoints` or `diffusion_models`; any other `kind` answers 400. It sends the weights and sidecars to the Recycle Bin and answers `{removed, warnings}`. It is refused (409) without `send2trash`, while another kind of job runs, or while that file is waiting to be replaced, and answers 404 when the LoRA is gone.
- `POST delete-file` takes `{jobId, path}`: the `modelPath` of a version row in that check job's results (an Updates or Issues card). It deletes only a file the job lists, the same way as `delete-model`, and answers `{removed, warnings}`. A path the job doesn't list, or a row with only an info file, answers 404. An archived row answers 409 (the archive is never changed), as do the `delete-model` refusals. A missing `jobId` or `path` answers 400.

---

## Architecture

```
warp_pipe.py          every node, the storage, the resource scan, the routes
prompt_writer.py      the Prompt Writer's backend and its /warp-studio/llm routes
comfy_updater/        the Warp Studio panel's backend, see below
studio/               the first-start data migration and the check for old packages
standdown/            the notice shown when an old package is still active
__init__.py           starts both halves, or stands down for an old package
web/
  civitai_updater.js  the Warp Studio sidebar panel
  rename_view.js      its Rename view
  gallery_view.js     its Gallery view: the cards, the bands, the marks
  gallery_core.js     the Gallery's search, filters, bands and card data, without markup
  prompt_target.js    which Prompt + LoRAs node a Gallery click adds to
  scroll_memory.js    each panel view's own scroll offset
  storage_migration.js  carries the Rename view's saved choices over
  prompt_ui.js        the coloured prompt box, the strip, inline completion
  lora_browser.js     the Browse LoRAs button, which opens the panel's Gallery
  model_base.js       which base model a node is wired to
  library.js          one fetch of the index, shared by both views
  text.js             escaping and filename helpers
  widget_values.js    saving widget values by name rather than by position
  save_image_ui.js    friendly labels, and the toast when nothing was saved
  provider_ui.js      the size widget's name, and today's label for an old preset
  size_presets.js     matching an old preset label to a new one
  warp_send.js        Warp Send's activation and slider, Warp Select, the preview mirror
  warp_route.js       which Send is active, and whether the route can run
  docs/*.md           the per-node help ComfyUI shows behind the ? button
WIKI.md               this documentation
tests/                pytest, no ComfyUI required
tests/web/            node --test for the frontend modules that need no page
```

### How a warp travels

A `WARPPIPE` link does not carry the data. It carries `{"id": "<uuid>"}`, and
the data sits in a module-level dict keyed by that id.

That is a deliberate trade. Passing tensors and model patchers down a link would
have ComfyUI copy and cache them at every hop; passing a token means a Warp
costs the same whether it carries a seed or six models.

The cost is that the dict holds references, so a stored bundle keeps its models
alive. Two limits bound it: entries older than an hour are pruned, and no more
than 256 are kept, oldest evicted first. An Unwarp refreshes an entry's
timestamp when it reads it, so a warp in active use never expires. All of it is
under one lock, and the cleanup runs on insertion rather than on a timer.

Each `Warp` instance gets its id at construction. ComfyUI caches node instances
per graph node, so the id is stable across runs of the same workflow.

### Validation

Sampler and scheduler sockets are the awkward part of this pack.

ComfyUI types a combo socket by its list of options, so a `sampler_name` coming
out of another node pack with a wider list is a *different type* to ComfyUI's
own, and the link is refused. Warp Studio wants those links to work.

Earlier versions patched ComfyUI's global prompt validator, which affected every
node in the process. That is gone. Instead each node declares
`VALIDATE_INPUTS(input_types)`, which:

- keeps normal socket checks for everything else — an `IMAGE` into a `MODEL`
  still fails, and says so;
- relaxes only `sampler_name` and `scheduler`;
- accepts a comma-joined enum string that shares any member with what was
  expected, which is how some builds spell a combo type.

Whatever arrives is then coerced at run time: an unknown sampler becomes
`euler`, an unknown scheduler `karras`. The allowlists are snapshots of
ComfyUI's lists taken at import, not references to them — a reference would grow
as other packs registered their own schedulers, and coercion would stop
coercing.

### Finding the resources for a saved image

`Save Image (Civitai)` needs to know which checkpoint and which LoRAs made the
picture. It reads the prompt graph ComfyUI passes it as a hidden input.

1. **Walk back from this node.** Breadth-first over the links, recording how far
   each node is. Nodes not upstream are ignored, so a second branch of a large
   workflow contributes nothing.
2. **Follow only the taken branch of a switch.** A switch names its inputs
   `input1..inputN` and carries a `select`; the others are wired but never ran.
   The selector is usually a small constant node one link away, so that is
   followed too — but only when it is unambiguous. When the choice cannot be
   read, every branch is followed, as before, because dropping a real resource
   is worse than including a spare one.
3. **Recognise loaders by the input they carry**, not by class name — `ckpt_name`,
   `unet_name`, `model_name`. A name that resolves inside the checkpoint,
   diffusion-model or UNet folders is a model whatever the input is called, so a
   loader from an unknown pack still records what it loaded.
4. **Collect LoRAs** from `lora_name`, from numbered `lora_name_1` slots with
   their matching strengths, from rgthree-style `{"lora":…, "on":…}` dicts
   honouring the on/off switch, and from `<lora:...>` tags in any string input
   with comments stripped first.
5. **Nearest model wins.** With several upstream, the one fewest links away is
   the one that made this picture — a refiner in front of a base. Equal
   distances fall to the order the graph listed them.

Then each name is resolved to a file and hashed. A name that resolves to nothing
is logged and left out: crediting a LoRA that was never applied describes an
image that was not made.

### Hashes and metadata

Civitai matches a resource by AutoV2 — the first ten hex characters of the
file's SHA256. Warp Studio takes it from the sidecar when one is there and hashes
the file when it is not, caching by path, size and mtime so a replaced file
re-hashes and an unchanged one never does.

Two conventions go into the `parameters` block, because they are read by
different things:

- `Lora hashes: "name: hash"` — A1111's additional-networks spelling.
- `Hashes: {"model": …, "lora:name": …}` — what Civitai's own extension writes,
  and the field it actually links resources from.

Writing only the first is why an upload would name its checkpoint and credit
none of its LoRAs: the checkpoint comes from `Model hash`, which both agree on,
and the LoRAs were never anywhere Civitai looked.

JPEG has no text chunks, so for that format the same block goes into EXIF
`UserComment` as UTF-16BE behind a `UNICODE\0` marker — what A1111 writes and
what every reader of these files expects.

### The prompt box

The text is the only state. There is no list of LoRAs kept beside it, because two
sources of truth disagree eventually.

A transparent `textarea` sits over a highlight layer that renders the same
string as coloured spans. Every property that decides where a glyph lands is
copied from one to the other, including the width ComfyUI's scrollbar gutter
takes, or the colouring slides off the words. Typing, selection, undo and IME
are all the real textarea's.

Suggestions are *drawn* on that layer rather than written into the value.
Writing them meant undoing them on every keystroke, which raced with fast typing,
filled the undo stack with edits nobody made, and marked the workflow dirty
while merely browsing.

Edits go through `document.execCommand("insertText")` — the only way to change a
textarea that the browser's own undo still understands. Assigning to `value`
clears the undo stack, which would make every weight nudge a point of no return.

Layout is watched on a 100ms timer rather than with a `ResizeObserver`, which
only delivers while the page renders: a node resized in a background tab came
back with the layer at its old size. The timer, the observer and the strip are
all torn down on `onRemoved`.

### Refreshing

The library index is fetched once and shared by the prompt box and the Gallery,
because reading a sidecar per file is the expensive call on the server. A cache
that is never dropped goes stale, though: a LoRA added after the tab was opened
would keep reading as unknown.

ComfyUI invokes `refreshComboInNodes` on every extension when node definitions
are reloaded - the `r` key, or the menu - which is the same moment every other
node's model lists update. Warp Studio uses it to drop three things: the shared
library, the base model asked of the server per checkpoint, and each node's own
memo of what it had resolved. Nodes register themselves in a set while they
live, because walking the root graph would miss any inside a subgraph. It also
sends the `warpstudio:library-changed` window event, and a Gallery that has
loaded reads the library again.

### Widget values

ComfyUI serialises widget values positionally. Anything that changes widget
order between a save and a load — an extension reordering them, or a widget
added in a later version — shifts every later value onto the wrong widget,
silently. Booleans are the worst of it, because the wrong value is still valid.

This pack reorders its own widgets, so it also writes them by name into the saved
workflow and reads them back by name. A workflow saved before that existed falls
back to a per-node migration that knows what the old shape meant.

### Registration

`NODE_DEFINITIONS` maps node id to display name and class, and both registration
paths are built from it. Keeping two lists in step by hand is exactly how V3 mode
once shipped five of the seven nodes, with nothing said about the other two.

The V3 implementation is opt-in behind `WARPPIPE_ENABLE_V3=1` while the upstream
API settles. A test asserts both paths cover the same set.

### Compatibility shims

`comfy.samplers` is imported behind a `try`, with a small mock behind it, so the
module imports outside ComfyUI for tests and lint. The mock exists only to make
import work; it is not a simulation of ComfyUI.

`beta57` and `bong_tangent` are registered globally against the karras handler so
other packs accept a workflow built around RES4LYF's schedulers even when
RES4LYF has not loaded. Startup logs which names it stood in for.


<a id="the-civitai-panel"></a>

### The Warp Studio panel

The panel has two layers: a backend service (`comfy_updater/`) and a frontend sidebar tab (`web/civitai_updater.js`). Both are started from `__init__.py`.

#### Backend modules

- `plugin.py`: bootstraps the config store, archive store, job manager, and route registration
- `routes.py`: HTTP endpoints under `/warp-studio/updater/*`, the check-result cache, and merging of partial checks
- `jobs.py`: thread-based background job manager, result grouping, sorting, and the group outline
- `updater_service.py`: scan/check pipeline for one model file at a time
- `path_resolver.py`: resolve Comfy roots + `extra_model_paths.yaml` + custom roots, discover model files and orphan sidecars
- `hashing.py`: SHA256 file hashing
- `civitai_client.py`: Civitai API client with retries, preview download, and video frame extraction
- `sidecar.py`: sidecar file read/write helpers and corrupt-file quarantine
- `config_store.py`: persistent settings in `user/warp-studio/updater/config.json`
- `archived_updates.py`: persistent list of hidden releases in `user/warp-studio/updater/archived_updates.json`
- `base_models.py`: rolls Civitai base-model strings up into families for grouping
- `constants.py`: API URLs, supported types and extensions, sidecar suffixes, plugin version
- `node_info.py`: the status node. ComfyUI registers it from the legacy mappings; the V3 `comfy_entrypoint` is only a fallback that ComfyUI does not reach while those mappings exist

#### Frontend module

`web/civitai_updater.js`:

- registers sidebar tab with `registerSidebarTab`
- registers native Comfy settings via `app.registerExtension({ settings: [...] })`
- syncs settings to backend config route
- starts/polls jobs and renders result cards with thumbnails
- reconnects to a running job after a page reload, and falls back to the
  on-disk cache when the server forgets a job (for example after a restart)

#### Data flow

1. UI starts scan/check job via backend route.
2. Job manager launches a worker thread.
3. Updater service enumerates model files.
4. Service identifies files (sidecar ids, stored hash, or a fresh hash) and queries Civitai.
5. Sidecar files are updated.
6. Job output is polled by UI and rendered as result cards.
7. A finished check is written to `user/warp-studio/updater/last_check.json`; a check
   limited to some model types replaces only those types in the file.

#### Design constraints

- Keep v1 read-only regarding model file replacement.
- Keep sidecar files human-readable JSON.
- Keep Civitai auth optional.
- Never overwrite a user data file that failed to parse; move it aside first.

---

## Development

### Setting up

Clone anywhere and link it into ComfyUI rather than working inside
`custom_nodes`:

```powershell
$src = "C:\path\to\ComfyUI-WarpStudio"
$dst = "C:\path\to\ComfyUI\custom_nodes\warp-studio"
New-Item -ItemType Junction -Path $dst -Target $src
```

```bash
ln -s /path/to/ComfyUI-WarpStudio /path/to/ComfyUI/custom_nodes/warp-studio
```

### Checks

Everything CI runs, in the order it runs it:

```bash
python -m ruff check . --exclude .agents --exclude .claude
```

```bash
python -m ruff format --check . --exclude .agents --exclude .claude
```

```bash
python -m pytest -q
```

```bash
npm ci && npm run check
```

The Python tools are pinned in the workflow and the frontend lockfile is
committed, both for the same reason: a build going red should mean the code
changed, not that a tool released that morning.

`npm run check` is eslint plus prettier over `web/`. `npm run format` writes the
formatting rather than checking it.

### Tests

`pytest` needs no ComfyUI. `tests/conftest.py` installs a fake `comfy`,
`comfy_api` and `torch` into `sys.modules` and loads `warp_pipe.py` fresh for
each test, so legacy and V3 registration can both be exercised in one run.

The fakes exist to make import work and to let schemas be inspected. They are
not a simulation of ComfyUI: anything depending on real execution semantics has
to be tried in ComfyUI.

Worth knowing when adding tests:

- `warp_pipe` and `package_loader` fixtures give you a freshly imported module;
  `warp_pipe_loader(enable_v3=True)` gives you the V3 path.
- A test that asserts a fix should be checked against the unfixed code once, by
  reverting the change and watching it go red. A regression test that never
  could have failed is decoration.
- `test_the_package_ships_every_file_the_frontend_needs` reads `pyproject.toml`
  and asserts every file under `web/` is matched by a `package-data` glob. Add a
  `.css` there and it will tell you before the wheel does.

### Adding a node

1. Write the class in `warp_pipe.py` with `INPUT_TYPES`, `RETURN_TYPES`,
   `RETURN_NAMES`, `FUNCTION` and `CATEGORY`.
2. Add it to `NODE_DEFINITIONS`. That is what both registration paths are built
   from, so this is the only list.
3. Add a V3 class and an entry in `V3_NODE_CLASSES`. Startup logs an error
   naming anything in `NODE_DEFINITIONS` without one, and a test fails.
4. Write `web/docs/<Node ID>.md`. ComfyUI shows it behind the `?` on the node.
5. Document it under [Node reference](#node-reference) and, if it is worth
   showing, in the README.

V3 schemas share one field-id namespace across inputs and outputs, so an output
mirroring an input needs a distinct id and a matching `display_name` — `model`
in, `model_out` displayed as `model` out.

### Screenshots

`assets/docs/*.webp` are captured from a real ComfyUI, not mocked. The setup:

1. Build a small demo LoRA folder — plausible filenames in the
   `creator - name - version (base)` convention, `.civitai.info` sidecars, and
   generated stand-in preview art. Never the real library; a public README
   should not carry somebody's model collection.
2. Run a second ComfyUI on another port with `--base-directory` pointed at that
   folder, so the running instance is untouched. `--base-directory` does not
   override `extra_model_paths.yaml`, so copy `main.py` and the other loose
   modules into a scratch directory beside junctions to the package directories;
   ComfyUI then looks for the config next to that copy and finds none.
3. Drive it with Playwright against an installed Chrome, at
   `deviceScaleFactor: 2`, hiding `.workflow-tabs-container`,
   `.side-toolbar-container`, `.subgraph-breadcrumb` and `.actionbar-container`
   so the app's own chrome stays out of the clip.

Save flat UI shots as lossless WebP and anything with real gradients as quality
90. Palette-quantised PNG bands badly on preview art.

### Releasing

1. Move the `[Unreleased]` block in `CHANGELOG.md` under a new version heading
   with today's date.
2. Set the version in **both** `pyproject.toml` and `version.txt`.
3. Merge to `main`.

The publish workflow fires on a push touching `pyproject.toml`, but only
publishes when the version there actually changed against the previous commit —
editing lint config does not re-publish. A manual `workflow_dispatch` always
publishes.

Semver as this project reads it: a new node or a new capability is a minor; a
change that makes an existing workflow load differently is a major. 3.0.0 was
the `CONTROL` → `WARPPIPE` rename. 4.0.0 is the prompt box and the Civitai save
node — nothing breaks, but it is a different pack to use. 5.0.0 is the same
again: the Prompt Writer, Warp Send and Select, and the prompt boxes as a code
editor. Warp Studio starts again at 1.0.0; the numbers of the two packages before it are
linked at the end of the [changelog](CHANGELOG.md).

### Registry metadata

`[tool.comfy]` in `pyproject.toml` carries the publisher, display name, and raw
GitHub URLs for `assets/registry/icon.png` and `assets/registry/banner.png`.
Those two are the sized, quantised copies — the originals live in
`assets/source/` and are far too heavy for a listing page.

---

## Troubleshooting

### The nodes do not appear

Restart ComfyUI after installing, then check the startup log. If Warp Studio says it is
switched off, an old WarpPipe or Civitai Updater is still active: update or disable it, see
[Coming from WarpPipe or Civitai Updater](#coming-from-warppipe-or-civitai-updater). Otherwise it logs
`WarpPipe LoRA library routes registered` when it has loaded; an import failure
appears above that as a traceback naming the file.

If you installed by hand, the folder has to be directly inside `custom_nodes`,
with `__init__.py` at its top level.

### Some nodes are missing, but not all

If `WARPPIPE_ENABLE_V3` is set in your environment, Warp Studio registers through
ComfyUI's V3 API. Should a node ever lack a V3 schema, startup logs an error
naming it. Unset the variable to fall back to the legacy path, which registers
everything.

### The prompt box is a plain text field

No colouring, no strip, no `/` completion means the frontend did not load. Check
the browser console for a failed import of `prompt_ui.js` under `/extensions/`.

If you installed from the registry with a version before 4.0.0, the wheel did
not ship the JavaScript at all — `package-data` listed only the documentation.
Upgrading fixes it.

### Every tag is red

Red means no file matched. If *every* tag is red, the library did not load
rather than every name being wrong. Open `/warp-studio/loras` directly:

```
http://127.0.0.1:8188/warp-studio/loras
```

An error or an empty list points at ComfyUI's LoRA folder configuration, not at
your prompt.

### A tag is red but the file is there

The name has to identify exactly one file. Try, in order: the filename without
folder or extension, then several words from it in any order. A bare fragment
that matches several files cannot be resolved — in one real collection, 24 files
share a single common phrase.

A near miss suggests the closest names in the message, which usually settles it.

### The run stops with "matches no file" or "matches N files"

That is deliberate. Generating without a LoRA the prompt asked for gives a wrong
picture *and* wrong metadata, so an unresolvable tag fails the run rather than
being skipped with a warning nobody reads. Fix the tag, or comment it out with
`//`.

<a id="no-previews-in-the-browser"></a>

### No previews in the Gallery

Previews are read off the disk: an image beside the model file named
`<model>.preview.png`, `.preview.jpg`, `.preview.webp`, or just `<model>.png`.
The [Warp Studio panel](#warp-studio-panel) downloads them (**Fetch Missing Metadata**); so do most model managers.

If the files are there but the cards are blank, the thumbnail cache has nowhere
to write. It goes next to ComfyUI's user directory; check that is writable, and
that Pillow is importable in ComfyUI's Python.

### A LoRA I just added does not show up

The library is read once and cached in the browser, so a file added since the
tab was opened is not there yet. Press `r`, or pick **Refresh Node Definitions**
from the menu — the same thing that reloads every other node's model lists now
drops Warp Studio's cache as well. The prompt box recolours and the Gallery lists
it without a page reload.

The same applies to a `.civitai.info` written since: refresh and the trigger
words, base model and link appear.

### No trigger words anywhere

Trigger words come only from `.civitai.info` sidecars — there is no fallback,
because nothing else on disk knows them. Run the Warp Studio panel's **Fetch Missing
Metadata** to fetch them without checking for updates.

`Tab` on a tag always answers: it says the LoRA declares none rather than doing
nothing, so you can tell a missing sidecar from a key that did not register.

### Everything is grouped under folder names, not base models

Same cause. `baseModel` comes from the sidecar; without one, Warp Studio falls back
to the folder, which is right for collections filed that way and meaningless for
the rest.

### A tag is orange

The file is real but its sidecar declares a different base model to the
checkpoint you have wired in. It will load; it usually does nothing useful. You
may know better than the sidecar — the warning does not stop anything.

With nothing connected to `model`, nothing can be judged and nothing goes
orange.

If every LoRA is orange, the checkpoint probably has no sidecar and is being
judged by its folder name — the hover text then says *a model filed under …*.
File the LoRAs for it in a folder of the same name (`Unet\moss\` and
`Lora\moss\`) and they count as a match.

Bases are compared after tidying: case, spacing and punctuation are ignored, and
a trailing `-base` or `Turbo` is dropped, so `Krea 2 Turbo` matches `Krea 2` and
`ZImageTurbo` matches `ZImageBase`. Anything else that differs is a different
base — `Flux.2 Klein 4B` and `9B` stay apart.

### Civitai does not link the resources

Check the saved PNG actually has a `parameters` chunk — **Generation info** has
to be on, and something has to reach the `images` input.

Then check the resources were found. The node logs any name it could not resolve
and left out. The usual causes:

- The LoRA is not in ComfyUI's LoRA folder any more, so it was not applied
  either.
- The branch that loaded it was behind a switch that did not run — which is
  correct: it did not contribute to the image.
- The upload is a JPEG re-encode. Re-encoding strips EXIF, and with it the
  generation info.

### Saved nothing and said nothing

Fixed in 4.0.0. When nothing reaches `images` the node now reports it on screen
and in the log instead of showing a green tick over an empty save. If you see the
old silent behaviour you are on an older version.

### The workflow did not embed

JPEG has nowhere to keep it. The node saves the file and tells you the workflow
was dropped; the generation info still goes into EXIF, which Civitai reads. Use
PNG if you want the graph in the file.

### Schedulers named beta57 or bong_tangent behave oddly

Warp Studio registers those names globally so other packs — FaceDetailer especially
— accept a workflow built around them. Until RES4LYF itself is installed they
are karras under another label, which is logged at startup. Install RES4LYF for
the real implementations.

### A workflow from 2.x will not connect

3.0.0 renamed the internal type from `CONTROL` to `WARPPIPE`. Reconnect the
Warp-to-Unwarp link once and it will save correctly from then on.

### Outputs are None

An Unwarp returns `None` for anything the warp never carried, rather than
inventing a default. If a sampler is complaining about a missing value, connect
it into the Warp — or check that the Warp you are unpacking is the one you
think, since a chained Warp only carries what it was given plus what it was told.


<a id="the-civitai-panel-1"></a>

### The Warp Studio panel

<a id="the-civitai-tab-is-not-in-the-sidebar"></a>

#### The Warp Studio tab is not in the sidebar

1. Confirm the folder is directly under Comfy `custom_nodes`, and that Warp Studio is not switched off (see above).
2. Confirm restart was a full process restart.
3. Check terminal logs for `Civitai updater: routes registered`.
4. Check browser console for JS import/runtime errors.

#### Deprecation warnings in the startup log

Warnings such as:

- `Detected import of deprecated legacy API: /scripts/ui.js`

usually originate from other custom nodes using old APIs. They are not automatically a Warp Studio failure.

#### The panel finds no models

1. Verify selected model types.
2. Verify the effective root counts under `Resolved Roots` in the panel's settings and click `Show`.
3. Check the path source toggles under **Civitai Path Sources**.
4. Add custom paths under **Civitai Custom Paths**.
5. Re-run scan/check.

#### Many models are counted as `not found`

This means Civitai has no hash match for those local files. Typical reasons:

- converted/pruned/repacked files
- unpublished/private resources
- legacy models missing hash records on Civitai

#### Civitai refuses some models

If gated resources fail:

1. Set the API key under **Settings → Warp Studio → Civitai Network**.
2. Re-run the check.

### Reporting something else

Turn on debug logging and include the startup lines plus whatever the run
produced:

```python
import logging

logging.getLogger("WarpPipe").setLevel(logging.DEBUG)
```

Issues: <https://github.com/gregory-richard/ComfyUI-WarpStudio/issues>
