from tests.test_plugin_docs_support import *


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

    rendered = str(message)
    assert isinstance(message, Message)
    assert "测试插件\n完整流程" in rendered
    assert "命令：\n  #flow" in rendered
    assert "说明：" in rendered
    assert "这里是前置条件。" in rendered
    assert "这里是说明。" not in rendered
    assert "先发指令，再看返回。" not in rendered
    assert "从下方反馈群入口中任选其一加入" in rendered
    assert any(segment.type == "image" for segment in message)


def test_build_readme_docs_formats_multi_section_commands_for_help_output() -> None:
    message = build_readme_docs(
        source=Path("src/plugins/water/docs/README.MD"),
        name="吹水记录",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", feature_query="ranking"),
    )

    rendered = str(message)
    assert "命令：\n  #水王 / #水王 <主体> <范围> <时间>" in rendered
    assert "#今日水王" in rendered
    assert "#本周群榜" in rendered
    assert "#本季矩阵榜" in rendered


def test_build_readme_docs_plan_entry_renders_feature_text_and_demo_image(
    tmp_path: Path,
) -> None:
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

    entry = build_readme_docs_plan_entry(
        source=source,
        name="测试插件",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", feature_query="flow"),
    )

    assert isinstance(entry, MessagePlanEntry)
    rendered = render_message_plan_entry(entry)
    assert "测试插件\n完整流程" in str(rendered)
    assert any(segment.type == "image" for segment in rendered)


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
    assert "可用功能：" in rendered
    assert "查看周期榜单" in rendered
    assert "  #水王 / #水王 <主体> <范围> <时间>" in rendered
    assert "反馈与交流群" in rendered
    assert (
        rendered.count(
            "如需进一步支持，请联系管理员，或从下方反馈群入口中任选其一加入 💬。"
        )
        == 1
    )
    assert "群号 1107576103" in rendered


def test_render_doc_node_overview_plan_entry_can_attach_demo() -> None:
    node = load_doc_node(
        source=Path("src/plugins/water/docs/README.MD"),
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    entry = render_doc_node_overview_plan_entry(
        node,
        locale="zh-CN",
        include_demo=True,
        actor_permission=Permission.NORMAL,
    )

    assert isinstance(entry, MessagePlanEntry)
    rendered = render_message_plan_entry(entry)
    assert "可用功能：" in str(rendered)
    assert any(segment.type == "image" for segment in rendered)


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
    assert "测试插件" in str(with_demo)
    assert "完整流程" in str(with_demo)
    assert "  #flow" in str(with_demo)
    assert "触发方式" not in str(with_demo)
    assert "权限" not in str(with_demo)
    assert "子功能目录" not in str(with_demo)
    assert "反馈与交流群" in str(with_demo)
    assert "群号 427842039" in str(with_demo)
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
    assert "处理矩阵合并建议" in str(superuser_message)
    assert "超管维护命令" in str(superuser_message)
    assert "未找到子功能文档: admin-maintenance" in str(denied_feature_message)


def test_build_readme_docs_filters_wordbank_admin_features_by_permission() -> None:
    wordbank_source = Path("src/plugins/wordbank/docs/README.MD")

    normal_wordbank_message = build_readme_docs(
        source=wordbank_source,
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale="zh-CN", actor_permission=Permission.NORMAL),
    )
    admin_wordbank_message = build_readme_docs(
        source=wordbank_source,
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(
            locale="zh-CN",
            actor_permission=Permission.NORMAL | Permission.GROUP_ADMIN,
        ),
    )
    denied_approval_feature_message = build_readme_docs(
        source=wordbank_source,
        name="词库模块",
        description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(
            locale="zh-CN",
            feature_query="pending",
            actor_permission=Permission.NORMAL,
        ),
    )

    assert "基础添加" in str(normal_wordbank_message)
    assert "待审核词条" not in str(normal_wordbank_message)
    assert "通过审核" not in str(normal_wordbank_message)
    assert "拒绝审核" not in str(normal_wordbank_message)
    assert "待审核词条" in str(admin_wordbank_message)
    assert "通过审核" in str(admin_wordbank_message)
    assert "拒绝审核" in str(admin_wordbank_message)
    assert "未找到子功能文档: pending" in str(denied_approval_feature_message)


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
