import src.lib.plugin_docs.meta as plugin_docs_meta_module
from tests.test_plugin_docs_support import *


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
    assert ranking.demo_filename == "water-ranking.webp"
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
    assert profile.hero is True
    assert profile.priority == 10
    assert ranking.hero is True
    assert ranking.priority == 30
    assert merge.advanced is True
    assert admin.advanced is True


def test_virtual_plugin_doc_bundle_builds_feature_index() -> None:
    spec = VirtualPluginDocSpec(
        slug="derived.wordbank.sample",
        title="样例目录",
        summary="样例摘要",
        description="样例描述",
        trigger="词条触发 / #help 查询",
        author="SakuraiSenrin",
        version="0.1.0",
        impression_color="#74C0FC",
        features=(
            VirtualFeatureDocSpec(
                slug="alpha",
                title="Alpha",
                summary="第一项",
                trigger="样例1",
                overview="alpha overview",
                preconditions="无",
                failures="无",
                demo_turns=(plugin_docs_module.DocsDemoTurn("USER", "样例1"),),
            ),
        ),
    )

    bundle = build_virtual_plugin_doc_bundle(spec)

    assert bundle.title == "样例目录"
    assert bundle.summary == "样例摘要"
    assert bundle.index[0].slug == "alpha"
    assert bundle.index[0].trigger == "样例1"


def test_load_plugin_doc_bundle_parses_disclosure_metadata(tmp_path: Path) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 测试插件

## 概览
测试 progressive disclosure 元数据。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha: 第一条。
- `beta` Beta: 第二条。

## 子功能详情
### `alpha` Alpha
- 摘要: 第一条。
- Hero: true
- Priority: 12
- 指令: `#alpha`
#### 说明
alpha
#### 前置条件
无
#### 失败情况
无

### `beta` Beta
- 摘要: 第二条。
- Advanced: true
- Priority: 90
- 指令: `#beta`
#### 说明
beta
#### 前置条件
无
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

    alpha = next(feature for feature in bundle.index if feature.slug == "alpha")
    beta = next(feature for feature in bundle.index if feature.slug == "beta")
    assert alpha.hero is True
    assert alpha.priority == 12
    assert alpha.advanced is False
    assert beta.hero is False
    assert beta.advanced is True
    assert beta.priority == 90


def test_load_plugin_doc_bundle_supports_single_page_simple_plugin(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# 简单插件

## 概览
这是一个单页单功能插件。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- 别名: 简单, demo
- 指令: `#simple`
- Demo: simple-main.webp

## 说明
直接展示最终说明。

## 前置条件
无

## 完整流程
```demo
USER: #simple
BOT: OK
```

## 失败情况
无
""".strip(),
        encoding="utf-8",
    )

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="简单插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert bundle.title == "简单插件"
    assert bundle.summary == "这是一个单页单功能插件。"
    assert len(bundle.index) == 1
    feature = bundle.index[0]
    assert feature.slug == "main"
    assert feature.title == "简单插件"
    assert feature.trigger == "#simple"
    assert feature.aliases == ("简单", "demo")
    assert feature.demo_filename == "simple-main.webp"
    assert feature.hero is True
    assert feature.overview == "直接展示最终说明。"
    assert feature.demo_turns[0].text == "#simple"


def test_load_plugin_doc_bundle_preserves_markdown_block_structure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.MD"
    source.write_text(
        """
# Markdown 插件

## 概览
测试正文 markdown 渲染。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- 指令: `#md`

## 说明
第一段说明。

1. 第一步
2. 第二步，带 `inline code`

```bash
echo hello
echo world
```

## 前置条件
- 需要管理员
- 需要配置项

## 失败情况
- 参数错误
- 网络错误
""".strip(),
        encoding="utf-8",
    )

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="Markdown 插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    feature = bundle.index[0]
    assert "1. 第一步" in feature.overview
    assert "2. 第二步，带 `inline code`" in feature.overview
    assert "```bash" in feature.overview
    assert "- 需要管理员" in feature.preconditions
    assert "- 参数错误" in feature.failures


def test_markdown_layout_supports_lists_and_code_blocks() -> None:
    renderer = DemoImageRenderer()
    layout = build_markdown_layout(
        "第一段\n\n1. 第一步\n2. 第二步 `code`\n\n```bash\necho hello\n```",
        max_width=600,
        line_height=renderer._line_height_for_font(renderer.instruction_font),
        indent_px=renderer.COMMAND_INDENT_PX,
        measure_text=lambda value, code: renderer._measure_markdown_text_width(
            value,
            renderer.instruction_font,
            code=code,
        ),
    )

    assert layout.total_height > 0
    assert any(line.bullet == "1." for line in layout.lines if line.kind == "text")
    assert any(line.code for line in layout.lines if line.kind == "code")


def test_real_simple_plugin_readmes_parse_as_single_feature() -> None:
    for source, expected_trigger in (
        (Path("src/plugins/remove/docs/README.MD"), "#remove"),
        (Path("src/plugins/picsearch/docs/README.MD"), "搜图 [saucenao|ascii2d]"),
        (
            Path("src/plugins/community_miaomiao/docs/README.MD"),
            "词条触发",
        ),
        (Path("src/plugins/sentry/docs/README.MD"), "被动触发"),
        (Path("src/plugins/test/docs/README.MD"), "被动触发"),
        (Path("src/hooks/docs/processor/README.MD"), "被动触发"),
        (Path("src/plugins/notice/docs/user/README.MD"), "被动触发"),
    ):
        bundle = load_plugin_doc_bundle(
            source=source,
            default_name="测试",
            default_description="desc",
            trigger=TriggerType.COMMAND,
            permission=Permission.NORMAL,
        )

        assert len(bundle.index) == 1, source
        feature = bundle.index[0]
        assert feature.slug == "main", source
        assert feature.trigger == expected_trigger, source


def test_simple_plugin_readme_can_define_help_query() -> None:
    bundle = load_plugin_doc_bundle(
        source=Path("src/plugins/study/docs/README.MD"),
        default_name="词库模块（传统版）",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert bundle.help_query == "study"


def test_split_features_for_disclosure_prefers_hero_then_non_advanced() -> None:
    features = (
        FeatureDoc(
            slug="hero",
            title="Hero",
            summary="hero",
            aliases=(),
            trigger="#hero",
            permission=Permission.NORMAL,
            demo_filename="hero.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
            hero=True,
            priority=20,
        ),
        FeatureDoc(
            slug="starter",
            title="Starter",
            summary="starter",
            aliases=(),
            trigger="#starter",
            permission=Permission.NORMAL,
            demo_filename="starter.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
            priority=30,
        ),
        FeatureDoc(
            slug="advanced",
            title="Advanced",
            summary="advanced",
            aliases=(),
            trigger="#advanced",
            permission=Permission.NORMAL,
            demo_filename="advanced.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
            advanced=True,
            priority=10,
        ),
    )

    hero_features, advanced_features = split_features_for_disclosure(
        features,
        actor_permission=Permission.NORMAL,
    )

    assert [feature.slug for feature in hero_features] == ["hero", "starter"]
    assert [feature.slug for feature in advanced_features] == ["advanced"]


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


def test_support_note_uses_generic_multi_group_copy() -> None:
    assert plugin_docs_module._support_note("zh-CN") == (
        "如需进一步支持，请联系管理员，或从下方反馈群入口中任选其一加入 💬。"
    )


def test_resolve_support_groups_falls_back_to_env_when_runtime_config_is_unavailable(
    monkeypatch: Any,
) -> None:
    def _raise_config_import(name: str) -> Any:
        if name == "src.config":
            raise ValueError("NoneBot has not been initialized.")
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setenv(
        "HELP_SUPPORT_GROUPS",
        '[{"title":"测试群","group_id":"20002","url":"https://example.com/group"}]',
    )
    monkeypatch.setattr(plugin_docs_meta_module, "import_module", _raise_config_import)

    groups = plugin_docs_meta_module.resolve_support_groups()

    assert len(groups) == 1
    assert groups[0].title == "测试群"
    assert groups[0].group_id == "20002"
    assert groups[0].url == "https://example.com/group"


def test_support_text_block_contains_both_group_links() -> None:
    block = plugin_docs_meta_module.support_text_block("zh-CN")

    assert "反馈与交流群" in block
    assert "凛雪列車" in block
    assert "No Senrin No Life" in block
    assert "群号 1107576103" in block
    assert "群号 729530250" in block
    assert "https://qm.qq.com/q/rnNzj9thG8" in block
    assert "https://qm.qq.com/q/JrIxb24HsI" in block


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
        default_name="词库模块（传统版）",
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
    study_main = next(feature for feature in study.index if feature.slug == "main")

    assert add.demo_filename == "wordbank-add.webp"
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
    assert "ID: 12" in add.demo_turns[9].text
    assert "#help 词库审核" in add.demo_turns[10].text
    assert rank.demo_filename == "wordbank-rank.webp"
    assert rank.demo_turns[0].text == "#苦瓜榜"
    assert "[发送苦瓜榜海报]" in rank.demo_turns[1].text
    assert trigger.demo_filename == "wordbank-trigger.webp"
    assert trigger.demo_turns[0].text == "#wordbank trigger prob 271 0.3"
    assert "重新进入 pending" in trigger.demo_turns[4].text
    assert response.demo_filename == "wordbank-response.webp"
    assert response.demo_turns[0].text == "#wordbank response weight 12 5"
    assert "重新进入 pending" in response.demo_turns[4].text
    assert passive.demo_turns[-1].speaker == "SYSTEM"
    assert "group_recall" in passive.demo_turns[-1].text
    assert reply.demo_filename == "wordbank-reply-shortcut.webp"
    assert "词条详情 #12" in reply.demo_turns[3].text
    assert approve.demo_filename == "wordbank-approval-approve.webp"
    assert "词条 #12 已通过审核" in approve.demo_turns[1].text
    assert approval_reply.demo_filename == "wordbank-approval-approval-reply.webp"
    assert "[回复审批通知] @机器人 y" in approval_reply.demo_turns[1].text
    assert study_main.demo_filename == "study-main.webp"
    assert study_main.demo_turns[0].text == "#study a f 群公告 大家记得看"
    assert "管理员通过前不会触发。" in study_main.demo_turns[2].text
    assert any(
        turn.text == "#study [图片] => 这张图我记住了" for turn in study_main.demo_turns
    )
    assert study_main.demo_turns[-1].speaker == "SYSTEM"
    assert "重做当前步骤" in study_main.demo_turns[-1].text
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
    assert (
        load_representative_demo_bytes(
            approval,
            actor_permission=Permission.NORMAL | Permission.GROUP_ADMIN,
        )
        is not None
    )


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
