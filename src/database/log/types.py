from typing import NotRequired, TypedDict


class AuditLogPayload(TypedDict):
    operator_id: NotRequired[str]
    target_id: str
    category: str
    action: str
    summary: NotRequired[str]
    meta_data: dict


class PluginUsageLogPayload(TypedDict):
    user_id: str
    group_id: str
    plugin_name: str
    command: NotRequired[str]
    status: str
    cost_ms: int
