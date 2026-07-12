# 图片搜索

## 概览
图来！🫳

## 权限与触发
- 触发方式: 指令触发
- 权限: 普通用户

## 用法
- Help: `picsearch`
- 别名: 搜图
- 指令: `搜图 [saucenao|ascii2d]`
- Demo: picsearch-search.webp

## 说明
一次最多处理 3 张，默认引擎是 `saucenao`。

## 前置条件
同一用户默认有 30 秒冷却。

## 完整流程
```demo
USER: [回复一条图片消息]
搜图
BOT: 正在搜索第 1 张图片，请稍后...
BOT: 第 1 张图片搜索结果：
引擎：saucenao
相似度：89.12
标题：示例标题
作者：示例作者
链接：https://example.com/source
```
