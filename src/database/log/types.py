"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-12 20:43:34
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 16:02:57
Description: log db 基本类型
"""

from typing import NotRequired, TypedDict


class AuditLogPayload(TypedDict):
    created_at: int

    target_id: str
    context_type: str
    context_id: NotRequired[str]
    operator_id: NotRequired[str]
    category: str
    action: str
    summary: NotRequired[str]
    meta_data: NotRequired[dict]


class PluginUsageLogPayload(TypedDict):
    created_at: int

    user_id: str
    group_id: str
    plugin_name: str
    command: NotRequired[str]
    status: str
    cost_ms: int
