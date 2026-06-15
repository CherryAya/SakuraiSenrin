"""Render a standalone wordbank search treemap fixture into a PNG."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.wordbank_search_treemap import (
    load_search_treemap_fixture,
    render_search_results_treemap_bytes,
)

DEFAULT_FIXTURE = (
    ROOT / "tests" / "plugins" / "wordbank" / "fixtures" / "search_treemap_basic.json"
)
DEFAULT_OUTPUT = ROOT / "output" / "wordbank-search-treemap.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Path to a search treemap fixture JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the output PNG file.",
    )
    parser.add_argument(
        "--locale",
        type=str,
        default="zh-CN",
        help="Locale code used for static labels.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    page = load_search_treemap_fixture(args.fixture)
    image_bytes = render_search_results_treemap_bytes(
        page=page,
        locale=args.locale,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image_bytes)
    sys.stdout.write(f"{args.output}\n")


if __name__ == "__main__":
    main()
