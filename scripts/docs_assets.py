"""字体、绘图素材和预构建帮助图片管理入口；--help 不初始化配置。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nonebot

from src.lib.docs_assets import (
    DocsAssetsError,
    check_assets,
    prune_remote_assets,
    publish_assets,
    pull_assets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage fonts and prebuilt image assets"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    pull = actions.add_parser(
        "pull", help="fetch the asset revision locked by this checkout"
    )
    pull.add_argument(
        "--force",
        action="store_true",
        help="download again even if local files are valid",
    )
    actions.add_parser("status", help="verify local assets without network access")
    actions.add_parser(
        "publish", help="build, upload, verify, then update the asset lock"
    )
    prune = actions.add_parser(
        "prune", help="preview remote packages outside the keep list"
    )
    prune.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="SHA256",
        help="protect a deployed or rollback revision (repeatable)",
    )
    prune.add_argument(
        "--apply",
        action="store_true",
        help="delete previewed packages; requires two rollback revisions",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    match args.action:
        case "status":
            status = check_assets(ROOT)
            sys.stdout.write(json.dumps(asdict(status), indent=2) + "\n")
            return 0 if status.ready else 1
        case "pull":
            asyncio.run(pull_assets(ROOT, force=args.force))
        case "publish":
            from scripts.build_docs import build

            if result := build():
                return result
            asyncio.run(publish_assets(ROOT))
        case "prune":
            keys = asyncio.run(
                prune_remote_assets(ROOT, keep=args.keep, apply=args.apply)
            )
            sys.stdout.write(
                json.dumps(
                    {"deleted" if args.apply else "would_delete": keys}, indent=2
                )
                + "\n"
            )
        case _:
            raise DocsAssetsError("unknown action")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    nonebot.init()
    try:
        return _run(args)
    except (DocsAssetsError, OSError) as exc:
        sys.stderr.write(f"docs-assets: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
