# 文档构建脚本

`scripts/build_docs.py` 用于批量生成、拼接和校验插件文档 demo 资源。它面向开发者，不参与 bot 运行时。

脚本当前会扫描：

- `src/plugins/**/docs/README.MD`
- `src/hooks/**/docs/README.MD`

并基于 README 中的 `demo` 流程生成单功能 PNG、合集 PNG，以及结构校验结果。

## 快速开始

最常用的是一次性全跑：

```bash
uv run python scripts/build_docs.py build
```

如果只想拆开执行：

```bash
uv run python scripts/build_docs.py generate
uv run python scripts/build_docs.py compose
uv run python scripts/build_docs.py validate
```

## 子命令

### `build`

顺序执行：

1. `generate`
2. `compose`
3. `validate`

适合改完 README 或 demo 渲染逻辑后做一次完整构建。

示例：

```bash
uv run python scripts/build_docs.py build -j 8 --columns 2 --thumb-width 548
```

### `generate`

根据 README 里的 `demo` 流程生成单个子功能 PNG。

它会输出到各自 `docs/demos/` 目录，例如：

- `src/plugins/wordbank/docs/demos/wordbank-add.png`
- `src/plugins/study/docs/demos/study-guided-flow.png`

示例：

```bash
uv run python scripts/build_docs.py generate -j 8
```

### `compose`

把同一个 README 下的多个子功能 demo 拼成一张合集图。

它依赖 `generate` 已经产出的单图资源，输出文件例如：

- `src/plugins/wordbank/docs/demos/wordbank-collection.png`
- `src/plugins/study/docs/demos/study-collection.png`

示例：

```bash
uv run python scripts/build_docs.py compose -j 8 --columns 2 --thumb-width 548
```

### `validate`

校验 README 和 demo 资源是否完整、可解析、可渲染。

主要检查：

- `概览`、`子功能目录`、`说明`、`前置条件`、`失败情况` 是否存在
- 每个子功能是否带 `demo` 流程
- 对应 demo PNG 是否存在
- collection PNG 是否存在
- demo 布局是否越界或重叠

示例：

```bash
uv run python scripts/build_docs.py validate
```

## 参数说明

### `-j`, `--workers`

并行 worker 数量。

- `build`: 同时传给 `generate` 和 `compose`
- `generate`: 控制单图生成并发
- `compose`: 控制合集图生成并发

常见取值：

- `-j 1`: 串行，适合调试和排查异常
- `-j 8`: 本地常用并发值

### `--columns`

只对 `build` 和 `compose` 生效。

表示每张合集图按几列排版。

- 默认值：`2`
- 值越大：合集图更宽、更密
- 值越小：每张卡片更大，但整体图会更长

### `--thumb-width`

只对 `build` 和 `compose` 生效。

表示拼接合集图时，每张子图缩略图的目标宽度。

- 默认值：`548`
- 值越大：合集图里每张卡更清晰，但整图更大
- 值越小：合集图更紧凑，但可读性会下降

## 典型用法

### 只改了 README 文案或 demo 流程

```bash
uv run python scripts/build_docs.py build -j 8
```

### 正在调试渲染布局

```bash
uv run python scripts/build_docs.py generate -j 1
uv run python scripts/build_docs.py compose -j 1 --columns 2 --thumb-width 548
uv run python scripts/build_docs.py validate
```

### 只想确认仓库当前文档资源是否完整

```bash
uv run python scripts/build_docs.py validate
```

## 输出位置

脚本不会把产物集中写到一个公共目录，而是始终写回对应插件自己的 `docs/demos/`：

- 单功能图：`<plugin>/docs/demos/<feature>.png`
- 合集图：`<plugin>/docs/demos/<plugin>-collection.png`

这样 `#help` 运行时可以直接复用同一份资源。
