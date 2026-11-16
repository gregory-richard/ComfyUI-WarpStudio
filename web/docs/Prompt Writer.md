# Prompt Writer

Have an LLM write and rewrite an image prompt in a chat, and put the version you like into a 🌀 Prompt + LoRAs.

## Overview

Every **Prompt + LoRAs** has an **✨ AI Prompt Writer** button just above its prompt box, in the middle of the node's last row of inputs and outputs, so it takes no room from the text (on a narrow node it says only **✨ AI**). It opens the Prompt Writer, a pop-up with your chats on the left and the conversation on the right. Say what you want and press the round **↑** in the input (or Ctrl+Enter). Each answer is a new version in the chat. Press **Send to ComfyUI** on a version to write it into a Prompt + LoRAs. Writing a prompt never queues an image, and the Prompt Writer takes no part in a run.

Writing goes on in the background. You can close the pop-up (Esc, ×, or a click outside it), start something in another chat, or go back to the canvas. The **✨ AI Prompt Writer** button turns yellow while a chat opened from that node is writing and shows a green dot when one has finished and you have not looked at it yet. A toast says when a prompt is ready.

## Layout

The left rail holds **Prompt Writer** with its settings (the cog), **New chat** and your chats. On the right is the open chat: its title and **For** at the top, the versions in a reading column, and the input at the bottom.

- **Title** — Click to rename the chat. A new chat is named after your first request.
- **For** — The Prompt + LoRAs this chat writes for. **Send to ComfyUI**, **Edit current prompt** and the LoRA suggestions all use it. Each chat remembers its own choice by the node's label (its group title, else its own title). A new chat starts on the node you opened it from, and if a remembered label is no longer in the workflow the chat falls back to that node, else to the first one.

## Settings

The cog in the rail opens everything that is set once and then left alone:

- **Model** — Click to open the list and type to filter. Each entry shows the price per million input/output tokens and an eye for models that see images. A model that is no longer listed stays selected and reads "(not listed)".
- **System prompt** — The `.txt` files in your system prompt folder; the eye beside it shows the selected one, read-only.
- **Temperature** — 0–2, default 0.6.
- **Thinking** — How much the model may think before it writes: **Model default** sends nothing (NanoGPT then thinks at high on models that think), or **Off**, **Minimal**, **Low**, **Medium**, **High**, **Extra high**, **Max**, sent as `reasoning_effort`. More thinking costs more and takes longer. Not every model offers every level, and one a model refuses comes back as an error card.
- **Title model** — The cheap model that names new chats.
- **Server** — The OpenAI-compatible server. Default `https://nano-gpt.com/api/v1`. LM Studio, Ollama and llama.cpp work too; plain `http://` is allowed only for `localhost`, `127.0.0.1` and `::1`.
- **API key** and **Save** — See Setup.
- **Refresh lists** — Reloads the model list (otherwise cached for an hour) and the system prompt list.

## Chats

The **✨ AI Prompt Writer** button of a node opens the most recent chat for that node, or a new empty chat if it has none; while the pop-up is open it switches the same way. A node that is writing or has an unseen result opens that chat first.

**New chat** is at the top of the left rail. Each chat shows how many versions it has and when it last changed, a yellow `Writing 4 s` while it writes, `Waiting for your answer` while the AI has a question, and a green check (a ? for a question) until you open it. Its **⋮** menu renames or deletes it; deleting asks first. Several chats can write at the same time.

## Versions

Versions are numbered in the order they were made (v1, v2, v3…) and keep that order, the newest at the bottom. A number stays with its version: deleting one leaves a gap, and only the newest version's number is given out again. Each card says where it came from: `new prompt`, `edit of v2`, `hand edit of v4`, `hand edit of v4, in the node` or `from 🌀 Prompt + LoRAs`. A version the AI wrote also has tags for what it cost, how long it took and its tokens (e.g. `0.3 ¢` `8 s` `1,812 → 640 tokens`), then what you asked for with its images and any questions the AI asked on the way, and then the prompt it wrote. The LLM sees only what it edits, your request and those questions and answers, never the rest of the chat, which is why it is not shown as a conversation.

Under the prompt are two folded lines; click one to open it:

- **LoRAs** — With how many were active and how many the AI suggests; see LoRA suggestions.
- **Written with** — The model and system prompt the version was written with, right on the folded line. Open, it lists the temperature, thinking level, LoRA choice, server and when it was written, and shows the system prompt's text as the server read it for that write, so a version stays readable after you change the file. Versions written before this was kept show only the model and the system prompt's name.

**The graph** on the left of the cards shows which version came from which, like git history: a dot for each version (filled: written by the AI, hollow: edited by hand, square: taken from the node) and a line to every version made from it. A version's first follow-up continues its line; trying something else on a version that already has one branches off into a line of its own. The version the next request edits and its path back to the start are lit. Left of the graph, a **−** beside a version folds the versions made from it that lead away from where you are working, and its card shows how many (`+2`); **+** shows them again. The newest version, the one the next request edits and their paths never fold away, and a click on a card only opens or closes that card. Folds are saved with the chat; picking a folded version under **What the next request does** shows it again.

The newest version is open and editable. When you click away, what the AI wrote stays as it was and your text becomes a new version under it, a hand edit. Until that hand edit is sent to a node or a request starts from it, more typing changes it instead of adding another; an edit that only changes spacing is not kept. Older versions are one line with your request, or how they came to be; click to read them.

Above the prompt a line says what changed from the version it was made from, counted in words (`Changes from v2 +38 −12 words`), or that it is the same. **Show changes** switches the prompt for its changes: the new text reads as it is, with a margin line by each line that changed; what is new is lightly green, and right above each changed piece is, small and red, what it replaced (or, where something was only taken out, what went, over a thin red mark); a line that was taken out stays, small and red; when most of it is new (`Mostly rewritten from v2`), the two versions stand side by side instead. **Show prompt** switches back.

The buttons sit at the bottom of the open card. On a closed one they show while you point at it, except **✓ Input**, which always shows on the version your next request edits.

- **Send to ComfyUI** — Writes the version's text, exactly as it is, into the prompt box of the Prompt + LoRAs chosen under **For**, as one undo step. That node flashes and the pop-up closes. It writes only the prompt and never touches the LoRA lists.
- **Use as input** — Only chooses this version under **Edit …** (What the next request does) in the input below; that choice lights up, and nothing is sent. Your next request then edits this version, and what you have typed stays in the input. The version that is chosen shows **✓ Input** instead.
- **Retry** (on versions the AI wrote) — Writes the version again, at once, from the same request, images, answered questions and the version it edited, and replaces it with the new answer: same number, same place. It uses the model and settings chosen now, and **Written with** shows them afterwards. It is greyed out once other versions were made from this one, since they came from what it says now; use Recreate there. Stop leaves the version as it was. If the AI asks a question first, your answer still finishes the retry, and a failed retry keeps its own **Retry**.
- **Recreate** (on versions the AI wrote) — Sends the same request again, the same way, as a new version beside this one. This one stays, so you can compare the two.
- **Delete** (the bin on the left) — Asks first, in place of the buttons, and says how many versions made from this one go with it (`Delete v3 and the 2 versions made from it?`). It cannot be undone. The other versions keep their numbers. It is greyed out while a write, a question or a failed request starts from the version or one made from it.
- **Change the request** — The undo arrow beside a request (shown on hover) puts it back into the input with its images, editing the same version it edited, above anything you had typed there. Nothing is sent until you press **↑**, so you can change it first; what you send becomes a new version beside this one.

Retry, Recreate and the undo arrow wait while the chat is writing or while the AI waits for an answer, since the answer goes in the input.
- **Chat titles** — While the first version the AI writes is being written, a cheap model names the chat from your message (**Title model** in the settings, about 0.002 ¢ per title). A title you typed always stays; an empty field or a failure keeps the first words of your message.
- **⋮ on a chat** — **Rename** or **Delete** it.

A request that fails shows a red card with the message and **Retry**, and does not count as a version. The card is kept with the chat, so it is still there after a reload. **Retry** sends the request again with the settings as they are now (model, system prompt, temperature, server); the undo arrow takes it back into the input instead.

## Writing a request

The box at the bottom grows with your text. Images you add show above the text, and a row of tools sits under it:

- **Image** — Add images; drag and drop and Ctrl+V work too. Up to 8 per request, uploaded to `input/`. They are sent only when the model has vision; otherwise they are left out and the Prompt Writer says so.
- **What the next request does** — Sent to the LLM with your request, so a change request returns the complete updated prompt:
  - **Edit v3 (newest)** (or an older version) — The newest is chosen until you pick another; **Use as input** on a version picks it too.
  - **Edit current prompt** — The text in the prompt box of the **For** node, read when you press **↑**. It becomes a version of the chat first, so you can go back to it even after **Send to ComfyUI** has replaced it in the node: a version that holds exactly that text is simply edited; a tweak of a version (at most 60% of its words changed) is added as a hand edit of it, `in the node`; anything else is added as a new start `from` that node.
  - **New prompt** — A new prompt from your request alone.
  - Editing with an empty box asks the LLM to optimise the prompt.
- **LoRAs** — **Off**, **List** or **All**; the choice is kept and the numbers say how many LoRAs each offers. See LoRA suggestions.
- **↑** — Sends (Ctrl+Enter does too). It is a yellow stop button while the chat writes, and **Stop** ends the request at the provider too. Text you have not sent stays in its chat until ComfyUI's page is reloaded.

## While it writes

The card shows what the model is doing (waiting, thinking or writing), the seconds, and an estimate of the tokens it has answered so far; the exact count replaces it when the version is done. Models that send their thinking show it under **Thinking**, folded; open it to watch it come, scrolled to the newest line. The prompt appears as it is written, and a finished version keeps the thinking, folded, under its request. Whether a model sends its thinking depends on the model: the `:thinking` variants in NanoGPT's list do.

## Questions

Your system prompt may tell the LLM to ask instead of writing, for instance when a request contradicts itself. The Prompt Writer tells it to start such a reply with `===QUESTION===`, and shows it as a yellow **Question** card instead of a version, with nothing to send. **↑** then answers: type your answer and the LLM gets the same prompt to edit, your request, its question and your answer. It may ask again. Once it writes, the card becomes the next version and keeps the questions and answers. The undo arrow on the question card takes your request back into the input instead.

## LoRA suggestions

With **LoRAs** on **List** or **All**, each **Write** tells the LLM about your LoRAs:

- **Available, with All** — The LoRAs in your library made for the model wired into the **For** node, minus the ones already in use. Only LoRAs whose base model is known to match are listed, up to 1000.
- **Available, with List** — Only the LoRAs switched off with `//` in that node's LoRA list, and in the model 2 list while it is switched on.
- **Active** — The LoRAs in that node's prompt and LoRA list, and in the model 2 list while it is switched on, leaving out lines switched off with `//`. They count as your choice: the LLM writes the prompt to work with them, uses their trigger words where they fit, and leaves out the trigger words of anything else.

With **Off**, the LLM hears nothing about LoRAs and suggests none.

Under each version, the folded **LoRAs** line says how many LoRAs were active and how many the AI suggests; it starts closed, and stays open while the pop-up is open once you open it. Inside, **Active LoRAs** shows what was on when it was written, with its strength and a check while it is still on. Press the check to comment its line out and **+** to switch it on again; the version's text stays as it is. A LoRA written in the prompt text shows a check but is not switched from here.

The LLM may answer with LoRAs that would clearly help the image: with LoRAs active at most two, and only for something they do not already cover; with none active up to five. They appear under the version as **Suggested LoRAs**, each with its preview, a strength and a short reason. **Nothing is added on its own.** Press **+** on a suggestion to:

- switch on its commented line if the LoRA is already in the list, keeping the weight written there, or otherwise add `<lora:name:0.80>` as a new last line of the LoRA list of the **For** node, as one undo step;
- put the LoRA's trigger words, if it has any, as a new first line of that version.

The chip then shows a check. Press it again to undo that: a line that was added goes, a line that was already there is commented out again, and the trigger line is removed if that first line is unchanged. The check follows the list: it is off again if you delete or comment out the tag yourself, and it reflects whichever node **For** is on.

## Setup

- **System prompt folder** — Settings → WarpPipe → Prompt Writer. Only `.txt` files directly in that folder are listed.
- **API key** — Paste it once in the settings and click **Save**. The backend stores it in `ComfyUI/user/warppipe_llm_keys.json` for the host of the base URL, it survives restarts, and the browser can never read it back; the field only shows whether a key is set. Alternatively set the environment variable `NANOGPT_API_KEY`. For other servers you can also put `{"host": "key"}` entries in `ComfyUI/user/warppipe_llm_keys.json`. A key is sent only to its own host. Local servers get no key unless the file names them.

The key is never stored in a workflow, in image metadata or in the browser. WarpPipe never writes, logs or returns it.

## Where chats are stored

Chats are stored in the ComfyUI user folder, in `warppipe_prompt_chats.json` (`ComfyUI/user/default/` on a single-user install). They are never stored in a workflow or in images. The base URL, model, system prompt and temperature are ComfyUI settings. Each chat also keeps the text of the system prompts its versions were written with, once per text however many versions share it, and drops one when no version uses it any more.

## Request log

Every request is also written to `warppipe_llm_log.jsonl` in the ComfyUI user folder (`ComfyUI/user/` itself), one JSON object per line, on this machine only:

- **When it is sent** — `"event": "request"` with the time, an `id`, the chat, the server's host, model, system prompt name, temperature, thinking level, your request (`idea`), the prompt it edits (`previous_result`), the image names, the questions and answers so far, and the LoRA choice with how many LoRAs were offered and active. It is written at once, so it is there even if ComfyUI stops before the answer.
- **When it ends** — `"event": "result"` with the same `id` and the outcome: `text` (with the thinking and the LoRA suggestions), `question`, `error` or `stopped`, plus tokens, cost and seconds. The system prompt's text goes back to the page for **Written with**, but the log keeps only its name.

The log never holds the API key or the images themselves, and a key in an error message is replaced by `***`. When the file passes about 20 MB it is renamed to `warppipe_llm_log.jsonl.1`, replacing an older one, and a new log starts. If the log cannot be written, the request still goes ahead and ComfyUI's console says why. The log holds your requests and prompts in plain text: delete it whenever you like.
