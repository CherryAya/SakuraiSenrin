import json

from tests.test_plugin_docs_support import *


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
        lambda _bundle, feature, *, generated_at=None: (
            b"RIFFdemoWEBP" + feature.slug.encode()
        ),
    )

    assert plugin_docs_script.generate(workers=2) == 0
    assert (docs_root / "demos" / "sample-alpha.webp").read_bytes() == (
        b"RIFFdemoWEBPalpha"
    )
    assert (docs_root / "demos" / "sample-beta.webp").read_bytes() == (
        b"RIFFdemoWEBPbeta"
    )


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
    (demos_dir / "sample-alpha.webp").write_bytes(b"feature-demo")
    (demos_dir / "sample-collection.webp").write_bytes(b"collection-demo")
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert load_representative_demo_bytes(bundle) == b"collection-demo"


def test_representative_demo_can_skip_collection_for_simple_leaf(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "plugins" / "sample" / "docs" / "README.MD"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
# 测试插件

## 概览
用于测试简单叶子代表图回退。

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
    (demos_dir / "sample-alpha.webp").write_bytes(b"feature-demo")
    (demos_dir / "sample-collection.webp").write_bytes(b"collection-demo")
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert (
        load_representative_demo_bytes(
            bundle,
            prefer_collection=False,
        )
        == b"feature-demo"
    )


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
- `gamma` Gamma 功能: 第三个功能。

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

### `gamma` Gamma 功能
- 摘要: 第三个功能。
- Advanced: true
- 指令: `#gamma`
#### 说明
gamma
#### 前置条件
无
#### 完整流程
```demo
USER: #gamma
BOT: Gamma 完成
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
    collection_path = demos_dir / "sample-collection.webp"
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
- `beta` Beta 功能: 第二条说明。

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

### `beta` Beta 功能
- 摘要: 第二条说明。
- Advanced: true
- 指令: `#beta run`
#### 说明
beta
#### 前置条件
无
#### 完整流程
```demo
USER: #beta run
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

    _, jobs = plugin_docs_script.collect_collection_jobs(columns=2)
    tile = jobs[0].tiles[0]
    renderer = plugin_docs_script.DemoCollectionRenderer(columns=2)
    prepared = renderer._prepare_tile(  # pyright: ignore[reportPrivateUsage]
        tile,
        renderer._card_width(2),  # pyright: ignore[reportPrivateUsage]
    )

    assert prepared.index == 1
    assert prepared.trigger == "#alpha run"
    assert prepared.demo_help == "#help 测试插件 alpha"
    assert prepared.trigger_layout.lines
    assert prepared.demo_layout.lines
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
- `beta` Beta 功能: 第二条说明。

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

### `beta` Beta 功能
- 摘要: 第二条说明。
- Advanced: true
- 指令: `#beta run`
#### 说明
beta
#### 前置条件
无
#### 完整流程
```demo
USER: #beta run
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

    _, jobs = plugin_docs_script.collect_collection_jobs(columns=2)

    assert len(jobs) == 1
    assert jobs[0].tiles[0].trigger == "#alpha run"


def test_simple_leaf_docs_do_not_create_collection_jobs(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试简单插件不再生成合集图。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- 别名: 样例
- 指令: `#sample`
- Demo: sample-main.webp
## 说明
alpha
## 前置条件
无
## 完整流程
```demo
USER: #sample
BOT: Sample 完成
```
## 失败情况
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

    assert jobs == ()


def test_validate_accepts_renderable_docs_without_demo_png_files(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试 validate 不再依赖已落盘 PNG。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 子功能目录
- `alpha` Alpha 功能: 第一条说明。
- `beta` Beta 功能: 第二条说明。

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

### `beta` Beta 功能
- 摘要: 第二条说明。
- 指令: `#beta run`
#### 说明
beta
#### 前置条件
无
#### 完整流程
```demo
USER: #beta run
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

    assert plugin_docs_script.validate() == 0


def test_validate_reuses_single_feature_render_pass(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    docs_root = tmp_path / "src" / "plugins" / "sample" / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试 validate 不再重复走 feature render/audit。

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
    plugin_docs_script._reset_caches()

    render_calls = {"with_audit": 0, "render": 0}

    def fake_render_with_audit(
        bundle: Any,
        feature: Any,
        *,
        generated_at: Any = None,
    ) -> tuple[bytes, tuple[str, ...]]:
        _ = (bundle, feature, generated_at)
        render_calls["with_audit"] += 1
        return b"RIFFfakeWEBP", ()

    def fail_render(*args: Any, **kwargs: Any) -> bytes:
        _ = (args, kwargs)
        render_calls["render"] += 1
        raise AssertionError("validate should not call render_demo_png directly")

    monkeypatch.setattr(
        plugin_docs_script,
        "render_demo_png_with_audit",
        fake_render_with_audit,
    )
    monkeypatch.setattr(plugin_docs_script, "render_demo_png", fail_render)

    assert plugin_docs_script.validate(workers=1) == 0
    assert render_calls == {"with_audit": 1, "render": 0}


def test_plugin_docs_build_runs_generate_compose_and_validate_in_order(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, int | None]]] = []

    def fake_generate(
        *,
        workers: int | None = None,
        profile: bool = False,
        profile_top: int = 10,
        reset_caches: bool = True,
    ) -> int:
        calls.append(
            (
                "generate",
                {
                    "workers": workers,
                    "profile": int(profile),
                    "profile_top": profile_top,
                    "reset_caches": int(reset_caches),
                },
            )
        )
        return 0

    def fake_compose(
        *,
        workers: int | None = None,
        columns: int = 2,
        profile: bool = False,
        profile_top: int = 10,
        reset_caches: bool = True,
    ) -> int:
        calls.append(
            (
                "compose",
                {
                    "workers": workers,
                    "columns": columns,
                    "profile": int(profile),
                    "profile_top": profile_top,
                    "reset_caches": int(reset_caches),
                },
            )
        )
        return 0

    def fake_validate(
        *,
        workers: int | None = None,
        profile: bool = False,
        profile_top: int = 10,
        reset_caches: bool = True,
    ) -> int:
        calls.append(
            (
                "validate",
                {
                    "workers": workers,
                    "profile": int(profile),
                    "profile_top": profile_top,
                    "reset_caches": int(reset_caches),
                },
            )
        )
        return 0

    def fake_build_help_assets(
        *,
        workers: int | None = None,
        profile: bool = False,
        profile_top: int = 10,
        reset_caches: bool = True,
    ) -> int:
        calls.append(
            (
                "build_help_assets",
                {
                    "workers": workers,
                    "profile": int(profile),
                    "profile_top": profile_top,
                    "reset_caches": int(reset_caches),
                },
            )
        )
        return 0

    monkeypatch.setattr(plugin_docs_script, "generate", fake_generate)
    monkeypatch.setattr(plugin_docs_script, "compose", fake_compose)
    monkeypatch.setattr(plugin_docs_script, "build_help_assets", fake_build_help_assets)
    monkeypatch.setattr(plugin_docs_script, "validate", fake_validate)

    assert (
        plugin_docs_script.build(
            workers=3,
            columns=4,
            profile=True,
            profile_top=7,
        )
        == 0
    )
    assert calls == [
        (
            "generate",
            {
                "workers": 3,
                "profile": 1,
                "profile_top": 7,
                "reset_caches": 0,
            },
        ),
        (
            "compose",
            {
                "workers": 3,
                "columns": 4,
                "profile": 1,
                "profile_top": 7,
                "reset_caches": 0,
            },
        ),
        (
            "build_help_assets",
            {
                "workers": 3,
                "profile": 1,
                "profile_top": 7,
                "reset_caches": 0,
            },
        ),
        (
            "validate",
            {
                "workers": 3,
                "profile": 1,
                "profile_top": 7,
                "reset_caches": 0,
            },
        ),
    ]


def test_build_parser_accepts_profile_options() -> None:
    args = plugin_docs_script.build_parser().parse_args(
        [
            "build",
            "-j",
            "4",
            "--columns",
            "3",
            "--profile",
            "--profile-top",
            "6",
        ]
    )

    assert args.action == "build"
    assert args.workers == 4
    assert args.columns == 3
    assert args.profile is True
    assert args.profile_top == 6


def test_validate_parser_accepts_worker_and_profile_options() -> None:
    args = plugin_docs_script.build_parser().parse_args(
        [
            "validate",
            "-j",
            "5",
            "--profile",
            "--profile-top",
            "4",
        ]
    )

    assert args.action == "validate"
    assert args.workers == 5
    assert args.profile is True
    assert args.profile_top == 4


def test_build_profile_emits_phase_summary(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setattr(plugin_docs_script, "generate", lambda **kwargs: 0)
    monkeypatch.setattr(plugin_docs_script, "compose", lambda **kwargs: 0)
    monkeypatch.setattr(plugin_docs_script, "build_help_assets", lambda **kwargs: 0)
    monkeypatch.setattr(plugin_docs_script, "validate", lambda **kwargs: 0)

    assert plugin_docs_script.build(profile=True, profile_top=3) == 0

    captured = capsys.readouterr().out
    assert "[profile:build.phase] " in captured
    assert "[profile:build.phase:summary]" in captured
    assert "[profile:build.phase:top]" in captured


def test_render_feature_deep_dive_prefers_manifest_backed_local_asset(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        plugin_docs_module.ProgressiveDisclosureRenderer,
        "render_with_support_strip",
        lambda self, image_bytes, **kwargs: image_bytes,
    )
    source = tmp_path / "src" / "plugins" / "sample" / "docs" / "README.MD"
    demos_dir = source.parent / "demos"
    demos_dir.mkdir(parents=True)
    source.write_text(
        """
# 测试插件

## 概览
用于测试静态图优先。

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
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    node = load_doc_node(
        source=source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    feature = bundle.index[0]
    (demos_dir / "sample-alpha.webp").write_bytes(b"local-static")
    (demos_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "locale": "zh-CN",
                "targets": {
                    f"feature:{node.slug}:{feature.slug}": {
                        "normal": "sample-alpha.webp",
                        "group_admin": "sample-alpha.webp",
                        "group_owner": "sample-alpha.webp",
                        "superuser": "sample-alpha.webp",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        plugin_docs_module.render_feature_deep_dive(node, feature, locale="zh-CN")
        == b"local-static"
    )


def test_build_help_assets_dedupes_identical_profile_renders(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    help_docs = tmp_path / "src" / "plugins" / "help" / "docs"
    help_docs.mkdir(parents=True)
    (help_docs / "README.MD").write_text(
        """
# 帮助中心

## 概览
帮助首页。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户
""".strip(),
        encoding="utf-8",
    )
    sample_docs = tmp_path / "src" / "plugins" / "sample" / "docs"
    sample_docs.mkdir(parents=True)
    (sample_docs / "README.MD").write_text(
        """
# 测试插件

## 概览
用于测试 help-assets 去重。

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
    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    plugin_docs_script._reset_caches()
    render_counts = {
        "dashboard": 0,
        "sample_feature": 0,
    }

    def fake_render_help_dashboard(*args: Any, **kwargs: Any) -> bytes:
        render_counts["dashboard"] += 1
        return b"RIFFfakeWEBP"

    def fake_render_feature_deep_dive(*args: Any, **kwargs: Any) -> bytes:
        node = args[0]
        if getattr(node, "slug", "") == "sample":
            render_counts["sample_feature"] += 1
        return b"RIFFfakeWEBP"

    monkeypatch.setattr(
        plugin_docs_script,
        "render_help_dashboard",
        fake_render_help_dashboard,
    )
    monkeypatch.setattr(
        plugin_docs_script,
        "render_feature_deep_dive",
        fake_render_feature_deep_dive,
    )

    assert plugin_docs_script.build_help_assets(workers=4) == 0
    assert render_counts["dashboard"] == 1
    assert render_counts["sample_feature"] == 1


def test_ensure_help_support_qr_asset_writes_double_qr_png_from_support_groups(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    rendered_payloads: list[str] = []

    class _Group:
        def __init__(self, *, title: str, group_id: str, url: str) -> None:
            self.title = title
            self.group_id = group_id
            self.url = url

    groups = (
        _Group(title="群一", group_id="10001", url="https://example.com/one"),
        _Group(title="群二", group_id="10002", url="https://example.com/two"),
        _Group(title="群三", group_id="10003", url="https://example.com/three"),
    )

    def fake_render_support_qr_image(
        payload: str,
        *,
        pixels: int = plugin_docs_script.SUPPORT_QR_IMAGE_SIZE,
    ) -> Image.Image:
        rendered_payloads.append(payload)
        color = (255, 0, 0, 255) if payload.endswith("/one") else (0, 128, 255, 255)
        return Image.new("RGBA", (pixels, pixels), color)

    monkeypatch.setattr(plugin_docs_script, "resolve_support_groups", lambda: groups)
    monkeypatch.setattr(
        plugin_docs_script,
        "_render_support_qr_image",
        fake_render_support_qr_image,
    )

    asset_path, changed = plugin_docs_script.ensure_help_support_qr_asset(root=tmp_path)

    image = Image.open(asset_path).convert("RGBA")

    assert changed is True
    assert asset_path == (
        tmp_path / "src" / "lib" / "assets" / "help-support-qr-double.png"
    )
    assert rendered_payloads == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert image.size == (
        plugin_docs_script.SUPPORT_QR_IMAGE_SIZE * 2
        + plugin_docs_script.SUPPORT_QR_IMAGE_GAP,
        plugin_docs_script.SUPPORT_QR_IMAGE_SIZE,
    )
    assert image.getpixel((8, 8)) == (255, 0, 0, 255)
    assert image.getpixel((image.width - 8, 8)) == (0, 128, 255, 255)


def test_build_help_assets_refreshes_support_qr_asset_before_rendering(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    help_root = tmp_path / "src" / "plugins" / "help" / "docs"
    help_root.mkdir(parents=True)
    (help_root / "README.MD").write_text(
        """
# 帮助中心

## 概览
帮助首页。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "plugins" / "help" / "__init__.py").write_text(
        """
from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name="帮助中心",
    description="desc",
    extra={
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=10,
            source=DOCS_SOURCE,
            slug="help",
        ),
    },
)
""".strip(),
        encoding="utf-8",
    )

    refresh_calls: list[Path | None] = []

    def fake_ensure_help_support_qr_asset(
        *,
        root: Path | None = None,
    ) -> tuple[Path, bool]:
        refresh_calls.append(root)
        return (
            tmp_path / "src" / "lib" / "assets" / "help-support-qr-double.png",
            False,
        )

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    monkeypatch.setattr(
        plugin_docs_script,
        "ensure_help_support_qr_asset",
        fake_ensure_help_support_qr_asset,
    )
    monkeypatch.setattr(
        plugin_docs_script,
        "render_help_dashboard",
        lambda *args, **kwargs: b"RIFFfakeWEBP",
    )
    plugin_docs_script._reset_caches()

    assert plugin_docs_script.build_help_assets(workers=1) == 0
    assert refresh_calls == [tmp_path]


def test_load_doc_context_preserves_declared_docs_category_and_permission(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    plugin_root = tmp_path / "src" / "plugins" / "community_sample"
    docs_root = plugin_root / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "README.MD").write_text(
        """
# 社区样例

## 概览
用于测试静态构建读取声明元数据。

## 权限与触发
- 触发方式: 指令触发
- 权限: 超级用户
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "__init__.py").write_text(
        """
from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name="社区样例",
    description="desc",
    extra={
        "permission": Permission.SUPERUSER,
        "docs": create_docs_meta(
            visible=True,
            category="community",
            order=86,
            source=DOCS_SOURCE,
            slug="community.sample",
        ),
    },
)
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    plugin_docs_script._reset_caches()

    context = plugin_docs_script.load_doc_context(docs_root / "README.MD")

    assert context.node.slug == "community.sample"
    assert context.node.category == "community"
    assert context.node.permission == Permission.SUPERUSER
    assert context.node.module_name == "src.plugins.community_sample"
    assert context.node.plugin_name == "community_sample"


def test_build_help_assets_keeps_distinct_dashboard_variants_per_permission(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    help_root = tmp_path / "src" / "plugins" / "help" / "docs"
    help_root.mkdir(parents=True)
    (help_root / "README.MD").write_text(
        """
# 帮助中心

## 概览
帮助首页。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "plugins" / "help" / "__init__.py").write_text(
        """
from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name="帮助中心",
    description="desc",
    extra={
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=10,
            source=DOCS_SOURCE,
            slug="help",
        ),
    },
)
""".strip(),
        encoding="utf-8",
    )

    admin_root = tmp_path / "src" / "plugins" / "admin" / "docs"
    admin_root.mkdir(parents=True)
    (admin_root / "README.MD").write_text(
        """
# 管理总览

## 概览
仅超级用户可见。

## 权限与触发
- 触发方式: 指令触发
- 权限: 超级用户
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "plugins" / "admin" / "__init__.py").write_text(
        """
from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name="管理总览",
    description="desc",
    extra={
        "permission": Permission.SUPERUSER,
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=10,
            source=DOCS_SOURCE,
            slug="admin",
            kind="overview",
        ),
    },
)
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    plugin_docs_script._reset_caches()

    assert plugin_docs_script.build_help_assets(workers=1) == 0

    manifest = json.loads((help_root / "demos" / "manifest.json").read_text("utf-8"))
    dashboard_variants = manifest["targets"]["dashboard:index"]

    assert dashboard_variants["normal"] == "help-index.webp"
    assert dashboard_variants["superuser"] != dashboard_variants["normal"]


def test_build_help_assets_reuses_feature_filename_when_signatures_match(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    help_root = tmp_path / "src" / "plugins" / "help" / "docs"
    help_root.mkdir(parents=True)
    (help_root / "README.MD").write_text(
        """
# 帮助中心

## 概览
帮助首页。

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- 别名: 首页说明
- 指令: `#help`
- Demo: help-main.webp
## 说明
首页说明。
## 前置条件
无
## 完整流程
```demo
USER: #help
BOT: 帮助首页
```
## 失败情况
无
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "plugins" / "help" / "__init__.py").write_text(
        """
from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name="帮助中心",
    description="desc",
    extra={
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=10,
            source=DOCS_SOURCE,
            slug="help",
        ),
    },
)
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(plugin_docs_script, "ROOT", tmp_path)
    monkeypatch.setattr(
        plugin_docs_script,
        "DOCS_ROOTS",
        (tmp_path / "src" / "plugins",),
    )
    plugin_docs_script._reset_caches()

    assert plugin_docs_script.build_help_assets(workers=1) == 0

    manifest = json.loads((help_root / "demos" / "manifest.json").read_text("utf-8"))
    feature_variants = manifest["targets"]["feature:help:main"]

    assert feature_variants["normal"] == "help-main.webp"
    assert feature_variants["group_admin"] == "help-main.webp"
    assert feature_variants["group_owner"] == "help-main.webp"
    assert feature_variants["superuser"] == "help-main.webp"


def test_render_help_dashboard_prefers_manifest_backed_help_asset_from_explicit_source(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        plugin_docs_module.ProgressiveDisclosureRenderer,
        "render_with_support_strip",
        lambda self, image_bytes, **kwargs: image_bytes,
    )
    help_source = tmp_path / "src" / "plugins" / "help" / "docs" / "README.MD"
    help_demos_dir = help_source.parent / "demos"
    help_demos_dir.mkdir(parents=True)
    help_source.write_text("# 帮助中心\n", encoding="utf-8")

    other_source = tmp_path / "src" / "plugins" / "sample" / "docs" / "README.MD"
    other_source.parent.mkdir(parents=True)
    other_source.write_text(
        """
# 测试插件

## 概览
用于测试首页静态图优先。

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
    node = load_doc_node(
        source=other_source,
        default_name="测试插件",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    (help_demos_dir / "help-index.webp").write_bytes(b"help-static")
    (help_demos_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "locale": "zh-CN",
                "targets": {
                    "dashboard:index": {
                        "normal": "help-index.webp",
                        "group_admin": "help-index.webp",
                        "group_owner": "help-index.webp",
                        "superuser": "help-index.webp",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        plugin_docs_module.render_help_dashboard(
            (
                plugin_docs_module.HelpDashboardSection(
                    kind="developer",
                    title="官方功能扩展",
                    nodes=(node,),
                ),
            ),
            locale="zh-CN",
            source_path=help_source,
        )
        == b"help-static"
    )
