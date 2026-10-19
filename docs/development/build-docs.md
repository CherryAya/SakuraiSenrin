# 帮助文档构建

修改插件或 hook 的 `docs/README.MD` 后，用 [build_docs.py](../../scripts/build_docs.py) 重建帮助图片与资产，或校验文档结构和渲染布局；参数以脚本 `--help` 为准。

```bash
uv run python scripts/build_docs.py build
uv run python scripts/build_docs.py validate
```

字体、绘图素材与生成图片统一通过 R2 分发，Git 只保留文档、目标映射
`demos/manifest.json` 和 `docs-assets.lock.json`，无需 Git LFS。
资源包括 `data/font/` 下的两份字体、`src/lib/assets/` 下的头像、立绘及支持二维码，
以及各插件的预构建帮助图片。运行数据库、用户上传内容和备份继续由各自的数据服务管理。

在本机配置当前环境的 `R2_*` 后发布：

```bash
uv run python scripts/docs_assets.py publish
```

首次克隆后先执行下方的 `pull`，补齐字体与绘图素材，再修改源码和构建。
已有的字体和素材需要更新时，直接替换本地对应文件，再执行 `publish`。

该命令先构建并验证文档，再以 SHA-256 为对象名上传完整资源包，回读校验成功后才更新
资源锁定清单。随后提交文档、目标映射和锁定清单。不同环境使用不同 bucket 时，
需将相同构建包分别发布到各 bucket，确保生产配置可以读取清单指定的对象。

图片时间采用构建输入最后一次 Git 提交的 UTC 时间；无 Git 时使用 Unix epoch，
也可以通过 `SOURCE_DATE_EPOCH` 显式固定。构建输入及输出不变时沿用现有构建缓存。

生产环境拉取代码后启动 `bot.py`：配置初始化后先同步完整资源包，再检查安装字体，最后加载插件。
本地文件全部匹配锁定清单时不联网；缺失或损坏时从本地包缓存或 R2 恢复。
资源包完整校验后才安装，资源不完整且无法恢复时终止启动，不在生产构建图片。
源码版本通过文本输入和清单中锁定的素材摘要验证；空字体/图片目录也能下载，
缺少字体和损坏的字体均可自动恢复，字体指针文件不能用于发布。

主动检查、修复或重新下载当前代码锁定的版本：

```bash
uv run python scripts/docs_assets.py status
uv run python scripts/docs_assets.py pull
uv run python scripts/docs_assets.py pull --force
```

本地包缓存位于 `output/docs-assets/`，保留当前包和最近两个包。远端清理先预览：

```bash
uv run python scripts/docs_assets.py prune --keep <仍在部署的版本SHA256>
```

远端保留当前锁定版本、Git 清单历史中的最近两个版本及所有 `--keep` 指定版本。
明确检查各环境部署版本后，添加 `--apply` 才执行删除；不足两个回滚版本时拒绝删除。
旧版本产物删除后，对应代码回滚需在本机重新构建发布。生产更新代码后需要重启以加载新代码。

管理员文档须显式设置 `docs.permission`，实际鉴权在 matcher/service 中执行；图片复用 [demo_theme.py](../../src/lib/demo_theme.py)，文字遵守 [i18n 规范](message-delivery.md)，并按聊天窗口宽度检查换行、遮挡、裁切和可读性。
