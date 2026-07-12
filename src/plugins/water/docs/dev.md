# Water Dev

## 总览

`water` 分四层：

- `handlers/`
  接命令和事件
- `services/`
  放业务规则
- `database/`
  放表、查询、仓储
- `renderers/`
  放文本拼装

## 目录说明

### `handlers/`

这里是入口层。

- `query.py`
  统一处理 `水王 ...`
- `admin.py`
  处理 `#water ...` 管理命令
- `rank.py`
  处理自然周期榜
- `achievement.py`
  处理成就查询
- `merge.py`
  处理群矩阵合并确认
- `passive.py`
  处理群消息和入群事件

写法要求：

- 只做参数解析。
- 只做路由选择。
- 不写复杂业务。

### `services/`

这里是业务层。

- `query_router.py`
  统一解析 `水王` 参数
- `season.py`
  管活动赛季
- `rank.py`
  管自然周期榜
- `rank_season.py`
  管活动赛季统计
- `achievement.py`
  管成就规则
- `season_achievement.py`
  管赛季成就视图
- `profile.py`
  组装画像数据
- `settlement.py`
  管日结算
- `matrix_suggestion.py`
  管合并建议

写法要求：

- 规则放这里。
- 流程放这里。
- 统一对外提供简单方法。

### `database/`

这里是数据层。

- `tables.py`
  ORM 表定义
- `types.py`
  写入 payload 类型
- `ops.py`
  SQL 查询和更新
- `repo.py`
  对外仓储接口
- `instances.py`
  数据库实例
- `writers.py`
  缓冲写入

写法要求：

- 优先加到 `repo.py`。
- 需要 SQL 时再进 `ops.py`。
- 不要在 handler 里直接写 SQL。

### `renderers/`

这里放纯文本拼装。

- `season_overview.py`
  赛季列表和概览文案

如果只是格式化输出，优先放这里。

### `docs/`

- `README.MD`
  只放用法
- `dev.md`
  只放开发说明

## 数据流

1. 群消息进 `handlers/`
2. `handlers/` 调 `services/`
3. `services/` 调 `repo/`
4. `repo/` 调 `ops/`
5. `renderers/` 出文本

## 新增功能怎么做

### 新命令

1. 先在 `handlers/` 加入口。
2. 再在 `services/` 加业务方法。
3. 需要文本时加 `renderers/`。
4. 需要存储时改 `database/`。
5. 最后补测试。

### 新赛季功能

1. 先改 `services/season.py`。
2. 再改 `services/query_router.py`。
3. 需要列表文案时改 `renderers/season_overview.py`。
4. 需要持久化时改 `database/tables.py`、`types.py`、`ops.py`、`repo.py`。

### 新画像字段

1. 先改 `database/repo.py` 的取数接口。
2. 再改 `services/profile.py` 的组装逻辑。
3. 最后改 `img.py` 或对应渲染函数。

### 新榜单

1. 先在 `repo.py` 加聚合接口。
2. 再在 `services/` 加统计服务。
3. 如需输出，再补 `renderers/`。

## 测试

- 事件测试优先用 `nonebug`。
- 命令测试尽量走 matcher。
- 服务测试可直接测函数。
- 仓储测试主要测边界和查询结果。

## 约定

- `季榜` 只表示自然季度。
- `赛季` 只表示活动窗口。
- `水王 赛季` 默认查当前全部活动赛季。
- 文案保持短。
- 代码改动保持小。
