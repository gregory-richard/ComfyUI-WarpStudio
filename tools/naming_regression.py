"""Run the naming prompt over the regression set on a live ComfyUI and score it.

usage: python tools/naming_regression.py --clip qwen3.5_9b_int8_convrot.safetensors
           [--url http://127.0.0.1:8188] [--data .civitai_updater/naming_regression.json]
           [--case 3 --case 10] [--json-out results.json] [--wait 0]

Each case is one Civitai model as the renamer would send it (creator, title, raw
description HTML, the version slots) and the expected answer. The data file is
built from the user's own library, so it lives in the git-ignored
``.civitai_updater`` folder and is never committed.

Data format::

    {"cases": [{"case": 1, "input": {ModelInput fields, slots as dicts},
                "expect": {"title": "...", "original": "...", "versions": {"1": "..."}}}]}

An expected value may be a list of accepted answers. A case passes when the
answer parses, every part matches (after the renamer's own formatting:
lowercase, collapsed spaces) and the invented-content check finds nothing.
The answer is the model's first one; no retry. An original maker must appear in
the description's credit lines (the text the model saw), which is stricter than
the renamer's check against the whole description. Needs a live ComfyUI, so it
is not part of the test suite. It queues jobs only while ComfyUI's queue is empty.

Exit codes: 0 every case passed, 1 some case failed, 2 ComfyUI unreachable or
the model failed (BLOCKED), 3 the queue was busy and nothing was queued (BUSY),
4 bad data or case selection (REFUSED).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_updater.comfy_queue import ComfyTextGenerator, NamingModelError  # noqa: E402
from comfy_updater.naming_ai import (  # noqa: E402
    ModelInput, PROMPT_VERSION, SYSTEM_PROMPT, Slot, build_message, credit_lines, parse_answer,
)
from comfy_updater.naming_check import invented_words, maker_in_description  # noqa: E402
from comfy_updater.naming_scheme import format_part  # noqa: E402

DEFAULT_DATA = ROOT / ".civitai_updater" / "naming_regression.json"

EXIT_PASS = 0
EXIT_FAIL = 1  # some case did not pass
EXIT_BLOCKED = 2  # ComfyUI unreachable or the model failed
EXIT_BUSY = 3  # the user's queue was busy, nothing was queued
EXIT_BAD_INPUT = 4  # bad data or case selection, nothing was queued


class BadData(ValueError):
    """The data file or the case selection cannot be scored."""


class QueueBusy(RuntimeError):
    """ComfyUI's queue had the user's jobs in it."""


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def check_case(case: dict) -> None:
    """Refuse a case still marked todo, or whose expected versions don't name exactly its slots."""
    if case.get("todo"):
        raise BadData(f"case {case.get('case')} is marked todo: fill in its expectations first")
    slot_keys = {str(slot["key"]) for slot in case["input"]["slots"]}
    expect_keys = set((case["expect"].get("versions") or {}).keys())
    if slot_keys != expect_keys:
        raise BadData(f"case {case.get('case')}: expect.versions keys {sorted(expect_keys)} "
                      f"do not match slot keys {sorted(slot_keys)}")


def select_cases(cases: list[dict], wanted: list[int] | None) -> list[dict]:
    if wanted:
        missing = sorted(set(wanted) - {c["case"] for c in cases})
        if missing:
            raise BadData(f"no such case: {', '.join(map(str, missing))}")
        cases = [c for c in cases if c["case"] in set(wanted)]
    if not cases:
        raise BadData("no cases to run")
    for case in cases:
        check_case(case)
    return cases


def model_input(case: dict) -> ModelInput:
    raw = case["input"]
    slots = [Slot(**slot) for slot in raw["slots"]]
    return ModelInput(
        model_id=raw["model_id"], creator=raw["creator"], title=raw["title"],
        description=raw["description"], slots=slots, new_bases=list(raw.get("new_bases") or []),
    )


def _accepted(value) -> list[str]:
    return [format_part(v) for v in (value if isinstance(value, list) else [value])]


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _same_maker(got: str, wanted: str) -> bool:
    """Same maker on letters and digits; junk like "-" is not an empty answer."""
    if not wanted.strip():
        return not got.strip()
    return _compact(got) == _compact(wanted)


def judge(case: dict, mi: ModelInput, text: str) -> dict:
    """Score one answer: the parts that differ and the invented words."""
    answer = parse_answer(text, mi)
    expect = case["expect"]
    diffs = []
    got = {"title": format_part(answer.title), "original": answer.original.strip(), "versions": {}}
    if answer.unparsed:
        diffs.append("unparsed answer")
    if got["title"] not in _accepted(expect["title"]):
        diffs.append(f"title: expected {expect['title']!r}, got {got['title']!r}")
    originals = expect.get("original", "")
    originals = originals if isinstance(originals, list) else [originals]
    if not any(_same_maker(got["original"], o) for o in originals):
        diffs.append(f"original: expected {expect.get('original', '')!r}, got {got['original']!r}")
    invented = []
    for slot in mi.slots:
        version = format_part(answer.versions.get(slot.key, ""))
        got["versions"][slot.key] = version
        wanted = expect["versions"].get(slot.key, "")
        if version not in _accepted(wanted):
            diffs.append(f"version {slot.key}: expected {wanted!r}, got {version!r}")
        source = " ".join([mi.title, slot.version_name, slot.file_name])
        invented += invented_words(source, [got["title"], version])
    invented = list(dict.fromkeys(invented))
    # The maker must be in the text the model saw (the credit lines), which is
    # stricter than the renamer's own check (the whole description).
    if got["original"] and not maker_in_description(got["original"], credit_lines(mi.description)):
        invented.append(f"original {got['original']!r} not in the credit lines")
    return {"case": case["case"], "ok": not diffs and not invented, "diffs": diffs,
            "invented": invented, "got": got, "raw": text}


def queue_idle(url: str) -> bool:
    with urllib.request.urlopen(url.rstrip("/") + "/queue", timeout=10) as response:
        queue = json.loads(response.read())
    return not queue.get("queue_running") and not queue.get("queue_pending")


def wait_for_idle(url: str, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    while not queue_idle(url):
        if time.monotonic() >= deadline:
            raise QueueBusy("ComfyUI's queue is busy; not queueing (use --wait to wait for it).")
        time.sleep(5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--clip", required=True, help="text-encoder file name of the naming model")
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--case", type=int, action="append", help="run only these case numbers")
    parser.add_argument("--json-out", type=Path, help="also write every answer and score here")
    parser.add_argument("--wait", type=float, default=0, help="seconds to wait for a busy queue (default: refuse)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        cases = select_cases(load_cases(args.data), args.case)
    except (BadData, OSError, KeyError, TypeError, ValueError) as exc:  # OSError: no data file
        print(f"REFUSED: {exc}")
        return EXIT_BAD_INPUT
    inputs = [model_input(c) for c in cases]
    messages = [build_message(mi) for mi in inputs]
    generator = ComfyTextGenerator(args.url, args.clip, SYSTEM_PROMPT, poll_seconds=0.5)

    try:
        wait_for_idle(args.url, args.wait)
        generator.generate(messages[:1])  # warm-up: loads the model so the timing below is generation only
        wait_for_idle(args.url, args.wait)
        start = time.monotonic()
        texts = generator.generate(messages)
        seconds = time.monotonic() - start
    except QueueBusy as exc:
        print(f"BUSY: {exc}")
        return EXIT_BUSY
    except (NamingModelError, OSError) as exc:  # OSError: /queue unreachable
        print(f"BLOCKED: {exc}")
        return EXIT_BLOCKED

    results = [judge(c, mi, t) for c, mi, t in zip(cases, inputs, texts)]
    for r in results:
        if r["ok"]:
            print(f"ok   #{r['case']}")
            continue
        print(f"diff #{r['case']}")
        for line in r["diffs"]:
            print(f"       {line}")
        if r["invented"]:
            print(f"       invented: {', '.join(r['invented'])}")
    passed = sum(r["ok"] for r in results)
    flagged = sum(bool(r["invented"]) for r in results)
    per_model = seconds / len(results) if results else 0.0
    print(f"\nprompt {PROMPT_VERSION}: {passed}/{len(results)} pass, {flagged} with invented content, "
          f"{seconds:.1f} s ({per_model:.2f} s per model)")
    if args.json_out:
        args.json_out.write_text(json.dumps({
            "prompt_version": PROMPT_VERSION, "passed": passed, "total": len(results),
            "invented": flagged, "seconds": seconds, "results": results,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return EXIT_PASS if passed == len(results) else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
