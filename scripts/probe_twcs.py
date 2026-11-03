"""Sprint 0.3: probe data/raw/twcs.csv, validate its columns and rank brands by volume.

    python scripts/probe_twcs.py [path/to/twcs.csv] [--top 25]

Volume = customer tweets each brand account replied to (the brand's support load). The
default brand is SpotifyCares; if it has fewer than ~5,000, pick the largest software or
subscription brand instead and record why in PROGRESS.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalhq.connectors.twcs import brand_volumes, validate_columns  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=str(ROOT / "data" / "raw" / "twcs.csv"))
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--brand", default="SpotifyCares")
    args = parser.parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"{path} not found. Download twcs.csv from Kaggle (thoughtvector/customer-support-on-twitter).")
        return 2
    validate_columns(path)
    print("columns: ok")
    volumes = brand_volumes(path)
    print(f"{'brand':<20} replied-to customer tweets")
    for brand, n in volumes.most_common(args.top):
        print(f"{brand:<20} {n}")
    chosen = volumes.get(args.brand, 0)
    print(f"\n{args.brand}: {chosen}")
    if chosen < 5000:
        print(f"{args.brand} has fewer than 5,000; choose the largest software or subscription brand above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
