#!/usr/bin/env python3
"""Validate the in-repo agent plugin: manifests, catalogs, hooks, rules, skills.

Run from the repository root:

    python3 scripts/check_plugin.py            # all checks
    python3 scripts/check_plugin.py urls size  # named checks only

Standard library only. Exit status 1 when any check fails.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

PLUGIN = "marketplace/plugins/memchentic"
REPO_URL = "https://github.com/memchentic/memchentic"
MANIFESTS = {
    "claude": f"{PLUGIN}/.claude-plugin/plugin.json",
    "codex": f"{PLUGIN}/.codex-plugin/plugin.json",
    "cursor": f"{PLUGIN}/.cursor-plugin/plugin.json",
}
CATALOGS = [
    ".cursor-plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".github/plugin/marketplace.json",
    ".agents/plugins/marketplace.json",
]
ENTITIES = "application/api/src/memchentic/models/entities.py"
CAPTURE_RULE = f"{PLUGIN}/rules/memchentic-capture.mdc"
CAPTURE_SKILL = f"{PLUGIN}/skills/memchentic-capture/SKILL.md"
HOOK_SCRIPT = f"{PLUGIN}/hooks/session-start.sh"
COPILOT_HOOKS = f"{PLUGIN}/hooks/hooks.json"
ALWAYS_ON_BUDGET_BYTES = 8400
HOOK_CONTEXT_MAX_CHARS = 4000

Files = dict[str, str]


def load(root: Path) -> Files:
    """Read every plugin file, catalog and the entry-type enum into memory."""
    paths = [p for p in (root / "marketplace").rglob("*") if p.is_file()]
    paths += [root / name for name in (*CATALOGS, ENTITIES)]
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in paths
        if p.is_file()
    }


def exists(files: Files, path: str) -> bool:
    """True when ``path`` is a loaded file or a directory that holds one."""
    path = posixpath.normpath(path)
    return path in files or any(name.startswith(path + "/") for name in files)


def parse(files: Files, path: str, errors: list[str]) -> dict:
    """Return the JSON object in ``path``; record an error and return {} if bad."""
    if path not in files:
        errors.append(f"{path}: file is missing")
        return {}
    try:
        data = json.loads(files[path])
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON ({exc})")
        return {}
    if not isinstance(data, dict):
        errors.append(f"{path}: top level is not an object")
        return {}
    return data


def catalog_entries(files: Files, errors: list[str]) -> list[tuple[str, dict, dict]]:
    """Return (catalog path, memchentic plugin entry, whole catalog) triples."""
    found = []
    for path in CATALOGS:
        catalog = parse(files, path, errors)
        entries = [e for e in catalog.get("plugins", []) if e.get("name") == "memchentic"]
        if len(entries) != 1:
            errors.append(f"{path}: expected one plugin entry named memchentic")
        found += [(path, entry, catalog) for entry in entries]
    return found


def hook_json_files(files: Files) -> list[str]:
    """Return the paths of the JSON files under the plugin's hooks directory."""
    prefix = f"{PLUGIN}/hooks/"
    return sorted(p for p in files if p.startswith(prefix) and p.endswith(".json"))


def section(text: str, heading: str) -> str | None:
    """Return the body under the level-2 heading that starts with ``heading``."""
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return match.group(1).strip() if match else None


def check_json(files: Files) -> list[str]:
    """Every manifest, catalog and hook file parses as a JSON object."""
    errors: list[str] = []
    for path in (*MANIFESTS.values(), *CATALOGS, *hook_json_files(files)):
        parse(files, path, errors)
    return errors


def check_versions(files: Files) -> list[str]:
    """All seven files carry the same MAJOR.MINOR.PATCH plugin version."""
    errors: list[str] = []
    seen = {path: parse(files, path, errors).get("version") for path in MANIFESTS.values()}
    seen.update({path: entry.get("version") for path, entry, _ in catalog_entries(files, errors)})
    for path, version in seen.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(version)):
            errors.append(f"{path}: plugin version {version!r} is not MAJOR.MINOR.PATCH")
    if len(set(seen.values())) > 1:
        errors.append(f"plugin versions differ: {seen}")
    return errors


def check_urls(files: Files) -> list[str]:
    """Every homepage, repository and websiteURL equals ``REPO_URL``."""
    errors: list[str] = []
    found: list[tuple[str, str, object]] = []
    for path in MANIFESTS.values():
        data = parse(files, path, errors)
        found += [(path, key, data.get(key)) for key in ("homepage", "repository")]
        if "interface" in data:
            found.append((path, "interface.websiteURL", data["interface"].get("websiteURL")))
    for path, entry, _ in catalog_entries(files, errors):
        found += [(path, key, entry[key]) for key in ("homepage", "repository") if key in entry]
    for path, key, value in found:
        if value != REPO_URL:
            errors.append(f"{path}: {key} is {value!r}, expected {REPO_URL!r}")
    return errors


def check_paths(files: Files) -> list[str]:
    """Manifest path keys and catalog sources resolve to existing plugin paths."""
    errors: list[str] = []
    for path in MANIFESTS.values():
        data = parse(files, path, errors)
        keys = ("rules", "skills", "commands", "hooks", "logo")
        refs = {key: data[key] for key in keys if key in data}
        if "logo" in data.get("interface", {}):
            refs["interface.logo"] = data["interface"]["logo"]
        for key, ref in refs.items():
            if not exists(files, posixpath.join(PLUGIN, ref)):
                errors.append(f"{path}: {key} -> {ref!r} does not exist")
    for path, entry, catalog in catalog_entries(files, errors):
        source = entry.get("source")
        ref = source.get("path", "") if isinstance(source, dict) else str(source)
        root = catalog.get("metadata", {}).get("pluginRoot", "")
        # "./x" is read from the repository root; a bare name is joined to pluginRoot.
        target = ref if ref.startswith("./") or not root else posixpath.join(root, ref)
        resolved = posixpath.normpath(target)
        if resolved != PLUGIN:
            errors.append(f"{path}: source {ref!r} resolves to {resolved!r}, expected {PLUGIN!r}")
    return errors


def check_hooks(files: Files) -> list[str]:
    """Each host has its own hook file with only its own event schema."""
    errors: list[str] = []
    for host in ("claude", "codex"):
        ref = parse(files, MANIFESTS[host], errors).get("hooks", "")
        path = posixpath.normpath(posixpath.join(PLUGIN, ref))
        if not ref or path == COPILOT_HOOKS:
            errors.append(f"{MANIFESTS[host]}: hooks must name this host's own file, got {ref!r}")
            continue
        data = parse(files, path, errors)
        events = sorted(data.get("hooks", {}))
        if sorted(data) != ["hooks"] or events != ["SessionStart"]:
            errors.append(
                f"{path}: expected only hooks.SessionStart, got keys {sorted(data)} events {events}"
            )
    copilot = parse(files, COPILOT_HOOKS, errors)
    if copilot.get("version") != 1 or sorted(copilot.get("hooks", {})) != ["sessionStart"]:
        errors.append(f"{COPILOT_HOOKS}: expected version 1 and only hooks.sessionStart")
    for path in hook_json_files(files):
        if "/hooks/session-start.sh" not in files[path]:
            errors.append(f"{path}: does not run hooks/session-start.sh")
    return errors


def check_refs(files: Files) -> list[str]:
    """Rule and skill names in the texts resolve on the hosts that read them."""
    errors: list[str] = []
    rules = {
        posixpath.basename(p)[:-4]
        for p in files
        if p.startswith(f"{PLUGIN}/rules/") and p.endswith(".mdc")
    }
    skills = {
        p.split("/")[-2]
        for p in files
        if p.startswith(f"{PLUGIN}/skills/") and p.endswith("/SKILL.md")
    }
    for path, text in sorted(files.items()):
        if not path.startswith(f"{PLUGIN}/") or not path.endswith((".md", ".mdc")):
            continue
        # Skills and commands load on hosts that do not load rules.
        portable = "/skills/" in path or "/commands/" in path
        for kind, name in re.findall(r"\b(rule|skill)\s+`(memchentic[a-z-]*)`", text):
            if kind == "rule" and portable:
                errors.append(f"{path}: points at rule `{name}`, but rules load only in Cursor")
            elif kind == "rule" and name not in rules:
                errors.append(f"{path}: rule `{name}` does not exist")
            elif kind == "skill" and name not in skills:
                errors.append(f"{path}: skill `{name}` does not exist")
    for name in sorted(set(re.findall(r"memchentic-[a-z]+", files.get(HOOK_SCRIPT, "")))):
        if name not in skills:
            errors.append(f"{HOOK_SCRIPT}: names `{name}`, which is not a skill")
    return errors


def check_types(files: Files) -> list[str]:
    """The entry-type table has one home and matches the ``EntryType`` enum."""
    errors: list[str] = []
    block = re.search(r'class EntryType\(.*?\):\n(?:    """.*?"""\n\n)?((?:    \w+ = "\w+"\n)+)', files.get(ENTITIES, ""))
    enum = set(re.findall(r'= "(\w+)"', block.group(1))) if block else set()
    if not enum:
        return [f"{ENTITIES}: EntryType enum not found"]
    types_section = section(files.get(CAPTURE_SKILL, ""), "Entry types") or ""
    table = set(re.findall(r"^\| `(\w+)` \|", types_section, re.M))
    if table != enum:
        errors.append(
            f"{CAPTURE_SKILL}: entry-type table {sorted(table)} "
            f"differs from EntryType {sorted(enum)}"
        )
    for path in (CAPTURE_RULE, HOOK_SCRIPT):
        absent = sorted(v for v in enum if not re.search(rf"\b{v}\b", files.get(path, "")))
        if absent:
            errors.append(f"{path}: does not name entry type(s) {absent}")
    homes = sorted(
        p
        for p, text in files.items()
        if p.startswith(f"{PLUGIN}/") and "| Type | Use when |" in text
    )
    if homes != [CAPTURE_SKILL]:
        errors.append(f"the entry-type table must exist only in {CAPTURE_SKILL}; found in {homes}")
    return errors


def check_sync(files: Files) -> list[str]:
    """The Refuse section is identical in the capture rule and capture skill."""
    rule = section(files.get(CAPTURE_RULE, ""), "Refuse")
    skill = section(files.get(CAPTURE_SKILL, ""), "Refuse")
    if rule is None or skill is None:
        return ["Refuse section missing from the capture rule or the capture skill"]
    if rule != skill:
        return [f"Refuse section differs between {CAPTURE_RULE} and {CAPTURE_SKILL}"]
    return []


def check_size(files: Files) -> list[str]:
    """The always-applied rules stay within ``ALWAYS_ON_BUDGET_BYTES``."""
    sizes = {
        path: len(text.encode("utf-8"))
        for path, text in sorted(files.items())
        if path.startswith(f"{PLUGIN}/rules/") and re.search(r"^alwaysApply: true$", text, re.M)
    }
    total = sum(sizes.values())
    if total > ALWAYS_ON_BUDGET_BYTES:
        budget = ALWAYS_ON_BUDGET_BYTES
        return [f"always-applied rules total {total} bytes (budget {budget}): {sizes}"]
    return []


def check_script(files: Files) -> list[str]:
    """The hook script prints valid JSON in both output shapes."""
    errors: list[str] = []
    text = files.get(HOOK_SCRIPT)
    if text is None:
        return [f"{HOOK_SCRIPT}: file is missing"]
    quoted = re.search(r"^CONTEXT='(.*?)'$", text, re.S | re.M)
    if quoted is None or "'" in quoted.group(1):
        errors.append(
            f"{HOOK_SCRIPT}: CONTEXT must be one single-quoted string without apostrophes"
        )
    env = {k: v for k, v in os.environ.items() if k != "COPILOT_CLI"}
    for args, key in (([], "hookSpecificOutput"), (["copilot"], "additionalContext")):
        run = subprocess.run(
            ["bash", "-s", "--", *args],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )
        label = f"{HOOK_SCRIPT} {' '.join(args)}".strip()
        try:
            out = json.loads(run.stdout)
        except json.JSONDecodeError:
            detail = run.stderr.strip()[:200]
            errors.append(f"{label}: exit {run.returncode}, output is not JSON: {detail}")
            continue
        if list(out) != [key]:
            errors.append(f"{label}: top-level keys {list(out)}, expected [{key!r}]")
        nested = out.get("hookSpecificOutput")
        context = (nested if isinstance(nested, dict) else out).get("additionalContext", "")
        if not 0 < len(context) <= HOOK_CONTEXT_MAX_CHARS:
            limit = HOOK_CONTEXT_MAX_CHARS
            errors.append(f"{label}: context length {len(context)} is not within 1..{limit}")
    return errors


CHECKS: dict[str, Callable[[Files], list[str]]] = {
    "json": check_json,
    "versions": check_versions,
    "urls": check_urls,
    "paths": check_paths,
    "hooks": check_hooks,
    "refs": check_refs,
    "types": check_types,
    "sync": check_sync,
    "size": check_size,
    "script": check_script,
}


def main(argv: list[str]) -> int:
    """Run the named checks (default: all) and return the exit status."""
    names = argv or list(CHECKS)
    unknown = [name for name in names if name not in CHECKS]
    if unknown:
        print(f"unknown check(s) {unknown}; choose from {list(CHECKS)}")
        return 2
    files = load(Path.cwd())
    failed = 0
    for name in names:
        errors = CHECKS[name](files)
        print(f"{name}: {'ok' if not errors else f'{len(errors)} error(s)'}")
        for error in errors:
            print(f"  {error}")
        failed += bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
