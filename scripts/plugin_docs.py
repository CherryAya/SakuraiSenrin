"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    PluginDocBundle,
    load_plugin_doc_bundle,
    render_demo_png,
)

DOCS_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)


def iter_readmes() -> list[Path]:
    readmes: list[Path] = []
    for root in DOCS_ROOTS:
        readmes.extend(sorted(root.glob("**/README.MD")))
    return [path for path in readmes if "/docs/" in path.as_posix()]


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def load_bundle(path: Path) -> PluginDocBundle:
    return load_plugin_doc_bundle(
        source=path,
        default_name=path.parent.name,
        default_description="",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


def generate() -> int:
    total_files = 0
    total_images = 0
    for path in iter_readmes():
        bundle = load_bundle(path)
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        total_files += 1
        for feature in bundle.index:
            if not feature.demo_turns or not feature.demo_filename:
                continue
            output = demos_dir / feature.demo_filename
            output.write_bytes(render_demo_png(bundle, feature))
            total_images += 1
            _write_line(f"generated {output.relative_to(ROOT)}")
    _write_line(
        f"processed {total_files} README files, generated {total_images} demo images"
    )
    return 0


def validate() -> int:
    errors: list[str] = []
    for path in iter_readmes():
        try:
            bundle = load_bundle(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

        if not bundle.summary.strip():
            errors.append(f"{path.relative_to(ROOT)}: missing 概览 section content")
        if not bundle.index:
            errors.append(f"{path.relative_to(ROOT)}: missing feature entries")

        for feature in bundle.index:
            if not feature.overview.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 说明 section"
                )
            if not feature.preconditions.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 前置条件 section"
                )
            if not feature.failures.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 失败情况 section"
                )
            if not feature.demo_turns:
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing demo turns"
                )
            demo_path = path.parent / "demos" / feature.demo_filename
            if not demo_path.exists():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    f"missing demo file {feature.demo_filename}"
                )

    if errors:
        _write_line("\n".join(errors))
        return 1

    _write_line(f"validated {len(iter_readmes())} README files")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plugin docs helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("generate", help="generate demo PNG assets from README specs")
    subparsers.add_parser("validate", help="validate README structure and demo assets")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    match args.action:
        case "generate":
            return generate()
        case "validate":
            return validate()
        case _:
            parser.error("unknown action")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
