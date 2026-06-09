from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DocsRenderContext,
    audit_demo_layout,
    build_readme_docs,
    load_plugin_doc_bundle,
    load_representative_demo_bytes,
    match_feature,
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
    wordbank = load_plugin_doc_bundle(
        source=Path("src/plugins/wordbank/docs/README.MD"),
        default_name="词库模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    study = load_plugin_doc_bundle(
        source=Path("src/plugins/study/docs/README.MD"),
        default_name="学习模块",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    add = next(feature for feature in wordbank.index if feature.slug == "add")
    passive = next(feature for feature in wordbank.index if feature.slug == "passive")
    reply = next(
        feature for feature in wordbank.index if feature.slug == "reply-shortcut"
    )
    shortcut = next(feature for feature in study.index if feature.slug == "shortcut")

    assert add.demo_filename == "wordbank-add.png"
    assert len(add.demo_turns) == 12
    assert add.demo_turns[0].text == "#wordbank.add"
    assert add.demo_turns[-1].text == "做个好梦"
    assert "revoke" in add.failures
    assert "直接中止" in add.failures
    assert "wordbank.help" in wordbank.summary
    assert "ID: 12" in add.demo_turns[9].text
    assert passive.demo_turns[-1].speaker == "SYSTEM"
    assert "group_recall" in passive.demo_turns[-1].text
    assert reply.demo_filename == "wordbank-reply-shortcut.png"
    assert "词条详情 #12" in reply.demo_turns[3].text
    assert shortcut.demo_filename == "study-shortcut.png"
    assert len(shortcut.demo_turns) == 14
    assert shortcut.demo_turns[0].text == "#study"
    assert shortcut.demo_turns[2].text == "a"
    assert shortcut.demo_turns[-1].text == "做个好梦"
    assert "revoke" in shortcut.failures
    assert "直接中止" in shortcut.failures
    assert load_representative_demo_bytes(wordbank) is not None
    assert load_representative_demo_bytes(study) is not None


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
