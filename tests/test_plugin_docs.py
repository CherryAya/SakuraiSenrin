from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DocsRenderContext,
    audit_demo_layout,
    build_readme_docs,
    load_plugin_doc_bundle,
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
    assert "===== 测试插件 / 完整流程 =====" in str(message)
    assert "这里是说明。" in str(message)
    assert "先发指令，再看返回。" in str(message)
    assert any(segment.type == "image" for segment in message)


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
