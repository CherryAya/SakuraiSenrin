# Sentry 监控

## 概览
Sentry 主动推送运行时异常

## 权限与触发
- 触发方式: 被动触发
- 权限: 超级管理员

## 用法
- Help: `sentry`
- 别名: sentry, 告警
- 指令: 被动触发
- Demo: sentry-alert.webp
## 说明
插件初始化时会先注册 Sentry SDK，等异常进入 `before_send` 后，再异步通知所有超级管理员。
## 前置条件
需要先配好有效的 `SENTRY_DSN`。
## 完整流程
```demo
SYSTEM: 某个插件抛出了未捕获异常。
BOT: [私聊超管] Sentry 异常告警
Type: ValueError
Value: invalid season id
```

## 失败情况
未配置有效 `SENTRY_DSN`、告警发送失败或通知目标为空时，会记录日志并跳过通知，不阻塞主流程。
