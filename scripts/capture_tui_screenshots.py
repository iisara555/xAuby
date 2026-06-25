#!/usr/bin/env python3
"""Export Textual TUI screens to docs/screenshots/ (SVG via Textual save_screenshot)."""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
os.environ.pop("NO_COLOR", None)
os.environ.setdefault("COLORTERM", "truecolor")


async def _capture(
    out_dir: Path,
    width: int,
    height: int,
) -> list[Path]:
    from xauby.ui.textual_tui.app import XAubyTextualApp

    screens = [
        ("dashboard", "dashboard-wide"),
        ("tradelog", "tradelog"),
        ("incidents", "incidents"),
        ("menu", "menu"),
        ("quick_config", "quick-config"),
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(out_dir.glob("*.svg"))
    app = XAubyTextualApp()
    async with app.run_test(size=(width, height)) as pilot:
        for screen_id, _name in screens:
            await app.push_screen(screen_id)
            await pilot.pause(0.4)
            app.save_screenshot(path=str(out_dir))
    after = sorted(
        (set(out_dir.glob("*.svg")) - before),
        key=lambda p: p.stat().st_mtime,
    )
    written: list[Path] = []
    for (_, stable_name), src in zip(screens, after[-len(screens) :]):
        dest = out_dir / f"{stable_name}.svg"
        shutil.copy2(src, dest)
        written.append(dest)
        try:
            src.unlink()
        except OSError:
            pass
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "docs" / "screenshots",
    )
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--height", type=int, default=36)
    args = parser.parse_args()
    paths = asyncio.run(_capture(args.out, args.width, args.height))
    for p in paths:
        print(f"  wrote {p.relative_to(PROJECT_ROOT)} ({p.stat().st_size} bytes)")
    print(f"Done — {len(paths)} screenshots in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
