#!/usr/bin/env python3
"""Conservatively add Django i18n markers to static template text.

This helper is meant for the source-language templates after editing pages.
Run it before `makemessages` so new simple text nodes are wrapped in `{% trans %}`.

It intentionally skips scripts, styles, existing translation tags, and complex
cases that should be translated manually with `{% blocktrans %}`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TAG_RE = re.compile(r"(<[^>]+>)", re.DOTALL)
TAG_NAME_RE = re.compile(r"^</?\s*([a-zA-Z0-9:-]+)")
ATTR_RE_TEMPLATE = r'({attr}\s*=\s*)"([^"]*)"'
TRANSLATABLE_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")
SKIP_TAGS = {"script", "style"}
SKIP_TEXTS = {
    "BrainBoost",
    "BigBlueButton",
    "ZUMPad",
    "Google Maps",
}
SKIP_CLASS_RE = re.compile(r'class\s*=\s*"[^"]*\bbrainboost-wordmark\b[^"]*"')


def django_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def django_attr_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def is_template_tag(value: str) -> bool:
    return "{%" in value or "{{" in value or "{#" in value


def should_translate_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped in SKIP_TEXTS:
        return False
    if is_template_tag(stripped):
        return False
    if not TRANSLATABLE_RE.search(stripped):
        return False
    return True


def mark_text_node(text: str) -> str:
    if not should_translate_text(text):
        return text
    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    stripped = text.strip()
    if "\n" in stripped or len(stripped) > 120:
        marked = "{% blocktrans trimmed %}" + stripped + "{% endblocktrans %}"
    else:
        marked = "{% trans " + django_quote(stripped) + " %}"
    return leading + marked + trailing


def tag_name(tag: str) -> str:
    match = TAG_NAME_RE.match(tag)
    return match.group(1).lower() if match else ""


def is_closing_tag(tag: str) -> bool:
    return tag.startswith("</")


def is_self_closing_tag(tag: str) -> bool:
    return tag.rstrip().endswith("/>")


def mark_attrs(tag: str, attrs: tuple[str, ...]) -> str:
    updated = tag
    for attr in attrs:
        pattern = re.compile(ATTR_RE_TEMPLATE.format(attr=re.escape(attr)))

        def replace(match: re.Match[str]) -> str:
            prefix, value = match.groups()
            stripped = value.strip()
            if not should_translate_text(stripped):
                return match.group(0)
            return f'{prefix}"{{% trans {django_attr_quote(stripped)} %}}"'

        updated = pattern.sub(replace, updated)
    return updated


def mark_template(content: str, attrs: tuple[str, ...]) -> str:
    parts = TAG_RE.split(content)
    output: list[str] = []
    skip_tag_stack: list[str] = []
    blocktrans_depth = 0
    class_skip_depth = 0

    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            name = tag_name(part)
            closing = is_closing_tag(part)

            if closing:
                if skip_tag_stack and skip_tag_stack[-1] == name:
                    skip_tag_stack.pop()
                if class_skip_depth:
                    class_skip_depth -= 1
                output.append(part)
                continue

            marked_tag = mark_attrs(part, attrs)
            output.append(marked_tag)
            if name in SKIP_TAGS and not is_self_closing_tag(part):
                skip_tag_stack.append(name)
            if class_skip_depth and not is_self_closing_tag(part):
                class_skip_depth += 1
            elif SKIP_CLASS_RE.search(part) and not is_self_closing_tag(part):
                class_skip_depth = 1
            continue

        if "{% blocktrans" in part:
            blocktrans_depth += part.count("{% blocktrans")
        if "{% endblocktrans" in part and blocktrans_depth:
            blocktrans_depth -= part.count("{% endblocktrans")

        if skip_tag_stack or class_skip_depth or blocktrans_depth:
            output.append(part)
        else:
            output.append(mark_text_node(part))

    return "".join(output)


def process_path(path: Path, write: bool, attrs: tuple[str, ...]) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = mark_template(original, attrs)
    changed = updated != original
    if changed and write:
        path.write_text(updated, encoding="utf-8")
    return changed


def resolve_input_path(path: Path) -> Path:
    if path.exists():
        return path
    project_path = PROJECT_ROOT / path
    if project_path.exists():
        return project_path
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Django i18n markers to simple static text in templates."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write changes. Without this flag the command only reports files that would change.",
    )
    parser.add_argument(
        "--attrs",
        default="aria-label,title,alt,placeholder",
        help="Comma-separated HTML attributes to mark. Default: aria-label,title,alt,placeholder.",
    )
    args = parser.parse_args()
    attrs = tuple(attr.strip() for attr in args.attrs.split(",") if attr.strip())

    changed_paths: list[Path] = []
    for path in args.paths:
        path = resolve_input_path(path)
        if path.is_dir():
            candidates = sorted(path.rglob("*.html"))
        else:
            candidates = [path]
        for candidate in candidates:
            if process_path(candidate, args.write, attrs):
                changed_paths.append(candidate)

    if not changed_paths:
        print("No i18n marker changes needed.")
        return 0

    action = "Updated" if args.write else "Would update"
    for path in changed_paths:
        print(f"{action}: {path}")
    if not args.write:
        print("Run again with --write to apply these marker changes.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
