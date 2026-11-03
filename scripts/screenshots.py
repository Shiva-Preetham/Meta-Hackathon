"""Capture dashboard screenshots for the README with Playwright (headless Chromium).

    python -m signalhq demo            # in another terminal (serves on :8000)
    python scripts/screenshots.py [--url http://127.0.0.1:8000] [--out docs/screenshots]

Set PLAYWRIGHT_CHROMIUM to a Chromium binary if Playwright's own download is not available.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    launch = {"executable_path": os.environ["PLAYWRIGHT_CHROMIUM"]} if os.environ.get("PLAYWRIGHT_CHROMIUM") else {}
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        for name, viewport in (("dashboard-1280", {"width": 1280, "height": 900}),
                               ("dashboard-phone", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=viewport, device_scale_factor=args.scale)
            page.goto(args.url, wait_until="networkidle")
            page.wait_for_selector("#themes-body tr")
            page.wait_for_selector(".why", timeout=10_000)
            page.wait_for_timeout(800)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if overflow > 0:
                print(f"WARNING {name}: page scrolls horizontally by {overflow}px")
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            print(f"saved {out / f'{name}.png'}")
            page.close()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=args.scale)
        page.goto(f"{args.url}/label", wait_until="networkidle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "label-1280.png"), full_page=True)
        print(f"saved {out / 'label-1280.png'}")
        browser.close()


if __name__ == "__main__":
    main()
