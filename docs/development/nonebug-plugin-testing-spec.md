# 插件测试

matcher、会话、权限和 Bot API 交互使用 NoneBug 的 `app.test_matcher()` / `app.test_api()`；纯解析、service 和 repository 逻辑使用普通 pytest。

复用 [tests/conftest.py](../../tests/conftest.py) 的隔离配置与临时数据库，覆盖成功、拒绝/非法输入和失败降级，隔离外部 I/O；质量门禁按 [AGENTS.md](../../AGENTS.md) 执行。
