#!/usr/bin/env python3
"""Fail CI if the Website silently re-enables Next's image optimizer.

The current Next 16 dependency tree has an accepted sharp/libvips advisory.
The only ``next/image`` call is deliberately unoptimized. Keep that mitigation
true until a forward dependency fix exists; any expansion requires an explicit
security review and an update to this allowlist.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SOURCE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
SKIP_DIRS = {".next", "node_modules", "public"}
ALLOWED_NEXT_IMAGE_FILES = {Path("components/profile-avatar.tsx")}
NEXT_IMAGE_IMPORT = re.compile(
    r"(?:from\s*['\"]next/image['\"]|require\(\s*['\"]next/image['\"]\s*\)|"
    r"import\(\s*['\"]next/image['\"]\s*\))"
)
IMAGE_TAG = re.compile(r"<Image\b[^>]*>", re.DOTALL)
UNOPTIMIZED_PROP = re.compile(r"\bunoptimized(?:\s|=|/|>)")


def policy_violations(website_root: Path) -> list[str]:
    root = website_root.resolve()
    violations: list[str] = []
    found_allowed: set[Path] = set()

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        text = path.read_text(encoding="utf-8")

        if "/_next/image" in text:
            violations.append(f"{relative}: calls the Next image optimizer endpoint")

        if not NEXT_IMAGE_IMPORT.search(text):
            continue
        if relative not in ALLOWED_NEXT_IMAGE_FILES:
            violations.append(f"{relative}: next/image is not security-reviewed")
            continue

        found_allowed.add(relative)
        tags = IMAGE_TAG.findall(text)
        if not tags:
            violations.append(f"{relative}: imports next/image without a reviewable <Image> tag")
            continue
        for tag in tags:
            if not UNOPTIMIZED_PROP.search(tag):
                violations.append(f"{relative}: <Image> must keep the unoptimized prop")

    for expected in sorted(ALLOWED_NEXT_IMAGE_FILES - found_allowed):
        violations.append(f"{expected}: reviewed next/image usage is missing or no longer detected")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "website_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "Website",
    )
    args = parser.parse_args()
    violations = policy_violations(args.website_root)
    if violations:
        print("Next image optimizer policy failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Next image optimizer policy passed: all reviewed Image usages are unoptimized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
