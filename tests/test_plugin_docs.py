from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image

import scripts.build_docs as plugin_docs_script
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DemoImageRenderer,
    DocsRenderContext,
    InlineTextSpan,
    audit_demo_layout,
    build_readme_docs,
    load_plugin_doc_bundle,
    load_representative_demo_bytes,
    match_feature,
    split_inline_text_spans,
)


def test_load_plugin_doc_bundle_parses_real_readme() -> None:
    source = Path("src/plugins/water/docs/README.MD")

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert bundle.title == "吹水记录"
    assert "群聊活跃度" in bundle.summary
    assert bundle.trigger == "指令触发"
    assert bundle.permission == "普通用户"
    assert len(bundle.index) == 7

    ranking = next(feature for feature in bundle.index if feature.slug == "ranking")
    assert ranking.title == "查看周期榜单"
    assert "月榜" in ranking.aliases
    assert ranking.demo_filename == "water-ranking.png"
    assert ranking.demo_turns[0].speaker == "USER"
    assert ranking.demo_turns[0].text == "#水王 月榜"

    merge = next(feature for feature in bundle.index if feature.slug == "merge-confirm")
    admin = next(
        feature for feature in bundle.index if feature.slug == "admin-maintenance"
    )
    assert merge.permission == Permission.GROUP_ADMIN
    assert admin.permission == Permission.SUPERUSER
    assert "#water ..." not in admin.trigger
    assert "#water season delete <season_id>" in admin.trigger


def test_match_feature_supports_exact_fuzzy_ambiguous_and_not_found(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试 README 解析。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一个功能。
- `beta` Beta 功能: 第二个功能。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一个功能。
- 别名: 公共, alpha-one
- 指令: `#alpha`
#### 说明
alpha
#### 前置条件
无
#### 完整流程
流程 alpha
#### 失败情况
无

### `beta` Beta 功能
- 摘要: 第二个功能。
- 别名: 公共, beta-two
- 指令: `#beta`
#### 说明
beta
#### 前置条件
无
#### 完整流程
流程 beta
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
    )

    exact = match_feature(bundle.index, "alpha")
    fuzzy = match_feature(bundle.index, "Alpha")
    ambiguous = match_feature(bundle.index, "公共")
    not_found = match_feature(bundle.index, "missing")

    assert exact.status == "matched"
    assert exact.feature is not None
    assert exact.feature.slug == "alpha"

    assert fuzzy.status == "matched"
    assert fuzzy.feature is not None
    assert fuzzy.feature.slug == "alpha"

    assert ambiguous.status == "ambiguous"
    assert {feature.slug for feature in ambiguous.candidates} == {"alpha", "beta"}

    assert not_found.status == "not_found"


def test_split_inline_text_spans_marks_backtick_segments_as_code() -> None:
    spans = split_inline_text_spans("使用 `#study` 或 `#help 词库审核` 查看详情")

    assert [(span.text, span.code) for span in spans] == [
        ("使用 ", False),
        ("#study", True),
        (" 或 ", False),
        ("#help 词库审核", True),
        (" 查看详情", False),
    ]


def test_split_inline_text_spans_supports_adjacent_short_code_segments() -> None:
    spans = split_inline_text_spans("直接发送 `y` / `n` 快捷审批")

    assert [(span.text, span.code) for span in spans] == [
        ("直接发送 ", False),
        ("y", True),
        (" / ", False),
        ("n", True),
        (" 快捷审批", False),
    ]


def test_demo_image_renderer_fit_inline_spans_keeps_code_spans_without_backticks() -> (
    None
):
    renderer = DemoImageRenderer()

    fitted = renderer._fit_inline_spans(  # pyright: ignore[reportPrivateUsage]
        (
            InlineTextSpan("指令示例: ", code=False),
            InlineTextSpan("#help 词库审核", code=True),
        ),
        renderer.meta_font,  # pyright: ignore[reportPrivateUsage]
        120,
    )

    assert "".join(span.text for span in fitted).endswith("...")
    assert all("`" not in span.text for span in fitted)


def test_build_readme_docs_renders_feature_text_and_demo_image(tmp_path: Path) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试 feature 渲染。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `flow` 完整流程: 测试 demo 生成。

## 子功能详情
### `flow` 完整流程
- 摘要: 测试 demo 生成。
- 别名: 跑流程
- 指令: `#flow`
#### 说明
这里是说明。
#### 前置条件
这里是前置条件。
#### 完整流程
先发指令，再看返回。
```demo
SYSTEM: 系统提示：准备开始
USER: #flow
BOT: 操作完成
```
#### 失败情况
失败时会提示错误原因。
""".strip(),
        encoding="utf-8",
    )

    message = build_readme_docs(
        source=source,
        name="测试插件",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", feature_query="flow"),
    )

    assert isinstance(message, Message)
    assert "📖 ===== 测试插件 / 完整流程 =====" in str(message)
    assert "功能名: 完整流程" in str(message)
    assert "指令:\n  #flow" in str(message)
    assert "⚠️ 注意事项:" in str(message)
    assert "这里是前置条件。" in str(message)
    assert "这里是说明。" not in str(message)
    assert "先发指令，再看返回。" not in str(message)
    assert "反馈群「427842039」" in str(message)
    assert any(segment.type == "image" for segment in message)


def test_build_readme_docs_can_attach_representative_overview_demo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试 overview demo。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `flow` 完整流程: 测试 overview demo。

## 子功能详情
### `flow` 完整流程
- 摘要: 测试 overview demo。
- 指令: `#flow`
#### 说明
这里是说明。
#### 前置条件
无
#### 完整流程
```demo
USER: #flow
BOT: 操作完成
```
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )

    text_only = build_readme_docs(
        source=source,
        name="测试插件",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN"),
    )
    with_demo = build_readme_docs(
        source=source,
        name="测试插件",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", view="plugin"),
    )

    assert not any(segment.type == "image" for segment in text_only)
    assert "📖 ===== 测试插件 =====" in str(with_demo)
    assert "1. 完整流程" in str(with_demo)
    assert "  #flow" in str(with_demo)
    assert "触发方式" not in str(with_demo)
    assert "权限" not in str(with_demo)
    assert "子功能目录" not in str(with_demo)
    assert "反馈群「427842039」" in str(with_demo)
    assert any(segment.type == "image" for segment in with_demo)


def test_build_readme_docs_filters_features_by_actor_permission() -> None:
    source = Path("src/plugins/water/docs/README.MD")

    normal_message = build_readme_docs(
        source=source,
        name="吹水记录",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", actor_permission=Permission.NORMAL),
    )
    superuser_message = build_readme_docs(
        source=source,
        name="吹水记录",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(
            locale="zh-CN",
            actor_permission=Permission.NORMAL
            | Permission.GROUP_ADMIN
            | Permission.GROUP_OWNER
            | Permission.SUPERUSER,
        ),
    )
    denied_feature_message = build_readme_docs(
        source=source,
        name="吹水记录",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(
            locale="zh-CN",
            feature_query="admin-maintenance",
            actor_permission=Permission.NORMAL,
        ),
    )

    assert "处理矩阵合并建议" not in str(normal_message)
    assert "超管维护命令" not in str(normal_message)
    assert "处理矩阵合并建议" in str(superuser_message)
    assert "超管维护命令" in str(superuser_message)
    assert "无权限查看子功能文档: 超管维护命令" in str(denied_feature_message)
    assert "需要权限: 超级管理员" in str(denied_feature_message)


def test_build_readme_docs_splits_wordbank_normal_and_approval_docs() -> None:
    wordbank_source = Path("src/plugins/wordbank/docs/README.MD")
    approval_source = Path("src/plugins/wordbank_approval/docs/README.MD")

    normal_wordbank_message = build_readme_docs(
        source=wordbank_source,
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", actor_permission=Permission.NORMAL),
    )
    admin_approval_message = build_readme_docs(
        source=approval_source,
        name="词库审核",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
        ctx=DocsRenderContext(
            locale="zh-CN",
            actor_permission=Permission.NORMAL | Permission.GROUP_ADMIN,
        ),
    )
    denied_approval_feature_message = build_readme_docs(
        source=approval_source,
        name="词库审核",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
        ctx=DocsRenderContext(
            locale="zh-CN",
            feature_query="approve",
            actor_permission=Permission.NORMAL,
        ),
    )

    assert "基础添加" in str(normal_wordbank_message)
    assert "待审核词条" not in str(normal_wordbank_message)
    assert "通过审核" not in str(normal_wordbank_message)
    assert "拒绝审核" not in str(normal_wordbank_message)
    assert "词库审核" in str(admin_approval_message)
    assert "待审核词条" in str(admin_approval_message)
    assert "通过审核" in str(admin_approval_message)
    assert "拒绝审核" in str(admin_approval_message)
    assert "无权限查看子功能文档: 通过审核" in str(denied_approval_feature_message)
    assert "需要权限: 群管理员" in str(denied_approval_feature_message)


def test_plugin_docs_readmes_do_not_use_placeholder_commands() -> None:
    readmes = sorted(Path("src").glob("**/docs/README.MD"))

    placeholder_lines: list[str] = []
    for readme in readmes:
        lines = readme.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped.startswith("- 指令:"):
                continue
            if "..." in stripped or "…" in stripped:
                placeholder_lines.append(f"{readme}:{lineno}: {stripped}")

    assert not placeholder_lines, "\n".join(placeholder_lines)


def test_load_representative_demo_bytes_uses_first_available_feature() -> None:
    source = Path("src/plugins/water/docs/README.MD")
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert load_representative_demo_bytes(bundle) is not None


def test_wordbank_and_study_readmes_use_interactive_demos() -> None:
    wordbank_source = Path("src/plugins/wordbank/docs/README.MD")
    study_source = Path("src/plugins/study/docs/README.MD")
    approval_source = Path("src/plugins/wordbank_approval/docs/README.MD")
    wordbank = load_plugin_doc_bundle(
        source=wordbank_source,
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    study = load_plugin_doc_bundle(
        source=study_source,
        default_name="学习模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    approval = load_plugin_doc_bundle(
        source=approval_source,
        default_name="词库审核",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
    )

    add = next(feature for feature in wordbank.index if feature.slug == "add")
    mode = next(feature for feature in wordbank.index if feature.slug == "add-mode")
    passive = next(feature for feature in wordbank.index if feature.slug == "passive")
    reply = next(
        feature for feature in wordbank.index if feature.slug == "reply-shortcut"
    )
    approve = next(feature for feature in approval.index if feature.slug == "approve")
    approval_reply = next(
        feature for feature in approval.index if feature.slug == "approval-reply"
    )
    shortcut = next(feature for feature in study.index if feature.slug == "shortcut")
    legacy_args = next(
        feature for feature in study.index if feature.slug == "legacy-args"
    )
    recall_rewind = next(
        feature for feature in study.index if feature.slug == "recall-rewind"
    )

    assert add.demo_filename == "wordbank-add.png"
    assert len(add.demo_turns) == 15
    assert add.demo_turns[0].text == "#wordbank.add"
    assert "审核文档请查看 #help 词库审核" in add.demo_turns[10].text
    assert "#wordbank add 是这张图喔 [图片]" in add.demo_turns[11].text
    assert "响应: [图片:7]" in add.demo_turns[12].text
    assert "#wordbank add [图片] => 是这张图喔" in add.demo_turns[13].text
    assert "触发: [图片:7]" in add.demo_turns[14].text
    assert mode.demo_filename == "wordbank-add-mode.png"
    assert "fullmatch" in mode.demo_turns[0].text
    assert all(feature.slug != "image" for feature in wordbank.index)
    assert "revoke" in add.failures
    assert "发送取消提示并中止" in add.failures
    assert "连续输错 3 次" in add.failures
    assert "#help 词库审核" in wordbank.summary
    assert "ID: 12" in add.demo_turns[9].text
    assert passive.demo_turns[-1].speaker == "SYSTEM"
    assert "group_recall" in passive.demo_turns[-1].text
    assert reply.demo_filename == "wordbank-reply-shortcut.png"
    assert "词条详情 #12" in reply.demo_turns[3].text
    assert approve.demo_filename == "wordbank_approval-approve.png"
    assert "词条 #12 已通过审核" in approve.demo_turns[1].text
    assert approval_reply.demo_filename == "wordbank_approval-approval-reply.png"
    assert "[回复审批通知] @机器人 y" in approval_reply.demo_turns[1].text
    assert shortcut.demo_filename == "study-shortcut.png"
    assert shortcut.demo_turns[0].text == "#study 晚安 => 做个好梦"
    assert "审核流程请查看 #help 词库审核" in shortcut.demo_turns[2].text
    assert legacy_args.demo_filename == "study-legacy-args.png"
    assert "a f 群公告" in legacy_args.demo_turns[0].text
    assert recall_rewind.demo_filename == "study-recall-rewind.png"
    assert recall_rewind.demo_turns[-1].speaker == "SYSTEM"
    assert "不会起作用" in recall_rewind.demo_turns[-1].text
    for source, bundle in (
        (wordbank_source, wordbank),
        (study_source, study),
        (approval_source, approval),
    ):
        for feature in bundle.index:
            if not feature.demo_turns:
                continue
            demo_path = source.parent / "demos" / feature.demo_filename
            assert demo_path.is_file(), demo_path
            assert demo_path.stat().st_size > 0
    assert load_representative_demo_bytes(wordbank) is not None
    assert load_representative_demo_bytes(study) is not None
    assert load_representative_demo_bytes(approval) is not None


def test_load_plugin_doc_bundle_distinguishes_message_newlines_from_turns(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试 demo 换行。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `flow` 完整流程: 测试 demo 换行。

## 子功能详情
### `flow` 完整流程
- 摘要: 测试 demo 换行。
- 指令: `#flow`
#### 说明
这里是说明。
#### 前置条件
无
#### 完整流程
```demo
USER: #flow
BOT: 第一条消息第一行
第一条消息第二行
BOT: 第二条消息
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
    )

    feature = bundle.index[0]

    assert len(feature.demo_turns) == 3
    assert feature.demo_turns[1].speaker == "BOT"
    assert feature.demo_turns[1].text == "第一条消息第一行\n第一条消息第二行"
    assert feature.demo_turns[2].speaker == "BOT"
    assert feature.demo_turns[2].text == "第二条消息"


def test_build_readme_docs_returns_ambiguous_hint(tmp_path: Path) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
用于测试歧义提示。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一个功能。
- `beta` Beta 功能: 第二个功能。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一个功能。
- 别名: 公共
- 指令: `#alpha`
#### 说明
alpha
#### 前置条件
无
#### 完整流程
流程 alpha
#### 失败情况
无

### `beta` Beta 功能
- 摘要: 第二个功能。
- 别名: 公共
- 指令: `#beta`
#### 说明
beta
#### 前置条件
无
#### 完整流程
流程 beta
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )

    message = build_readme_docs(
        source=source,
        name="测试插件",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", feature_query="公共"),
    )

    assert "子功能查询存在歧义: 公共" in str(message)
    assert "- Alpha 功能 (alpha)" in str(message)
    assert "- Beta 功能 (beta)" in str(message)


def test_audit_demo_layout_detects_no_overlap_for_real_header_case() -> None:
    source = Path("src/plugins/admin/docs/invite/README.MD")
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="邀请管理模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    feature = next(item for item in bundle.index if item.slug == "reply-shortcut")

    assert audit_demo_layout(bundle, feature) == ()


def test_plugin_docs_generate_supports_parallel_workers(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试并发生成 demo。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一个功能。
- `beta` Beta 功能: 第二个功能。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一个功能。
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

### `beta` Beta 功能
- 摘要: 第二个功能。
- 指令: `#beta`
#### 说明
beta
#### 前置条件
无
#### 完整流程
```demo
USER: #beta
BOT: Beta 完成
```
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    monkeypatch.setattr(
        plugin_docs_script,
        "render_demo_png",
        lambda _bundle, feature: f"demo:{feature.slug}".encode(),
    )

    assert plugin_docs_script.generate(workers=2) == 0
    assert (docs_root / "demos" / "sample-alpha.png").read_bytes() == b"demo:alpha"
    assert (docs_root / "demos" / "sample-beta.png").read_bytes() == b"demo:beta"


def test_representative_demo_prefers_collection_image(tmp_path: Path) -> None:
    source = tmp_path / "src" / "plugins" / "sample" / "docs" / "README.MD"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
# 测试插件

## 概览
用于测试合集优先级。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一个功能。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一个功能。
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
    demos_dir = source.parent / "demos"
    demos_dir.mkdir()
    (demos_dir / "sample-alpha.png").write_bytes(b"feature-demo")
    (demos_dir / "sample-collection.png").write_bytes(b"collection-demo")
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert load_representative_demo_bytes(bundle) == b"collection-demo"


def test_plugin_docs_compose_builds_collection_image(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    demos_dir = docs_root / "demos"
    demos_dir.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试合集生成。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一个功能。
- `beta` Beta 功能: 第二个功能。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一个功能。
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

### `beta` Beta 功能
- 摘要: 第二个功能。
- 指令: `#beta`
#### 说明
beta
#### 前置条件
无
#### 完整流程
```demo
USER: #beta
BOT: Beta 完成
```
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )
    Image.new("RGB", (320, 240), "#FFF0F5").save(demos_dir / "sample-alpha.png")
    Image.new("RGB", (320, 180), "#EEF4FF").save(demos_dir / "sample-beta.png")

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )

    assert plugin_docs_script.compose(workers=2, columns=2, thumb_width=240) == 0
    collection_path = demos_dir / "sample-collection.png"
    assert collection_path.is_file()
    with Image.open(collection_path) as collection:
        assert collection.width == 1440
        assert collection.height > 300


def test_collection_tile_keeps_demo_preview_and_metadata(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    demos_dir = docs_root / "demos"
    demos_dir.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试合集卡片结构。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一条说明。

## 子功能详情
### `alpha` Alpha 功能
- 摘要: 第一条说明。
- 指令: `#alpha run`
#### 说明
alpha
#### 前置条件
无
#### 完整流程
```demo
USER: #alpha run
BOT: Alpha 完成
```
#### 失败情况
无
""".strip(),
        encoding="utf-8",
    )
    Image.new("RGB", (320, 240), "#FFF0F5").save(demos_dir / "sample-alpha.png")

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )

    _, jobs = plugin_docs_script.collect_collection_jobs(columns=2, thumb_width=240)
    tile = jobs[0].tiles[0]
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2, thumb_width=240)
    prepared = plugin_docs_script.PreparedCollectionTile(
        index=tile.index,
        title=tile.title,
        slug=tile.slug,
        summary=tile.summary,
        trigger=tile.trigger,
        image=renderer._load_thumbnail(tile.source),  # pyright: ignore[reportPrivateUsage]
    )

    assert prepared.index == 1
    assert prepared.trigger == "#alpha run"
    assert prepared.image.width == 240


def test_collection_thumbnail_crops_to_conversation_panel() -> None:
    source = Path("src/plugins/study/docs/demos/study-shortcut.png")
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2, thumb_width=240)

    with Image.open(source) as original:
        original_size = original.size
    thumb = renderer._load_thumbnail(source)  # pyright: ignore[reportPrivateUsage]

    full_scaled_height = round(original_size[1] * 240 / original_size[0])
    assert thumb.width == 240
    assert thumb.height < full_scaled_height


def test_collection_command_block_uses_distinct_code_chip_palette() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2, thumb_width=240)

    assert renderer.CARD_COMMAND_CODE_BG != renderer.CARD_COMMAND_BG
    assert renderer.CARD_COMMAND_CODE_BORDER is not None


def test_collection_header_layout_stays_within_reasonable_height() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2, thumb_width=700)

    layout = renderer._measure_header_layout(  # pyright: ignore[reportPrivateUsage]
        title="学习模块",
        summary=(
            "学习模块保留传统 `study` / `学习` 快捷入口，用于把常用词条提交到 "
            "`wordbank`。查询、审核、删除、投票删除、恢复、回复式词条管理和"
            "被动匹配都由 `wordbank` 负责。"
        ),
        width=1978,
    )

    assert layout.panel_height < 340
    assert layout.right_x + layout.right_width <= layout.panel_right - 8


def test_plugin_docs_build_runs_generate_compose_and_validate_in_order(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, int | None]]] = []

    def fake_generate(*, workers: int | None = None) -> int:
        calls.append(("generate", {"workers": workers}))
        return 0

    def fake_compose(
        *,
        workers: int | None = None,
        columns: int = 2,
        thumb_width: int = 620,
    ) -> int:
        calls.append(
            (
                "compose",
                {
                    "workers": workers,
                    "columns": columns,
                    "thumb_width": thumb_width,
                },
            )
        )
        return 0

    def fake_validate() -> int:
        calls.append(("validate", {}))
        return 0

    monkeypatch.setattr(plugin_docs_script, "generate", fake_generate)
    monkeypatch.setattr(plugin_docs_script, "compose", fake_compose)
    monkeypatch.setattr(plugin_docs_script, "validate", fake_validate)

    assert plugin_docs_script.build(workers=3, columns=4, thumb_width=360) == 0
    assert calls == [
        ("generate", {"workers": 3}),
        ("compose", {"workers": 3, "columns": 4, "thumb_width": 360}),
        ("validate", {}),
    ]


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
