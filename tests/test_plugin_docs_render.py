from PIL import ImageDraw

from src.lib.plugin_docs.models import DocsDemoTurn
from src.lib.plugin_docs.render.demo import build_trace_footer_left_text
from src.lib.plugin_docs.render.encoding import WEBP_MAX_DIMENSION, encode_docs_image
from tests.test_plugin_docs_support import *


def test_audit_demo_layout_accepts_all_project_readmes() -> None:
    readmes = [
        path
        for root in (Path("src/plugins"), Path("src/hooks"))
        for path in sorted(root.glob("**/README.MD"))
        if "/docs/" in path.as_posix()
    ]
    errors: list[str] = []

    for source in readmes:
        bundle = load_plugin_doc_bundle(
            source=source,
            default_name=source.parent.name,
            default_description="desc",
            trigger=TriggerType.COMMAND,
            permission=Permission.NORMAL,
        )
        for feature in bundle.index:
            errors.extend(
                f"{source}: {feature.slug}: {message}"
                for message in audit_demo_layout(bundle, feature)
            )

    assert errors == []


def test_demo_theme_has_required_tokens() -> None:
    assert DEFAULT_DEMO_THEME.theme_name == SENRIN_V3_THEME.name
    assert DEFAULT_DEMO_THEME.impression_color == DEFAULT_IMPRESSION_COLOR
    assert DEFAULT_DEMO_THEME.page_bg
    assert DEFAULT_DEMO_THEME.panel_bg
    assert DEFAULT_DEMO_THEME.accent
    assert DEFAULT_DEMO_THEME.strong
    assert DEFAULT_DEMO_THEME.deep
    assert DEFAULT_DEMO_THEME.hint
    assert DEFAULT_DEMO_THEME.line
    assert DEFAULT_DEMO_THEME.shell_bg
    assert DEFAULT_DEMO_THEME.shell_border
    assert DEFAULT_DEMO_THEME.user_bubble
    assert DEFAULT_DEMO_THEME.bot_bubble
    assert DEFAULT_DEMO_THEME.system_bubble
    assert DEFAULT_DEMO_THEME.system_border
    assert DEFAULT_DEMO_THEME.inline_code_bg
    assert DEFAULT_DEMO_THEME.inline_code_text
    assert DEFAULT_DEMO_THEME.avatar_text
    assert DEFAULT_DEMO_THEME.bot_avatar_bg
    assert DEFAULT_DEMO_THEME.bot_avatar_border
    assert DEFAULT_DEMO_THEME.system_label_bg
    assert DEFAULT_DEMO_THEME.system_label_text
    assert DEFAULT_DEMO_THEME.terminal_flag
    assert DEFAULT_DEMO_THEME.grid_color
    assert DEFAULT_DEMO_THEME.decor_color
    assert DEFAULT_DEMO_THEME.footer_divider
    assert DEFAULT_DEMO_THEME.standee_anchor_fill
    assert DEFAULT_DEMO_THEME.showcase_accent_rail_bg
    assert DEFAULT_DEMO_THEME.showcase_support_rail_bg


def test_build_demo_theme_uses_dynamic_impression_color_palette() -> None:
    theme = get_demo_theme(impression_color="#3BC9DB")

    assert theme.theme_name == SENRIN_V3_THEME.name
    assert theme.impression_color == "#3BC9DB"
    assert theme.accent == "#3BC9DB"
    assert theme.page_bg != theme.accent
    assert theme.bot_bubble == theme.panel_soft_bg
    assert theme.deep != theme.hint
    assert theme.grid_color != theme.accent
    assert theme.standee_anchor_fill != theme.accent


def test_normalize_hex_color_falls_back_to_default() -> None:
    assert normalize_hex_color("not-a-color") == DEFAULT_IMPRESSION_COLOR
    assert normalize_hex_color("#abc") == "#AABBCC"


def test_build_demo_theme_keeps_backward_compatible_alias() -> None:
    assert build_demo_theme("#3BC9DB") == get_demo_theme(impression_color="#3BC9DB")


def test_load_plugin_doc_bundle_uses_explicit_impression_color_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试颜色透传。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一条说明。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一条说明。
- 指令: `#alpha`
#### 说明
alpha
#### 前置条件
无
#### 完整流程
```demo
USER: #alpha
BOT: Alpha 完成
```
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        impression_color="#3BC9DB",
    )

    assert bundle.impression_color == "#3BC9DB"


def test_demo_image_renderer_uses_theme() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    assert renderer.theme_name == SENRIN_V3_THEME.name
    assert renderer.theme == get_demo_theme(impression_color="#3BC9DB")
    assert not hasattr(renderer, "PAGE_BG")
    assert not hasattr(renderer, "ACCENT")
    assert not hasattr(renderer, "SHELL_BG")


def test_demo_collection_renderer_uses_theme() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    assert renderer.theme_name == SENRIN_V3_THEME.name
    assert hasattr(renderer, "theme")
    assert renderer.theme == DEFAULT_DEMO_THEME
    assert not hasattr(renderer, "PAGE_BG")
    assert not hasattr(renderer, "CARD_BG")
    assert not hasattr(renderer, "TITLE")


def test_demo_collection_renderer_resolves_impression_color_on_render() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    renderer.render(
        title="测试插件",
        summary="用于测试 collection 是否走印象色。",
        impression_color="#3BC9DB",
        tiles=(),
    )

    assert renderer.theme.theme_name == SENRIN_V3_THEME.name
    assert renderer.theme.impression_color == "#3BC9DB"
    assert renderer.theme.accent == "#3BC9DB"


def test_demo_theme_layout_constants() -> None:
    assert DEFAULT_DEMO_THEME.outer_margin == 40
    assert DEFAULT_DEMO_THEME.shell_radius == 32
    assert DEFAULT_DEMO_THEME.panel_radius == 28
    assert DEFAULT_DEMO_THEME.card_radius == 32
    assert DEFAULT_DEMO_THEME.chip_radius == 20
    assert DEFAULT_DEMO_THEME.inline_code_radius == 12
    assert DEFAULT_DEMO_THEME.inline_code_pad_x == 8
    assert DEFAULT_DEMO_THEME.inline_code_pad_y == 4


def test_demo_theme_showcase_tokens_follow_expected_scale() -> None:
    assert DEFAULT_DEMO_THEME.canvas_width == 1280
    assert DEFAULT_DEMO_THEME.hero_top == 64
    assert DEFAULT_DEMO_THEME.hero_side_padding % 8 == 0
    assert DEFAULT_DEMO_THEME.hero_bottom_padding % 8 == 0
    assert DEFAULT_DEMO_THEME.hero_standee_size == 304
    assert DEFAULT_DEMO_THEME.pill_height == 40
    assert DEFAULT_DEMO_THEME.section_gap % 8 == 0
    assert DEFAULT_DEMO_THEME.instruction_padding_x % 8 == 0
    assert DEFAULT_DEMO_THEME.instruction_padding_y % 8 == 0
    assert DEFAULT_DEMO_THEME.trigger_padding_x % 8 == 0
    assert DEFAULT_DEMO_THEME.trigger_padding_y % 8 == 0
    assert DEFAULT_DEMO_THEME.avatar_size % 8 == 0
    assert DEFAULT_DEMO_THEME.bubble_padding_x % 8 == 0
    assert DEFAULT_DEMO_THEME.bubble_padding_y % 8 == 0
    assert DEFAULT_DEMO_THEME.hero_summary_line_height >= 56
    assert DEFAULT_DEMO_THEME.bubble_line_height >= 48
    assert DEFAULT_DEMO_THEME.grid_spacing % 8 == 0
    assert DEFAULT_DEMO_THEME.footer_height >= 72


def test_encode_docs_image_scales_oversized_canvas_and_keeps_webp() -> None:
    image = Image.new("RGB", (1280, WEBP_MAX_DIMENSION + 1024), "#FFFFFF")

    encoded = encode_docs_image(image, webp_quality=88, webp_method=6)
    decoded = Image.open(BytesIO(encoded))

    assert encoded.startswith(b"RIFF")
    assert decoded.format == "WEBP"
    assert decoded.width <= WEBP_MAX_DIMENSION
    assert decoded.height <= WEBP_MAX_DIMENSION


def test_demo_image_renderer_measure_layout_includes_footer_traceability() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")
    generated_at = datetime(2026, 6, 16, 21, 30, 45)

    layout = renderer._measure_layout(
        plugin_title="测试插件",
        feature_title="查询功能",
        feature_summary="用于查询状态。",
        feature_trigger="#query",
        feature_overview="输入命令后即可查看状态。",
        feature_preconditions="需要在群聊中调用。",
        feature_failures="参数错误时会提示原因。",
        feature_flow_notes="支持直接回复触发。",
        plugin_trigger="指令触发",
        feature_permission="普通用户",
        plugin_version="v1.2.3",
        plugin_author="SakuraiCora",
        turns=(),
        locale="zh-CN",
        generated_at=generated_at,
    )

    assert layout.footer_left_text == "测试插件 · 查询功能 · v1.2.3 · By SakuraiCora"
    assert (
        layout.footer_right_text == "Generated at 2026-06-16 21:30:45 | © SakuraiSenrin"
    )
    assert (
        layout.footer_rect[3] - layout.footer_rect[1]
        == DEFAULT_DEMO_THEME.footer_height
    )


def test_build_trace_footer_left_text_dedupes_same_plugin_and_feature_title() -> None:
    assert (
        build_trace_footer_left_text(
            plugin_title="帮助文档",
            feature_title="帮助文档",
            plugin_version="v3.0.0",
            plugin_author="SakuraiSenrin",
        )
        == "帮助文档 · v3.0.0 · By SakuraiSenrin"
    )


def test_demo_image_renderer_measure_layout_uses_structured_trigger_layout() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    layout = renderer._measure_layout(  # pyright: ignore[reportPrivateUsage]
        plugin_title="测试插件",
        feature_title="复杂命令",
        feature_summary="测试结构化命令布局。",
        feature_trigger=(
            "wordbank search [关键词] [图片] --field all|trigger|response "
            "--creator 账号 --page 页码 --limit 每页数量"
        ),
        feature_overview="测试说明。",
        feature_preconditions="无",
        feature_failures="无",
        feature_flow_notes="",
        plugin_trigger="指令触发",
        feature_permission="普通用户",
        plugin_version="v1.2.3",
        plugin_author="SakuraiCora",
        turns=(),
        locale="zh-CN",
        generated_at=datetime(2026, 6, 16, 21, 30, 45),
    )

    assert layout.trigger_layout.lines[0].kind == "root"
    assert any(line.kind == "flag" for line in layout.trigger_layout.lines[1:])
    assert layout.trigger_layout.total_height > renderer._line_height_for_font(
        renderer.body_font
    )  # pyright: ignore[reportPrivateUsage]


def test_demo_image_renderer_measure_layout_uses_alt_lines_for_multi_commands() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    layout = renderer._measure_layout(  # pyright: ignore[reportPrivateUsage]
        plugin_title="测试插件",
        feature_title="多指令命令",
        feature_summary="测试多指令布局。",
        feature_trigger=(
            "wordbank trigger prob <group_id> <0.0-1.0>；"
            "wordbank trigger set <group_id> <新触发内容>"
        ),
        feature_overview="测试说明。",
        feature_preconditions="无",
        feature_failures="无",
        feature_flow_notes="",
        plugin_trigger="指令触发",
        feature_permission="普通用户",
        plugin_version="v1.2.3",
        plugin_author="SakuraiCora",
        turns=(),
        locale="zh-CN",
        generated_at=datetime(2026, 6, 16, 21, 30, 45),
    )

    assert layout.trigger_layout.lines[0].kind == "root"
    assert any(line.kind == "alternative" for line in layout.trigger_layout.lines[1:])
    assert all(
        line.indent_level == 0
        for line in layout.trigger_layout.lines
        if line.kind in {"root", "alternative"}
    )


def test_demo_image_renderer_measures_short_bubbles_without_min_width() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    spec = renderer._measure_turn(  # pyright: ignore[reportPrivateUsage]
        DocsDemoTurn(
            speaker="USER",
            text="hi",
        ),
        content_width=900,
    )

    expected_width = (
        renderer._max_inline_line_width(spec.lines, renderer.body_font)  # pyright: ignore[reportPrivateUsage]
        + renderer.theme.bubble_padding_x * 2
    )
    assert spec.width == expected_width
    assert spec.width < 280


def test_demo_image_renderer_normalizes_trailing_whitespace_only() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    normalized = renderer._normalize_demo_text(  # pyright: ignore[reportPrivateUsage]
        "  keep-leading  \nline two\t \n   \n"
    )

    assert normalized == "  keep-leading\nline two\n\n"


def test_demo_image_renderer_ignores_wu_placeholder_in_note_lines() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    assert renderer._split_note_lines("无") == ()  # pyright: ignore[reportPrivateUsage]
    assert renderer._split_note_lines("- 需要管理员\n无") == (  # pyright: ignore[reportPrivateUsage]
        "需要管理员",
    )


def test_demo_image_renderer_hides_duplicate_plugin_kicker_for_simple_leaf() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")

    layout = renderer._measure_layout(  # pyright: ignore[reportPrivateUsage]
        plugin_title="凛凛的妙妙小工具",
        feature_title="凛凛的妙妙小工具",
        feature_summary="用于测试 simple-leaf 同名标题去重。",
        feature_trigger="词条触发 / #help 查询",
        feature_overview="目录说明。",
        feature_preconditions="无",
        feature_failures="无",
        feature_flow_notes="",
        plugin_trigger="词条触发",
        feature_permission="普通用户",
        plugin_version="v0.1.0",
        plugin_author="SakuraiCora",
        turns=(),
        locale="zh-CN",
        generated_at=datetime(2026, 6, 19, 9, 31, 24),
    )

    assert layout.plugin_lines == ()
    assert layout.title_rect[1] == layout.pill_rects[-1][3] + 24
    assert layout.footer_left_text == "凛凛的妙妙小工具 · v0.1.0 · By SakuraiCora"


def test_demo_image_renderer_builds_full_section_bands_for_study_demo() -> None:
    node = load_doc_node(
        source="src/plugins/study/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    feature = next(item for item in node.bundle.index if item.slug == "main")
    renderer = DemoImageRenderer(impression_color=node.bundle.impression_color)

    layout = renderer._measure_layout(  # pyright: ignore[reportPrivateUsage]
        plugin_title=node.bundle.title,
        feature_title=feature.title,
        feature_summary=feature.summary,
        feature_trigger=feature.trigger,
        feature_overview=feature.overview,
        feature_preconditions=feature.preconditions,
        feature_failures=feature.failures,
        feature_flow_notes=feature.flow_notes,
        plugin_trigger=node.bundle.trigger,
        feature_permission=str(feature.permission),
        plugin_version=node.bundle.version,
        plugin_author=node.bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
        generated_at=datetime(2026, 6, 19, 16, 44, 50),
    )

    assert layout.demo_heading_rect is not None
    assert layout.demo_rect[1] - layout.demo_heading_rect[3] <= 40
    assert len(layout.demo_section_bands) == 6
    assert [band.index for band in layout.demo_section_bands] == [1, 2, 3, 4, 5, 6]
    assert [band.title for band in layout.demo_section_bands] == [
        "引导式",
        "传统模式",
        "=> 语法糖",
        "合并转发批量导入",
        "事件类例子",
        "高级选项",
    ]
    for band in layout.demo_section_bands:
        assert band.tag_rect[0] == band.content_rect[0]
        assert band.tag_rect[2] == band.content_rect[2]
        assert band.content_rect[3] <= band.rect[3]
        assert band.tag_rect[1] >= band.rect[1]


def test_demo_image_renderer_left_aligns_block_section_title_text() -> None:
    node = load_doc_node(
        source="src/plugins/study/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    feature = next(item for item in node.bundle.index if item.slug == "main")
    renderer = DemoImageRenderer(impression_color=node.bundle.impression_color)

    layout = renderer._measure_layout(  # pyright: ignore[reportPrivateUsage]
        plugin_title=node.bundle.title,
        feature_title=feature.title,
        feature_summary=feature.summary,
        feature_trigger=feature.trigger,
        feature_overview=feature.overview,
        feature_preconditions=feature.preconditions,
        feature_failures=feature.failures,
        feature_flow_notes=feature.flow_notes,
        plugin_trigger=node.bundle.trigger,
        feature_permission=str(feature.permission),
        plugin_version=node.bundle.version,
        plugin_author=node.bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
        generated_at=datetime(2026, 6, 19, 16, 44, 50),
    )
    band = layout.demo_section_bands[0]
    label_text = band.title
    captured: dict[str, tuple[float, float]] = {}

    def capture_draw_text(
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str | tuple[int, int, int, int],
    ) -> None:
        if text == label_text:
            captured[text] = (x, y)

    renderer._draw_text = capture_draw_text  # pyright: ignore[reportMethodAssignment]

    image = Image.new("RGBA", (renderer.WIDTH, layout.total_height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    renderer._draw_demo(  # pyright: ignore[reportPrivateUsage]
        image,
        draw,
        layout,
        locale="zh-CN",
    )

    badge_right = band.tag_rect[0] + 66
    assert captured[label_text][0] == badge_right + 18
    assert captured[label_text][1] >= band.tag_rect[1]


def test_demo_image_renderer_uses_card_style_for_system_turn() -> None:
    renderer = DemoImageRenderer(impression_color="#3BC9DB")
    spec = renderer._measure_turn(  # pyright: ignore[reportPrivateUsage]
        DocsDemoTurn(
            speaker="SYSTEM",
            text="`a` 表示对所有人有效，`f` 表示关闭群组隔离，因此会映射到更宽范围。",
            section="传统参数式",
        ),
        960,
    )

    placement = renderer._place_turn(  # pyright: ignore[reportPrivateUsage]
        spec,
        top=120,
        left=120,
        right=1080,
    )

    assert placement.bubble_rect is not None
    assert placement.label_rect is not None
    assert placement.label_rect[0] >= placement.bubble_rect[0]
    assert placement.label_rect[1] >= placement.bubble_rect[1]
    assert placement.text_rect[1] > placement.label_rect[3]
    assert placement.bubble_rect[3] > placement.text_rect[3]


def test_render_help_dashboard_returns_showcase_canvas() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    image = Image.open(
        BytesIO(
            render_help_dashboard(
                (
                    HelpDashboardSection(
                        kind="developer",
                        title="官方功能扩展",
                        nodes=(node,),
                    ),
                ),
                locale="zh-CN",
            )
        )
    )
    assert image.width == 1280
    assert image.height > 500


def test_render_help_dashboard_uses_localized_header_and_footer_copy() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    renderer = ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    )

    image_bytes = renderer.render_dashboard(
        sections=(
            HelpDashboardSection(
                kind="developer",
                title="官方功能扩展",
                nodes=(node,),
                column=1,
            ),
        ),
        locale="zh-CN",
        generated_at=datetime(2026, 6, 18, 22, 0, 0),
    )

    image = Image.open(BytesIO(image_bytes))
    assert image.width == 1280
    assert image.height > 500


def test_render_help_dashboard_grid_extends_to_footer_region() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    renderer = ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    )

    image = Image.open(
        BytesIO(
            renderer.render_dashboard(
                sections=(
                    HelpDashboardSection(
                        kind="system",
                        title="系统核心预置",
                        nodes=(node,) * 8,
                        column=0,
                    ),
                    HelpDashboardSection(
                        kind="developer",
                        title="官方功能扩展",
                        nodes=(node,) * 5,
                        column=1,
                    ),
                    HelpDashboardSection(
                        kind="community",
                        title="社区衍生工坊",
                        nodes=(node,) * 3,
                        column=1,
                    ),
                ),
                locale="zh-CN",
                generated_at=datetime(2026, 6, 18, 22, 0, 0),
            )
        )
    ).convert("RGBA")

    sample_x = renderer.theme.hero_side_padding // 3
    sample_y = (
        image.height
        - renderer.theme.footer_height
        - renderer.theme.outer_margin
        - (renderer.theme.grid_spacing // 2)
    )

    assert sample_y > image.height // 2
    assert image.getpixel((sample_x, sample_y)) != Image.new(
        "RGBA", (1, 1), renderer.theme.page_bg
    ).getpixel((0, 0))


def test_support_strip_qr_layout_applies_optical_vertical_offset(
    monkeypatch: Any,
) -> None:
    renderer = ProgressiveDisclosureRenderer(impression_color="#74C0FC")
    qr_image = Image.new("RGBA", (160, 160), (255, 255, 255, 255))
    monkeypatch.setattr(renderer, "_load_support_qr_image", lambda _path: qr_image)

    layout = renderer._measure_support_strip(  # pyright: ignore[reportPrivateUsage]
        locale="zh-CN",
        top=240,
    )

    assert layout.qr_rect is not None
    qr_frame_top = layout.qr_rect[1] - renderer.SUPPORT_STRIP_QR_FRAME_PADDING_Y
    qr_frame_height = qr_image.height + renderer.SUPPORT_STRIP_QR_FRAME_PADDING_Y * 2
    centered_top = (
        layout.rect[1] + ((layout.rect[3] - layout.rect[1]) - qr_frame_height) // 2
    )

    assert qr_frame_top == max(
        layout.rect[1],
        centered_top + renderer.SUPPORT_STRIP_QR_VERTICAL_OFFSET_Y,
    )


def test_render_with_support_strip_trims_existing_source_footer() -> None:
    renderer = ProgressiveDisclosureRenderer(impression_color="#74C0FC")
    source_height = 720
    source = Image.new("RGBA", (renderer.WIDTH, source_height), "#FFFFFF")
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    source_bytes = buffer.getvalue()

    trimmed_source_height = (
        source_height
        - renderer.theme.footer_gap_top
        - renderer.theme.footer_height
        - renderer.theme.outer_margin
    )
    support_layout = renderer._measure_support_strip(  # pyright: ignore[reportPrivateUsage]
        locale="zh-CN",
        top=trimmed_source_height + renderer.SUPPORT_STRIP_GAP,
    )
    expected_height = (
        support_layout.rect[3]
        + renderer.theme.footer_gap_top
        + renderer.theme.footer_height
        + renderer.theme.outer_margin
    )

    rendered = Image.open(
        BytesIO(
            renderer.render_with_support_strip(
                source_bytes,
                locale="zh-CN",
                footer_left_text="帮助文档 · v3.0.0 · By SakuraiSenrin",
                footer_right_text="Generated at 2026-06-25 18:00:00 | © SakuraiSenrin",
            )
        )
    )

    assert rendered.height == expected_height


def test_dashboard_section_height_includes_command_block() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    renderer = ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    )
    section_width = (
        renderer.WIDTH
        - renderer.theme.hero_side_padding * 2
        - renderer.DASHBOARD_CARD_GAP_X
    ) // 2
    section = HelpDashboardSection(
        kind="developer",
        title="官方功能扩展",
        nodes=(node,),
        column=1,
    )

    measured = renderer._measure_dashboard_section_height(section, section_width)
    content_width = section_width - renderer.GUIDE_SECTION_PADDING_X * 2
    title_height = renderer._line_height_for_font(renderer.summary_font)
    summary_height = min(
        2,
        len(
            tuple(
                renderer._wrap_inline_text(
                    node.summary or node.bundle.summary,
                    max_width=content_width - 40,
                    font=renderer.note_font,
                )
            )
        ),
    ) * renderer._line_height_for_font(renderer.note_font)
    command_layout = build_command_layout(
        f"#help {node.title}",
        max_width=content_width - 32,
        line_height=renderer._line_height_for_font(renderer.note_font),
        indent_px=renderer.COMMAND_INDENT_PX,
        measure_text=lambda value: renderer._text_width(value, renderer.note_font),
        palette=renderer._command_palette(),
    )
    naive = (
        renderer.GUIDE_SECTION_PADDING_Y * 2
        + title_height
        + renderer.DASHBOARD_SECTION_TITLE_GAP
        + renderer._line_height_for_font(renderer.instruction_font)
        + summary_height
        + renderer.DASHBOARD_SECTION_SUMMARY_GAP
        + renderer.DASHBOARD_SECTION_COMMAND_GAP
        + command_layout.total_height
        + renderer.DASHBOARD_CARD_COMMAND_PADDING_Y * 2
    )

    assert measured >= naive


def test_dashboard_layout_measures_sections_for_dynamic_masonry() -> None:
    renderer = ProgressiveDisclosureRenderer(impression_color="#74C0FC")
    system = HelpDashboardSection(
        kind="system",
        title="系统核心预置",
        nodes=(),
    )
    developer = HelpDashboardSection(
        kind="developer",
        title="官方功能扩展",
        nodes=(),
    )
    community = HelpDashboardSection(
        kind="community",
        title="社区衍生工坊",
        nodes=(),
    )

    section_width = (
        renderer.WIDTH
        - renderer.theme.hero_side_padding * 2
        - renderer.DASHBOARD_CARD_GAP_X
    ) // 2
    system_height = renderer._measure_dashboard_section_height(system, section_width)
    developer_height = renderer._measure_dashboard_section_height(
        developer, section_width
    )
    community_height = renderer._measure_dashboard_section_height(
        community, section_width
    )

    assert system_height > 0
    assert developer_height > 0
    assert community_height > 0


def test_dashboard_pixel_truncation_reserves_ellipsis_width() -> None:
    renderer = ProgressiveDisclosureRenderer(impression_color="#74C0FC")

    text = "A very long mixed 文本 description for dashboard card rendering"
    max_width = renderer._pixel_text_width(
        "A very long mixed 文本 desc",
        renderer.note_font,
    )
    fitted = renderer._truncate_text_to_width_pixels(
        text,
        renderer.note_font,
        max_width=max_width,
    )

    assert fitted.endswith("...")
    assert renderer._pixel_text_width(fitted, renderer.note_font) <= max_width


def test_dashboard_card_layout_bottom_anchors_command_and_limits_summary_height() -> (
    None
):
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    renderer = ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    )
    card_width = (
        renderer.WIDTH
        - renderer.theme.hero_side_padding * 2
        - renderer.DASHBOARD_CARD_GAP_X
    ) // 2
    verbose_node = DocNode(
        kind=node.kind,
        slug=node.slug,
        parent_slug=node.parent_slug,
        category=node.category,
        order=node.order,
        visible=node.visible,
        hidden=node.hidden,
        internal=node.internal,
        permission=node.permission,
        title=node.title,
        summary=(
            "这是一个非常长的 Bot Dashboard 描述，"
            "用来验证中英 mixed text 在固定卡片高度里会按像素换行，"
            "并且在触底前安全截断，"
            "不会压到底部的 #help 指令块，也不会因为字符数估算错误导致遮挡。 "
            "Visit https://example.com/really/long/path/for/layout/check if needed."
        ),
        description=node.description,
        aliases=node.aliases,
        source_path=node.source_path,
        bundle=node.bundle,
        module_name=node.module_name,
        plugin_name=node.plugin_name,
    )

    card = renderer._measure_dashboard_card(verbose_node, card_width)

    assert card.command_rect[3] == card.height - renderer.DASHBOARD_CARD_PADDING_Y
    assert card.command_rect[1] > card.summary_top + card.summary_block_height
    assert len(card.summary_lines) <= renderer.DASHBOARD_CARD_SUMMARY_VISIBLE_LINES
    if card.summary_lines:
        last_line = "".join(span.text for span in card.summary_lines[-1])
        assert last_line.endswith("...") or (
            len(card.summary_lines) < renderer.DASHBOARD_CARD_SUMMARY_VISIBLE_LINES
        )


def test_dashboard_footer_left_text_preserves_plugins_when_pixels_allow() -> None:
    renderer = ProgressiveDisclosureRenderer(impression_color="#74C0FC")
    left_text = "Help Center · Global Dashboard · 5 plugins · By SakuraiSenrin"
    max_width = renderer._pixel_text_width(left_text, renderer.footer_font)

    fitted = renderer._truncate_text_to_width_pixels(
        left_text,
        renderer.footer_font,
        max_width=max_width,
    )

    assert fitted == left_text
    assert "5 plugins" in fitted


def test_render_plugin_guide_and_copy_text_list_all_visible_features() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    visible_features = plugin_docs_module.filter_features_by_permission(
        node.features,
        actor_permission=Permission.NORMAL,
    )
    assert "add" in {feature.slug for feature in visible_features}
    assert "add-scope" in {feature.slug for feature in visible_features}

    image = Image.open(
        BytesIO(
            render_plugin_guide(
                node,
                actor_permission=Permission.NORMAL,
                locale="zh-CN",
                prefer_static=False,
            )
        )
    )
    assert image.width >= 650
    assert image.height > 1200

    copy_text = build_plugin_guide_copy_text(
        node,
        features=visible_features,
        child_nodes=(),
        locale="zh-CN",
    )
    assert "👉 基础添加" in copy_text
    assert "#添加词条 触发词 => 响应词" in copy_text
    assert "查看 demo：" not in copy_text
    assert "#添加词条 触发词 => 响应词 -s 本群|全局|自己|私聊" in copy_text
    assert "#help 词库模块 add-scope" not in copy_text
    assert "主功能" not in copy_text
    assert "高级功能" not in copy_text
    assert "反馈与交流群" in copy_text


def test_render_plugin_guide_builds_support_strip_inline_without_post_compose(
    monkeypatch: Any,
) -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    def fail_post_compose(*args: Any, **kwargs: Any) -> bytes:
        _ = (args, kwargs)
        raise AssertionError("plugin guide should not call render_with_support_strip")

    monkeypatch.setattr(
        ProgressiveDisclosureRenderer,
        "render_with_support_strip",
        fail_post_compose,
    )

    image = Image.open(
        BytesIO(
            render_plugin_guide(
                node,
                actor_permission=Permission.NORMAL,
                locale="zh-CN",
                prefer_static=False,
            )
        )
    )

    assert image.width >= 650
    assert image.height > 1200


def test_render_plugin_guide_disables_uppercase_english_watermarks(
    monkeypatch: Any,
) -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    def fail_draw_section_watermark(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise AssertionError(
            "plugin guide should not draw uppercase English watermarks"
        )

    monkeypatch.setattr(
        ProgressiveDisclosureRenderer,
        "_draw_section_watermark",
        fail_draw_section_watermark,
    )

    image = Image.open(
        BytesIO(
            render_plugin_guide(
                node,
                actor_permission=Permission.NORMAL,
                locale="zh-CN",
                prefer_static=False,
            )
        )
    )

    assert image.width >= 650
    assert image.height > 1200


def test_render_static_entry_builds_support_strip_inline_without_post_compose(
    monkeypatch: Any,
) -> None:
    node = load_doc_node(
        source="src/plugins/community_mima/docs/README.MD",
        default_name="用户反馈群密码",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    def fail_post_compose(*args: Any, **kwargs: Any) -> bytes:
        _ = (args, kwargs)
        raise AssertionError("static entry should not call render_with_support_strip")

    monkeypatch.setattr(
        ProgressiveDisclosureRenderer,
        "render_with_support_strip",
        fail_post_compose,
    )

    image = Image.open(BytesIO(render_static_entry(node, locale="zh-CN")))

    assert image.width > 700
    assert image.height > 600


def test_render_help_dashboard_builds_support_strip_inline_without_post_compose(
    monkeypatch: Any,
) -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    def fail_post_compose(*args: Any, **kwargs: Any) -> bytes:
        _ = (args, kwargs)
        raise AssertionError("dashboard should not call render_with_support_strip")

    monkeypatch.setattr(
        ProgressiveDisclosureRenderer,
        "render_with_support_strip",
        fail_post_compose,
    )

    image = Image.open(
        BytesIO(
            plugin_docs_module.render_help_dashboard(
                (
                    plugin_docs_module.HelpDashboardSection(
                        kind="developer",
                        title="官方功能扩展",
                        nodes=(node,),
                    ),
                ),
                locale="zh-CN",
                prefer_static=False,
                source_path=node.source_path,
            )
        )
    )

    assert image.width > 700
    assert image.height > 900


def test_resolve_help_entry_shape_distinguishes_simple_leaf_and_grouped_nodes() -> None:
    picsearch = load_doc_node(
        source="src/plugins/picsearch/docs/README.MD",
        default_name="图片搜索",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    wordbank = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    admin_meta = create_docs_meta(
        visible=False,
        category="admin",
        order=10,
        source="src/plugins/admin/docs/README.MD",
        slug="admin",
        kind="overview",
    )
    admin = load_doc_node(
        source=admin_meta["source"]["readme_path"],
        default_name="管理模块总览",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        docs_meta=admin_meta,
    )

    assert resolve_help_entry_shape(picsearch) == "simple_leaf"
    assert resolve_help_entry_shape(wordbank) == "plugin_guide"
    assert resolve_help_entry_shape(admin) == "overview_group"


def test_virtual_doc_node_without_features_is_static_entry() -> None:
    node = load_virtual_doc_node(
        VirtualPluginDocSpec(
            slug="community.miaomiao-toolkit",
            title="凛凛的妙妙小工具目录",
            summary="目录摘要",
            description="目录描述",
            trigger="词条触发 / #help 查询",
            author="SakuraiSenrin",
            version="0.1.0",
            impression_color="#74C0FC",
            plugin_name="community_miaomiao",
            module_name="src.plugins.community_miaomiao",
            origin_plugin_slug="community_miaomiao",
            features=(
                VirtualFeatureDocSpec(
                    slug="main",
                    title="凛凛的妙妙小工具",
                    summary="目录摘要",
                    trigger="词条触发 / #help 查询",
                    overview="目录描述",
                    preconditions="无",
                    failures="无",
                    demo_turns=(
                        plugin_docs_module.DocsDemoTurn("USER", "凛凛的妙妙小工具"),
                    ),
                ),
            ),
        )
    )

    assert resolve_help_entry_shape(node) == "simple_leaf"


def _load_wordbank_community_doc_node() -> DocNode:
    meta = create_docs_meta(
        visible=True,
        category="community",
        order=85,
        source="src/plugins/community_miaomiao/docs/README.MD",
        slug="community.miaomiao-toolkit",
        aliases=("凛凛的妙妙小工具", "妙妙小工具", "妙妙小工具目录", "小工具"),
    )
    return load_doc_node(
        source=meta["source"]["readme_path"],
        default_name="凛凛的妙妙小工具",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        docs_meta=meta,
        module_name="src.plugins.community_miaomiao",
        plugin_name="community_miaomiao",
    )


def test_wordbank_derived_directory_is_classified_as_static_entry() -> None:
    node = _load_wordbank_community_doc_node()

    assert resolve_help_entry_shape(node) == "simple_leaf"


def test_render_static_entry_returns_showcase_canvas() -> None:
    node = _load_wordbank_community_doc_node()

    image = Image.open(BytesIO(render_static_entry(node, locale="zh-CN")))

    assert image.width == 1280
    assert image.height > 500


def test_render_static_entry_dynamic_builds_command_and_demo_sections() -> None:
    node = load_doc_node(
        source="src/plugins/community_mima/docs/README.MD",
        default_name="用户反馈群密码",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    image = Image.open(
        BytesIO(
            render_static_entry(
                node,
                locale="zh-CN",
                prefer_static=False,
            )
        )
    )

    assert image.width == 1280
    assert image.height > 1200


def test_render_plugin_summary_returns_showcase_canvas() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    image = Image.open(
        BytesIO(plugin_docs_module.render_plugin_summary(node, locale="zh-CN"))
    )

    assert image.width == 1280
    assert image.height > 500


def test_render_plugin_summary_dynamic_keeps_showcase_support_strip() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    image = Image.open(
        BytesIO(
            plugin_docs_module.render_plugin_summary(
                node,
                locale="zh-CN",
                prefer_static=False,
            )
        )
    )

    assert image.width == 1280
    assert image.height > 700


def test_build_plugin_summary_copy_text_excludes_static_entry_wording() -> None:
    node = load_doc_node(
        source="src/plugins/wordbank/docs/README.MD",
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    copy_text = plugin_docs_module.build_plugin_summary_copy_text(node)

    assert "添加、删除、管理词条，都在这里啦！" in copy_text
    assert "静态社区入口说明页" not in copy_text
    assert "反馈与交流群" not in copy_text


def test_build_simple_leaf_copy_text_includes_direct_demo_command() -> None:
    node = load_doc_node(
        source="src/plugins/picsearch/docs/README.MD",
        default_name="图片搜索",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    feature = node.features[0]

    copy_text = build_simple_leaf_copy_text(node, feature, locale="zh-CN")

    assert "图片搜索" in copy_text
    assert "搜图 [saucenao|ascii2d]" in copy_text
    assert "查看 demo：" not in copy_text
    assert "反馈与交流群" in copy_text


def test_build_feature_copy_text_returns_command_skeleton() -> None:
    node = load_doc_node(
        source="src/plugins/water/docs/README.MD",
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    ranking = next(feature for feature in node.features if feature.slug == "ranking")

    copy_text = build_feature_copy_text(node, ranking, locale="zh-CN")

    assert "查看周期榜单" in copy_text
    assert "#水王" in copy_text
    assert "反馈与交流群" in copy_text


def test_feature_demo_help_command_uses_plugin_title_and_slug() -> None:
    node = load_doc_node(
        source="src/plugins/water/docs/README.MD",
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    ranking = next(feature for feature in node.features if feature.slug == "ranking")

    assert feature_demo_help_command(node, ranking) == "#help water ranking"


def test_build_doc_demo_message_prefers_feature_demo(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        plugin_docs_module,
        "render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )
    monkeypatch.setattr(
        plugin_docs_module,
        "render_plugin_guide",
        lambda *args, **kwargs: b"guide-demo",
    )

    message = build_doc_demo_message(
        source="src/plugins/wordbank/docs/README.MD",
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale="zh-CN",
        feature_query="add-scope",
        prefix_text="参数错误",
    )

    assert str(message).startswith("参数错误")
    assert any(segment.type == "image" for segment in message)
    assert message[-1].data["file"] == "base64://ZmVhdHVyZS1kZW1v"


def test_build_doc_demo_message_falls_back_to_plugin_guide(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        plugin_docs_module,
        "render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )
    monkeypatch.setattr(
        plugin_docs_module,
        "render_plugin_guide",
        lambda *args, **kwargs: b"guide-demo",
    )

    message = build_doc_demo_message(
        source="src/plugins/wordbank/docs/README.MD",
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale="zh-CN",
        feature_query="missing-feature",
        prefix_text="参数错误",
    )

    assert any(segment.type == "image" for segment in message)
    assert message[-1].data["file"] == "base64://Z3VpZGUtZGVtbw=="


def test_build_doc_demo_plan_entry_builds_prefixed_image_plan(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        plugin_docs_module,
        "render_feature_deep_dive",
        lambda *args, **kwargs: b"feature-demo",
    )
    monkeypatch.setattr(
        plugin_docs_module,
        "render_plugin_guide",
        lambda *args, **kwargs: b"guide-demo",
    )

    entry = build_doc_demo_plan_entry(
        source="src/plugins/wordbank/docs/README.MD",
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale="zh-CN",
        feature_query="add-scope",
        prefix_text="参数错误",
    )

    assert isinstance(entry, MessagePlanEntry)
    assert len(entry.blocks) == 2
    rendered = render_message_plan_entry(entry)
    assert str(rendered).startswith("参数错误")
    assert rendered[-1].data["file"] == "base64://ZmVhdHVyZS1kZW1v"


def test_plugin_docs_generate_reuses_one_build_timestamp(
    monkeypatch: Any,
) -> None:
    dummy_bundle = plugin_docs_module.PluginDocBundle(
        title="测试插件",
        description="",
        summary="",
        trigger="",
        permission="",
        author="",
        version="",
        impression_color=DEFAULT_IMPRESSION_COLOR,
        index=(),
        source_path=Path("test"),
    )
    jobs = (
        plugin_docs_script.DemoRenderJob(
            bundle=dummy_bundle,
            feature_index=0,
            output=plugin_docs_script.ROOT / "tmp-demo-a.png",
        ),
        plugin_docs_script.DemoRenderJob(
            bundle=dummy_bundle,
            feature_index=1,
            output=plugin_docs_script.ROOT / "tmp-demo-b.png",
        ),
    )
    seen_times: list[datetime | None] = []

    monkeypatch.setattr(
        plugin_docs_script,
        "collect_demo_jobs",
        lambda: (1, jobs),
    )

    def fake_render_demo_job(
        job: plugin_docs_script.DemoRenderJob,
        *,
        generated_at: datetime | None = None,
    ) -> tuple[Path, bytes]:
        seen_times.append(generated_at)
        return job.output, b"demo"

    monkeypatch.setattr(plugin_docs_script, "render_demo_job", fake_render_demo_job)
    monkeypatch.setattr(
        plugin_docs_script, "write_demo_result", lambda result: result[0]
    )
    monkeypatch.setattr(plugin_docs_script, "_write_line", lambda message: None)

    assert plugin_docs_script.generate(workers=1) == 0
    assert len(seen_times) == 2
    assert seen_times[0] == seen_times[1]
    assert seen_times[0] is not None
    assert seen_times[0].microsecond == 0


def test_demo_image_renderer_handles_features_without_demo_turns() -> None:
    renderer = DemoImageRenderer()
    image = Image.open(
        BytesIO(
            renderer.render(
                plugin_title="测试插件",
                feature_title="无 Demo 功能",
                feature_summary="仅展示说明内容。",
                feature_trigger="#noop",
                feature_overview="这个功能只有概览和说明。",
                feature_preconditions="需要在群聊中调用。",
                feature_failures="参数错误时会提示原因。",
                feature_flow_notes="不会附带完整流程演示。",
                plugin_trigger="指令触发",
                feature_permission="普通用户",
                plugin_version="0.1.0",
                plugin_author="SakuraiCora",
                turns=(),
                locale="zh-CN",
            )
        )
    )
    assert image.width == 1280
    assert image.height < 1200
    assert (
        renderer.audit(
            plugin_title="测试插件",
            feature_title="无 Demo 功能",
            feature_summary="仅展示说明内容。",
            feature_trigger="#noop",
            feature_overview="这个功能只有概览和说明。",
            feature_preconditions="需要在群聊中调用。",
            feature_failures="参数错误时会提示原因。",
            feature_flow_notes="不会附带完整流程演示。",
            plugin_trigger="指令触发",
            feature_permission="普通用户",
            plugin_version="0.1.0",
            plugin_author="SakuraiCora",
            turns=(),
            locale="zh-CN",
        )
        == ()
    )


def test_demo_collection_renderer_uses_showcase_canvas_width() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    assert renderer.CANVAS_WIDTH == 1280
    assert renderer.OUTER_MARGIN == 88
    assert renderer.GRID_GAP_X % 8 == 0
    assert renderer.GRID_GAP_Y % 8 == 0
