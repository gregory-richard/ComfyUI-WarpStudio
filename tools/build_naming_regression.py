"""Rebuild the naming regression data from live Civitai data.

usage: python tools/build_naming_regression.py [--data .civitai_updater/naming_regression.json]
           [--add MODEL_ID:VERSION_ID ...] [--api https://civitai.red/api/v1] [--dry-run]

Refresh (the default): for every case already in the data file, fetch its
model (``GET /models/<model_id>``) and rebuild the case's input: the current
title, uploader, raw description HTML and the case's version as slots. A
version with one main file (types as in ``renamer.MAIN_FILE_TYPES``) is one
slot. A version with several main files gets one slot per file, with its file
name; the renamer does that only when the library holds two or more of those
files in one folder, so when it holds just one, edit the case down to one slot
without a file name by hand (the refresh keeps an edited single slot).
Expectations are kept as they are; every case whose title, version names or
slots changed is printed, so its expectations can be checked by hand. A model
or version Civitai no longer serves keeps its old input, with a warning.

``--add`` appends a case for a model's version. Its expectations are empty and
it is marked ``"todo": true``; the runner refuses it until they are filled in.

The data comes from the user's own library, so the file lives in the
git-ignored ``.civitai_updater`` folder and is never committed. Only model ids
are sent to Civitai, about one request per second.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_updater.renamer import MAIN_FILE_TYPES  # noqa: E402

DEFAULT_DATA = ROOT / ".civitai_updater" / "naming_regression.json"
DEFAULT_API = "https://civitai.red/api/v1"
USER_AGENT = "civitai-updater"
PAUSE_SECONDS = 1.1


class NotServed(Exception):
    """Civitai has no such model (404) or no such version in it."""


def fetch_model(api: str, model_id: str) -> dict:
    request = urllib.request.Request(f"{api.rstrip('/')}/models/{model_id}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotServed(f"model {model_id}: 404") from exc
        raise


def build_input(model: dict, version_id: str, new_bases: list | None = None, per_file: bool = True) -> dict:
    """A case input for one version of a live model; ``per_file=False`` keeps one slot."""
    version = next((v for v in model.get("modelVersions") or [] if str(v.get("id")) == str(version_id)), None)
    if version is None:
        raise NotServed(f"model {model.get('id')}: no version {version_id}")
    files = [f for f in version.get("files") or [] if f.get("type") in MAIN_FILE_TYPES]
    common = {"version_id": str(version["id"]), "version_name": version.get("name") or "",
              "base_model": version.get("baseModel") or ""}
    if per_file and len(files) > 1:
        slots = [dict(common, key=str(i), file_id=str(f.get("id", "")), file_name=f.get("name") or "")
                 for i, f in enumerate(files, start=1)]
    else:
        slots = [dict(common, key="1", file_id=str(files[0].get("id", "")) if files else "", file_name="")]
    return {
        "model_id": str(model.get("id", "")),
        "creator": (model.get("creator") or {}).get("username") or "",
        "title": model.get("name") or "",
        "description": model.get("description") or "",
        "slots": slots,
        "new_bases": list(new_bases or []),
    }


def changes(old: dict, new: dict) -> list[str]:
    """What a reviewer should look at before trusting the old expectations."""
    found = []
    if old.get("title") != new["title"]:
        found.append(f"title {old.get('title')!r} -> {new['title']!r}")
    old_slots = [(s.get("key"), s.get("version_name"), s.get("file_name")) for s in old.get("slots") or []]
    new_slots = [(s["key"], s["version_name"], s["file_name"]) for s in new["slots"]]
    if old_slots != new_slots:
        found.append(f"slots {old_slots} -> {new_slots}")
    return found


def parse_add(value: str) -> tuple[str, str]:
    model_id, sep, version_id = value.partition(":")
    if not (sep and model_id.isdigit() and version_id.isdigit()):
        raise argparse.ArgumentTypeError("expected MODEL_ID:VERSION_ID, both numbers")
    return model_id, version_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--add", type=parse_add, action="append", default=[], metavar="MODEL_ID:VERSION_ID")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.data.exists():
        data = json.loads(args.data.read_text(encoding="utf-8"))
    elif args.add:
        data = {"format": 1, "cases": []}
    else:
        print(f"no data file at {args.data}; use --add to start one")
        return 1
    cases = data.setdefault("cases", [])

    first = True

    def fetch(model_id: str) -> dict:
        nonlocal first
        if not first:
            time.sleep(PAUSE_SECONDS)
        first = False
        return fetch_model(args.api, model_id)

    for case in cases:
        old = case["input"]
        version_id = (old.get("slots") or [{}])[0].get("version_id", "")
        try:
            single = len(old.get("slots") or []) == 1 and not old["slots"][0].get("file_name")
            new = build_input(fetch(old["model_id"]), version_id, old.get("new_bases"), per_file=not single)
        except NotServed as exc:
            print(f"#{case['case']}: kept the old input ({exc})")
            continue
        for line in changes(old, new):
            print(f"#{case['case']}: {line}")
        if [s["key"] for s in new["slots"]] != [s.get("key") for s in old.get("slots") or []]:
            old_versions = case["expect"].get("versions") or {}
            case["expect"]["versions"] = {s["key"]: old_versions.get(s["key"], "") for s in new["slots"]}
            case["todo"] = True
            print(f"#{case['case']}: the slots changed; check its expected versions (marked todo)")
        case["input"] = new

    next_number = max((c["case"] for c in cases), default=0) + 1
    for model_id, version_id in args.add:
        try:
            new = build_input(fetch(model_id), version_id)
        except NotServed as exc:
            print(f"--add {model_id}:{version_id}: skipped ({exc})")
            continue
        cases.append({
            "case": next_number,
            "todo": True,
            "input": new,
            "expect": {"title": "", "original": "", "versions": {s["key"]: "" for s in new["slots"]}},
        })
        print(f"#{next_number}: added; fill in its expectations and remove \"todo\"")
        next_number += 1

    if args.dry_run:
        print("dry run: nothing written")
        return 0
    data["source"] = f"live Civitai data ({args.api}), {time.strftime('%Y-%m-%d')}"
    args.data.parent.mkdir(parents=True, exist_ok=True)
    args.data.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {args.data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
