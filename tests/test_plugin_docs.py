from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image

import scripts.build_docs as plugin_docs_script
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import (
    BASE_THEME,
    DEFAULT_IMPRESSION_COLOR,
    build_demo_theme,
    normalize_hex_color,
)
from src.lib.i18n.runtime import tr
import src.lib.plugin_docs as plugin_docs_module
from src.lib.plugin_docs import (
    DemoImageRenderer,
    DocsRenderContext,
    InlineTextSpan,
    audit_demo_layout,
    build_command_layout,
    build_doc_tree,
    build_readme_docs,
    create_docs_meta,
    load_doc_node,
    load_plugin_doc_bundle,
    load_representative_demo_bytes,
    match_feature,
    render_doc_node_overview,
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
    assert len(bundle.index) == 8

    profile = next(feature for feature in bundle.index if feature.slug == "profile")
    ranking = next(feature for feature in bundle.index if feature.slug == "ranking")
    assert profile.trigger == "#我有多水"
    assert ranking.title == "查看周期榜单"
    assert "榜单" in ranking.aliases
    assert ranking.demo_filename == "water-ranking.png"
    assert ranking.demo_turns[0].speaker == "USER"
    assert ranking.demo_turns[0].text == "#水王"

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


def test_support_note_falls_back_to_env_when_runtime_config_is_unavailable(
    monkeypatch: Any,
) -> None:
    def _raise_config_import(name: str) -> Any:
        if name == "src.config":
            raise ValueError("NoneBot has not been initialized.")
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setenv("MAIN_GROUP_ID", "20002")
    monkeypatch.setattr(plugin_docs_module, "import_module", _raise_config_import)

    assert plugin_docs_module._support_note("zh-CN") == (
        "如需进一步支持，请联系管理员，或加入反馈群「20002」💬。"
    )


def test_load_plugin_doc_bundle_preserves_inline_backticks_in_meta_value() -> None:
    source = Path("src/plugins/wordbank/docs/README.MD")

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    reply_shortcut = next(
        feature for feature in bundle.index if feature.slug == "reply-shortcut"
    )

    assert (
        reply_shortcut.trigger
        == "回复机器人词库自动回复并发送 `info` / `history` / `del` / `rst` / "
        "`trigger prob` / `trigger set` / `response weight` / `response set`"
    )


def test_demo_image_renderer_fit_inline_spans_keeps_code_spans_without_backticks() -> (
    None
):
    renderer = DemoImageRenderer()
    trigger_prefix = tr("zh-CN", "docs.feature.trigger_example", command="")

    fitted = renderer._fit_inline_spans(  # pyright: ignore[reportPrivateUsage]
        (
            InlineTextSpan(trigger_prefix, code=False),
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
    assert "反馈群「10001」" in str(message)
    assert any(segment.type == "image" for segment in message)


def test_build_readme_docs_formats_multi_section_commands_for_help_output() -> None:
    shortcut_label = tr("zh-CN", "docs.feature.shortcuts")
    message = build_readme_docs(
        source=Path("src/plugins/water/docs/README.MD"),
        name="吹水记录",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", feature_query="ranking"),
    )

    rendered = str(message)
    expected_prefix = f"指令:\n  #水王 / #水王 <主体> <范围> <时间>\n  {shortcut_label}"
    assert expected_prefix in rendered
    assert "    #今日水王 / #本周水王 / #本月水王 / #本季水王" in rendered
    assert "    #今日群榜 / ..." in rendered
    assert "    #今日矩阵榜 / ..." in rendered


def test_render_doc_node_overview_formats_multi_section_commands() -> None:
    node = load_doc_node(
        source=Path("src/plugins/water/docs/README.MD"),
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    message = render_doc_node_overview(
        node,
        locale="zh-CN",
        include_demo=False,
        actor_permission=Permission.NORMAL,
    )

    rendered = str(message)
    assert "3. 查看周期榜单" in rendered
    assert "  #水王 / #水王 <主体> <范围> <时间>" in rendered
    assert f"  {tr('zh-CN', 'docs.feature.shortcuts')}" in rendered
    assert "    #今日矩阵群榜 / ..." in rendered


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
    assert "反馈群「10001」" in str(with_demo)
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
    assert "未找到子功能文档: admin-maintenance" in str(denied_feature_message)


def test_build_readme_docs_supports_wordbank_and_approval_docs() -> None:
    wordbank_source = Path("src/plugins/wordbank/docs/README.MD")
    approval_source = Path("src/plugins/wordbank/docs/approval/README.MD")

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
    assert "未找到子功能文档: approve" in str(denied_approval_feature_message)


def test_build_doc_tree_supports_admin_and_notice_hierarchy() -> None:
    admin_meta = create_docs_meta(
        visible=False,
        category="admin",
        order=10,
        source="src/plugins/admin/docs/README.MD",
        slug="admin",
        kind="overview",
    )
    group_meta = create_docs_meta(
        visible=True,
        category="admin",
        order=110,
        source="src/plugins/admin/docs/group/README.MD",
        slug="admin.group",
        parent_slug="admin",
    )
    notice_meta = create_docs_meta(
        visible=False,
        category="system",
        order=10,
        source="src/plugins/notice/docs/README.MD",
        slug="notice",
        kind="overview",
    )
    invite_meta = create_docs_meta(
        visible=False,
        category="system",
        order=130,
        source="src/plugins/notice/docs/invite/README.MD",
        slug="notice.invite",
        parent_slug="notice",
    )

    admin = load_doc_node(
        source=admin_meta["source"]["readme_path"],
        default_name="管理模块总览",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        docs_meta=admin_meta,
    )
    group = load_doc_node(
        source=group_meta["source"]["readme_path"],
        default_name="群组管理模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        docs_meta=group_meta,
    )
    notice = load_doc_node(
        source=notice_meta["source"]["readme_path"],
        default_name="通知模块总览",
        default_description="desc",
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        docs_meta=notice_meta,
    )
    invite = load_doc_node(
        source=invite_meta["source"]["readme_path"],
        default_name="群组邀请处理",
        default_description="desc",
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        docs_meta=invite_meta,
    )

    tree = build_doc_tree([admin, group, notice, invite])

    assert [node.slug for node in tree.children_of("admin")] == ["admin.group"]
    assert [node.slug for node in tree.children_of("notice")] == ["notice.invite"]


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
    approval_source = Path("src/plugins/wordbank/docs/approval/README.MD")
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
    rank = next(feature for feature in wordbank.index if feature.slug == "rank")
    trigger = next(feature for feature in wordbank.index if feature.slug == "trigger")
    response = next(feature for feature in wordbank.index if feature.slug == "response")
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
    assert all(feature.slug != "add-mode" for feature in wordbank.index)
    assert all(feature.slug != "image" for feature in wordbank.index)
    assert "revoke" in add.failures
    assert "发送取消提示并中止" in add.failures
    assert "连续输错 3 次" in add.failures
    assert "#help 词库审核" in wordbank.summary
    assert "ID: 12" in add.demo_turns[9].text
    assert rank.demo_filename == "wordbank-rank.png"
    assert rank.demo_turns[0].text == "#苦瓜榜"
    assert "[发送苦瓜榜海报]" in rank.demo_turns[1].text
    assert trigger.demo_filename == "wordbank-trigger.png"
    assert trigger.demo_turns[0].text == "#wordbank trigger prob 271 0.3"
    assert "重新进入 pending" in trigger.demo_turns[4].text
    assert response.demo_filename == "wordbank-response.png"
    assert response.demo_turns[0].text == "#wordbank response weight 12 5"
    assert "重新进入 pending" in response.demo_turns[4].text
    assert passive.demo_turns[-1].speaker == "SYSTEM"
    assert "group_recall" in passive.demo_turns[-1].text
    assert reply.demo_filename == "wordbank-reply-shortcut.png"
    assert "词条详情 #12" in reply.demo_turns[3].text
    assert approve.demo_filename == "wordbank-approval-approve.png"
    assert "词条 #12 已通过审核" in approve.demo_turns[1].text
    assert approval_reply.demo_filename == "wordbank-approval-approval-reply.png"
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
        lambda _bundle, feature, *, generated_at=None: f"demo:{feature.slug}".encode(),
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
    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )

    assert plugin_docs_script.compose(workers=2, columns=2) == 0
    collection_path = demos_dir / "sample-collection.png"
    assert collection_path.is_file()
    with Image.open(collection_path) as collection:
        assert collection.width == 1280
        assert collection.height > 300


def test_collection_tile_is_text_only_and_keeps_metadata(
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
    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )

    _, jobs = plugin_docs_script.collect_collection_jobs(columns=2)
    tile = jobs[0].tiles[0]
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)
    prepared = renderer._prepare_tile(  # pyright: ignore[reportPrivateUsage]
        tile,
        renderer._card_width(2),  # pyright: ignore[reportPrivateUsage]
    )

    assert prepared.index == 1
    assert prepared.trigger == "#alpha run"
    assert prepared.command_layout.lines
    assert prepared.height > 0


def test_collection_header_layout_stays_within_reasonable_height() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    layout = renderer._measure_header_layout(  # pyright: ignore[reportPrivateUsage]
        title="学习模块",
        summary=(
            "学习模块保留传统 `study` / `学习` 快捷入口，用于把常用词条提交到 "
            "`wordbank`。查询、审核、删除、投票删除、恢复、回复式词条管理和"
            "被动匹配都由 `wordbank` 负责。"
        ),
    )

    assert layout.height < 360
    assert layout.left_x == renderer.OUTER_MARGIN


def test_collection_renderer_keeps_requested_two_column_layout() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    assert renderer._effective_columns(14) == 2  # pyright: ignore[reportPrivateUsage]


def test_build_command_layout_breaks_flags_into_hanging_indent_lines() -> None:
    renderer = DemoImageRenderer()
    palette = plugin_docs_module.CommandPalette(
        root=renderer.theme.indigo_text,
        text=renderer.theme.terminal_text,
        param=renderer.theme.terminal_param,
        flag=renderer.theme.terminal_flag,
    )

    layout = build_command_layout(
        (
            "wordbank add 触发词 => 响应词 --scope "
            "current_group|all_groups|self|private_only "
            "--prob 0.0-1.0 --weight 1-5"
        ),
        max_width=720,
        line_height=renderer._line_height_for_font(renderer.body_font),  # pyright: ignore[reportPrivateUsage]
        indent_px=48,
        measure_text=lambda value: renderer._text_width(value, renderer.body_font),  # pyright: ignore[reportPrivateUsage]
        palette=palette,
    )

    assert [line.kind for line in layout.lines[:6]] == [
        "root",
        "flag",
        "continuation",
        "continuation",
        "flag",
        "flag",
    ]
    assert layout.lines[0].indent_level == 0
    assert layout.lines[1].indent_level == 1
    assert layout.lines[2].indent_level == 2
    assert layout.lines[3].indent_level == 2
    assert layout.lines[4].indent_level == 1
    assert layout.has_guide


def test_build_command_layout_wraps_long_flag_values_with_double_indent() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)
    layout = build_command_layout(
        "wordbank add 触发词 => 响应词 --role owner|admin|member|visitor|reviewer",
        max_width=408,
        line_height=renderer._line_height_for_font(renderer.tile_command_font),  # pyright: ignore[reportPrivateUsage]
        indent_px=48,
        measure_text=lambda value: renderer._text_width(
            value, renderer.tile_command_font
        ),  # pyright: ignore[reportPrivateUsage]
        palette=plugin_docs_module.CommandPalette(
            root=renderer.theme.indigo_text,
            text=renderer.theme.deep,
            param=renderer.theme.terminal_param,
            flag=renderer.theme.terminal_flag,
        ),
    )

    assert [line.kind for line in layout.lines[:4]] == [
        "root",
        "continuation",
        "flag",
        "continuation",
    ]
    assert layout.lines[1].indent_level == 1
    assert layout.lines[2].indent_level == 1
    assert layout.lines[3].indent_level == 2


def test_build_command_layout_splits_root_level_alternatives() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)
    layout = build_command_layout(
        (
            "#admin.invite reject -g <gid> / "
            "#admin.invite reject -f <flag> / "
            "#admin.invite reject --all"
        ),
        max_width=320,
        line_height=renderer._line_height_for_font(renderer.tile_command_font),  # pyright: ignore[reportPrivateUsage]
        indent_px=48,
        measure_text=lambda value: renderer._text_width(
            value, renderer.tile_command_font
        ),  # pyright: ignore[reportPrivateUsage]
        palette=plugin_docs_module.CommandPalette(
            root=renderer.theme.indigo_text,
            text=renderer.theme.deep,
            param=renderer.theme.terminal_param,
            flag=renderer.theme.terminal_flag,
        ),
    )

    assert layout.lines[0].kind == "root"
    assert all(
        line.kind in {"alternative", "flag", "continuation"}
        for line in layout.lines[1:]
    )
    assert any(line.kind == "alternative" for line in layout.lines[1:])
    assert layout.lines[1].indent_level >= 1


def test_build_command_layout_splits_consecutive_inline_code_variants() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)
    layout = build_command_layout(
        "`wordbank trigger prob <group_id> <0.0-1.0>` "
        "`wordbank trigger set <group_id> <新触发内容>`",
        max_width=408,
        line_height=renderer._line_height_for_font(renderer.tile_command_font),  # pyright: ignore[reportPrivateUsage]
        indent_px=48,
        measure_text=lambda value: renderer._text_width(
            value, renderer.tile_command_font
        ),  # pyright: ignore[reportPrivateUsage]
        palette=plugin_docs_module.CommandPalette(
            root=renderer.theme.indigo_text,
            text=renderer.theme.deep,
            param=renderer.theme.terminal_param,
            flag=renderer.theme.terminal_flag,
        ),
    )

    assert layout.lines[0].kind == "root"
    assert any(line.kind == "alternative" for line in layout.lines)
    alternative_line = next(line for line in layout.lines if line.kind == "alternative")
    assert alternative_line.indent_level == 1


def test_collection_jobs_do_not_require_demo_png_files(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试合集任务。

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
    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )

    _, jobs = plugin_docs_script.collect_collection_jobs(columns=2)

    assert len(jobs) == 1
    assert jobs[0].tiles[0].trigger == "#alpha run"


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
    ) -> int:
        calls.append(
            (
                "compose",
                {
                    "workers": workers,
                    "columns": columns,
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

    assert plugin_docs_script.build(workers=3, columns=4) == 0
    assert calls == [
        ("generate", {"workers": 3}),
        ("compose", {"workers": 3, "columns": 4}),
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


def test_demo_theme_has_required_tokens() -> None:
    assert BASE_THEME.page_bg
    assert BASE_THEME.panel_bg
    assert BASE_THEME.accent
    assert BASE_THEME.strong
    assert BASE_THEME.deep
    assert BASE_THEME.hint
    assert BASE_THEME.line
    assert BASE_THEME.shell_bg
    assert BASE_THEME.shell_border
    assert BASE_THEME.user_bubble
    assert BASE_THEME.bot_bubble
    assert BASE_THEME.system_bubble
    assert BASE_THEME.inline_code_bg
    assert BASE_THEME.inline_code_text
    assert BASE_THEME.terminal_flag
    assert BASE_THEME.grid_color
    assert BASE_THEME.decor_color
    assert BASE_THEME.footer_divider
    assert BASE_THEME.standee_anchor_fill


def test_build_demo_theme_uses_dynamic_impression_color_palette() -> None:
    theme = build_demo_theme("#3BC9DB")

    assert theme.accent == "#3BC9DB"
    assert theme.page_bg != theme.accent
    assert theme.bot_bubble == theme.panel_soft_bg
    assert theme.deep != theme.hint
    assert theme.grid_color != theme.accent
    assert theme.standee_anchor_fill != theme.accent


def test_normalize_hex_color_falls_back_to_default() -> None:
    assert normalize_hex_color("not-a-color") == DEFAULT_IMPRESSION_COLOR
    assert normalize_hex_color("#abc") == "#AABBCC"


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

    assert renderer.theme == build_demo_theme("#3BC9DB")
    assert not hasattr(renderer, "PAGE_BG")
    assert not hasattr(renderer, "ACCENT")
    assert not hasattr(renderer, "SHELL_BG")


def test_demo_collection_renderer_uses_theme() -> None:
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)

    assert hasattr(renderer, "theme")
    assert renderer.theme == BASE_THEME
    assert not hasattr(renderer, "PAGE_BG")
    assert not hasattr(renderer, "CARD_BG")
    assert not hasattr(renderer, "TITLE")


def test_demo_theme_layout_constants() -> None:
    assert BASE_THEME.outer_margin == 40
    assert BASE_THEME.shell_radius == 32
    assert BASE_THEME.panel_radius == 28
    assert BASE_THEME.card_radius == 32
    assert BASE_THEME.chip_radius == 20
    assert BASE_THEME.inline_code_radius == 12
    assert BASE_THEME.inline_code_pad_x == 8
    assert BASE_THEME.inline_code_pad_y == 4


def test_demo_theme_showcase_tokens_follow_expected_scale() -> None:
    assert BASE_THEME.canvas_width == 1280
    assert BASE_THEME.hero_top == 64
    assert BASE_THEME.hero_side_padding % 8 == 0
    assert BASE_THEME.hero_bottom_padding % 8 == 0
    assert BASE_THEME.hero_standee_size == 304
    assert BASE_THEME.pill_height == 40
    assert BASE_THEME.section_gap % 8 == 0
    assert BASE_THEME.instruction_padding_x % 8 == 0
    assert BASE_THEME.instruction_padding_y % 8 == 0
    assert BASE_THEME.trigger_padding_x % 8 == 0
    assert BASE_THEME.trigger_padding_y % 8 == 0
    assert BASE_THEME.avatar_size % 8 == 0
    assert BASE_THEME.bubble_padding_x % 8 == 0
    assert BASE_THEME.bubble_padding_y % 8 == 0
    assert BASE_THEME.hero_summary_line_height >= 56
    assert BASE_THEME.bubble_line_height >= 48
    assert BASE_THEME.grid_spacing % 8 == 0
    assert BASE_THEME.footer_height >= 72


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
    assert layout.footer_rect[3] - layout.footer_rect[1] == BASE_THEME.footer_height


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


def test_filter_features_by_permission_normal_user() -> None:
    """验证普通用户只能看到普通用户权限的功能"""
    from src.lib.plugin_docs import (
        FeatureDoc,
        filter_features_by_permission,
    )

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 普通用户只能看到普通权限的功能
    normal_visible = filter_features_by_permission(features, Permission.NORMAL)
    assert len(normal_visible) == 1
    assert normal_visible[0].slug == "public"


def test_filter_features_by_permission_admin_user() -> None:
    """验证管理员可以看到管理员及以下权限的功能"""
    from src.lib.plugin_docs import FeatureDoc, filter_features_by_permission

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 管理员可以看到普通用户和管理员权限的功能
    admin_visible = filter_features_by_permission(features, Permission.GROUP_ADMIN)
    assert len(admin_visible) == 2
    assert {f.slug for f in admin_visible} == {"public", "admin"}


def test_filter_features_by_permission_superuser() -> None:
    """验证超级管理员可以看到所有功能"""
    from src.lib.plugin_docs import FeatureDoc, filter_features_by_permission

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 超级管理员可以看到所有功能
    superuser_visible = filter_features_by_permission(features, Permission.SUPERUSER)
    assert len(superuser_visible) == 3
    assert {f.slug for f in superuser_visible} == {"public", "admin", "superuser"}


def test_can_view_node_respects_permission() -> None:
    """验证节点可见性检查遵守权限"""
    from src.lib.plugin_docs import DocNode, PluginDocBundle, can_view_node

    # 创建一个需要管理员权限的节点
    admin_node = DocNode(
        kind="plugin",
        slug="admin-plugin",
        parent_slug=None,
        category="admin",
        order=1,
        visible=True,
        hidden=False,
        internal=False,
        permission=Permission.GROUP_ADMIN,
        title="管理员插件",
        summary="仅管理员可见",
        description="",
        aliases=(),
        source_path=Path("test"),
        bundle=PluginDocBundle(
            title="测试",
            description="",
            summary="",
            trigger="",
            permission="",
            author="",
            version="",
            impression_color=DEFAULT_IMPRESSION_COLOR,
            index=(),
            source_path=Path("test"),
        ),
    )

    # 普通用户看不到
    assert not can_view_node(admin_node, Permission.NORMAL)

    # 管理员可以看到
    assert can_view_node(admin_node, Permission.GROUP_ADMIN)

    # 超级管理员可以看到
    assert can_view_node(admin_node, Permission.SUPERUSER)
