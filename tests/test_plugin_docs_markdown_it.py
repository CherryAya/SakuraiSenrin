from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DemoImageRenderer,
    InlineTextSpan,
    load_plugin_doc_bundle,
    split_inline_text_spans,
)


def test_split_inline_text_spans_preserves_multiple_merge_code_spans() -> None:
    spans = split_inline_text_spans("`#water.merge yes` / `#water.merge no`")

    assert [(span.text, span.code) for span in spans] == [
        ("#water.merge yes", True),
        (" / ", False),
        ("#water.merge no", True),
    ]


def test_split_inline_text_spans_preserves_ranking_shortcuts_and_template() -> None:
    spans = split_inline_text_spans("`#水王` / `#水王 <主体> <范围> <时间>`")

    assert [(span.text, span.code) for span in spans] == [
        ("#水王", True),
        (" / ", False),
        ("#水王 <主体> <范围> <时间>", True),
    ]


def test_load_plugin_doc_bundle_parses_water_commands() -> None:
    bundle = load_plugin_doc_bundle(
        source=Path("src/plugins/water/docs/README.MD"),
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    merge = next(feature for feature in bundle.index if feature.slug == "merge-confirm")
    admin = next(
        feature for feature in bundle.index if feature.slug == "admin-maintenance"
    )

    assert merge.trigger == "`#water.merge yes` / `#water.merge no`"
    assert "`#water help`" in admin.trigger
    assert "`#water ignore <group_id>`" in admin.trigger
    assert "`#water season delete <season_id>`" in admin.trigger


def test_demo_image_renderer_wraps_long_admin_command() -> None:
    renderer = DemoImageRenderer()

    lines = renderer._wrap_inline_text(  # pyright: ignore[reportPrivateUsage]
        (
            "`#water help` / `#water settle [YYYYMMDD] [-f|--force]` / "
            "`#water pardon <penalty_id>` / `#water ignore <group_id>` / "
            "`#water ignored` / `#water state`"
        ),
        max_width=320,
        font=renderer.meta_font,  # pyright: ignore[reportPrivateUsage]
    )

    assert all(
        renderer._inline_line_width(line, renderer.meta_font) <= 320  # pyright: ignore[reportPrivateUsage]
        for line in lines
    )
    assert all("`" not in span.text for line in lines for span in line)


def test_demo_image_renderer_fit_inline_spans_keeps_code_flags() -> None:
    renderer = DemoImageRenderer()

    fitted = renderer._fit_inline_spans(  # pyright: ignore[reportPrivateUsage]
        (
            InlineTextSpan("指令示例: ", code=False),
            InlineTextSpan("#water.merge yes", code=True),
            InlineTextSpan(" / ", code=False),
            InlineTextSpan("#water.merge no", code=True),
        ),
        renderer.meta_font,  # pyright: ignore[reportPrivateUsage]
        220,
    )

    assert "".join(span.text for span in fitted).endswith("...")
    assert any(span.code for span in fitted)
    assert all("`" not in span.text for span in fitted)
