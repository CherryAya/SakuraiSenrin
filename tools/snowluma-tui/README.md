# Snowluma TUI

`snowluma-tui` 是一个独立的 Rust 终端子项目，用来在 Linux 主机上管理一套固定的 `snowluma` 运行栈。

## 功能

- 一键启动、停止、重启整套服务
- 单服务启动、停止、重启
- 检查 pid、日志、端口和基础依赖
- 在 TUI 内修改核心配置、日志目录、pid 目录、每服务额外参数和环境变量，并保存到用户配置目录

## 受管服务

按固定顺序管理以下进程：

1. `Xvfb`
2. `fluxbox`
3. `x11vnc`
4. `novnc_proxy`
5. `QQ`
6. `snowluma`

## 运行

```bash
cargo run --manifest-path tools/snowluma-tui/Cargo.toml
```

可选参数：

```bash
cargo run --manifest-path tools/snowluma-tui/Cargo.toml -- --config-dir /path/to/config
```

首次运行会自动生成：

- 配置文件：`~/.config/snowluma-tui/config.toml`
- 状态目录：`~/.local/state/snowluma-tui/`

## 配置编辑格式

- `extra_args.<service>` 使用 JSON 数组格式，例如 `["--flag","value"]`
- `extra_env.<service>` 使用 JSON 对象格式，例如 `{"DISPLAY":":1","FOO":"bar"}`
- `log_dir` 控制日志目录
- `run_dir` 控制 pid / runtime state 目录

## 快捷键

- `q`: 退出 TUI
- `Tab`: 切换页面
- `j` / `k`: 移动选择
- `s` / `x` / `r`: 启动 / 停止 / 重启当前服务
- `S` / `X` / `R`: 启动 / 停止 / 重启全部服务
- `g`: 刷新状态
- `e` 或 `Enter`: 编辑配置项
- `w`: 保存配置
