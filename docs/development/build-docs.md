# 帮助文档构建

修改插件或 hook 的 `docs/README.MD` 后，用 [build_docs.py](../../scripts/build_docs.py) 重建帮助图片与资产，或校验文档结构和渲染布局；参数以脚本 `--help` 为准。

```bash
uv run python scripts/build_docs.py build
uv run python scripts/build_docs.py validate
```

管理员文档须显式设置 `docs.permission`，实际鉴权在 matcher/service 中执行；图片复用 [demo_theme.py](../../src/lib/demo_theme.py)，文字遵守 [i18n 规范](message-delivery.md)，并按聊天窗口宽度检查换行、遮挡、裁切和可读性。
