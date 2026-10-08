"""apscheduler 的类型存根（仅覆盖本仓库用到的调度器成员）。

apscheduler 3.x 未随包发布 py.typed，strict 模式下 pyright 把
``AsyncIOScheduler.scheduled_job`` / ``get_job`` 判为 Unknown，并沿
``@scheduler.scheduled_job(...)`` 装饰器把被装饰函数也染成 Unknown，在
water entry_plugin、wordbank bootstrap、backup_scheduler 三处制造
reportUnknownMemberType 噪音。

这里只描述 src 实际调用的成员：
* ``scheduled_job``：装饰器形态的定时任务注册；
* ``get_job``：按 id 查询任务。
其余成员（start / shutdown / configure / running / add_job ...）由
nonebot_plugin_apscheduler 在 site-packages 内使用，不在本仓库的检查范围内，
不在此描述。
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.job import Job

class AsyncIOScheduler:
    def scheduled_job(
        self,
        trigger: str,
        *args: object,
        id: str | None = ...,
        name: str | None = ...,
        misfire_grace_time: int | timedelta | None = ...,
        coalesce: bool | None = ...,
        max_instances: int | None = ...,
        next_run_time: datetime | None = ...,
        jobstore: str = ...,
        executor: str = ...,
        **trigger_args: object,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...
    def get_job(self, job_id: str, jobstore: str | None = ...) -> Job | None: ...
