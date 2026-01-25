import sentry_sdk

from nonebot.plugin import PluginMetadata
from src.common.enums import Permission, TriggerType
from src.config import config

name = "Sentry"
description = """
发送错误日志到 Sentry
""".strip()

usage = """
被动触发
""".strip()


__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
    },
)

sentry_sdk.init(
    dsn=config.SENTRY_DSN,
    send_default_pii=True,
    enable_logs=True,
)
