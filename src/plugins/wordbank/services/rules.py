"""Fixed-schema wordbank rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

Scope = Literal[
    "current_group",
    "all_groups",
    "self",
    "private_only",
    "self_in_current_group",
]
Role = Literal["any", "owner", "admin", "member"]
TriggerMode = Literal["contains", "fullmatch", "prefix"]

VALID_SCOPES: set[str] = {
    "current_group",
    "all_groups",
    "self",
    "private_only",
    "self_in_current_group",
}
VALID_ROLES: set[str] = {"any", "owner", "admin", "member"}
VALID_TRIGGER_MODES: set[str] = {"contains", "fullmatch", "prefix"}

SCOPE_PRIORITY: dict[str, int] = {
    "all_groups": 10,
    "private_only": 20,
    "current_group": 30,
    "self": 40,
    "self_in_current_group": 50,
}


class RuleError(ValueError):
    """Raised when a wordbank rule cannot be canonicalized."""


class CallCountRule(TypedDict):
    window_seconds: int
    min: int
    max: int


class RuleSchema(TypedDict, total=False):
    roles: Role
    call_count: CallCountRule


@dataclass(slots=True, frozen=True)
class CanonicalRule:
    rule: RuleSchema
    scope: Scope
    priority: int
    probability: float
    weight: int


@dataclass(slots=True, frozen=True)
class RuleContext:
    group_id: str
    user_id: str
    message_type: Literal["group", "private"]
    sender_role: Role = "member"


def _single_value(value: Any, field: str) -> Any:
    if isinstance(value, list | tuple | set):
        values = list(value)
        if len(values) != 1:
            raise RuleError(f"{field} 只能设置一个约束")
        return values[0]
    return value


def _normalize_scope(value: Any, *, is_group: bool) -> Scope:
    if value is None or value == "":
        return "current_group" if is_group else "self"
    if isinstance(value, list | tuple | set):
        values = {str(item).strip() for item in value if str(item).strip()}
        if values == {"self", "current_group"}:
            return "self_in_current_group"
        if len(values) != 1:
            raise RuleError("scope 只能设置一个生效范围")
        value = next(iter(values))
    scope = str(value).strip()
    if scope not in VALID_SCOPES:
        raise RuleError(f"不支持的生效范围: {scope}")
    return cast(Scope, scope)


def _normalize_role(value: Any) -> Role:
    value = _single_value(value, "roles")
    if value is None or value == "":
        return "any"
    role = str(value).strip()
    if role not in VALID_ROLES:
        raise RuleError(f"不支持的角色限制: {role}")
    return cast(Role, role)


def _normalize_probability(value: Any, *, short_trigger: bool) -> float:
    if value is None or value == "":
        return 0.5 if short_trigger else 1.0
    value = _single_value(value, "probability")
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise RuleError("概率必须是 0.0 到 1.0 之间的数字") from exc
    if probability < 0 or probability > 1:
        raise RuleError("概率必须是 0.0 到 1.0 之间的数字")
    return probability


def _normalize_weight(value: Any) -> int:
    if value is None or value == "":
        return 3
    value = _single_value(value, "weight")
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise RuleError("权重必须是 1 到 5 之间的整数") from exc
    if weight < 1 or weight > 5:
        raise RuleError("权重必须是 1 到 5 之间的整数")
    return weight


def _normalize_call_count(value: Any) -> CallCountRule | None:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise RuleError("调用次数窗口必须使用固定结构")
    allowed = {"window_seconds", "min", "max"}
    unknown = set(value) - allowed
    if unknown:
        raise RuleError(f"调用次数窗口包含不支持字段: {', '.join(sorted(unknown))}")
    try:
        window_seconds = int(value.get("window_seconds", 0))
        min_count = int(value.get("min", 0))
        max_count = int(value.get("max", 0))
    except (TypeError, ValueError) as exc:
        raise RuleError("调用次数窗口参数必须是整数") from exc
    if window_seconds <= 0:
        raise RuleError("调用次数窗口必须大于 0 秒")
    if min_count < 0 or max_count < 0:
        raise RuleError("调用次数上下限不能小于 0")
    if max_count and min_count > max_count:
        raise RuleError("调用次数最小值不能大于最大值")
    return {
        "window_seconds": window_seconds,
        "min": min_count,
        "max": max_count,
    }


def canonicalize_rule(
    raw_rule: dict[str, Any] | None = None,
    *,
    is_group: bool,
    short_trigger: bool,
) -> CanonicalRule:
    raw = dict(raw_rule or {})
    allowed = {"scope", "roles", "call_count", "probability", "priority", "weight"}
    unknown = set(raw) - allowed
    if unknown:
        raise RuleError(f"规则包含不支持字段: {', '.join(sorted(unknown))}")

    scope = _normalize_scope(raw.get("scope"), is_group=is_group)
    role = _normalize_role(raw.get("roles"))
    call_count = _normalize_call_count(raw.get("call_count"))
    probability = _normalize_probability(
        raw.get("probability"),
        short_trigger=short_trigger,
    )
    weight = _normalize_weight(raw.get("weight"))

    rule: RuleSchema = {}
    if role != "any":
        rule["roles"] = role
    if call_count is not None:
        rule["call_count"] = call_count

    return CanonicalRule(
        rule=rule,
        scope=scope,
        priority=SCOPE_PRIORITY[scope],
        probability=probability,
        weight=weight,
    )


def normalize_trigger_mode(value: str | None, *, short_trigger: bool) -> TriggerMode:
    if value is None or not value.strip():
        return "fullmatch" if short_trigger else "contains"
    mode = value.strip().lower()
    if mode not in VALID_TRIGGER_MODES:
        raise RuleError(f"不支持的触发模式: {mode}")
    return cast(TriggerMode, mode)


def rule_allows(
    *,
    scope: str,
    entry_group_id: str,
    entry_created_by: str,
    rule: dict[str, Any],
    context: RuleContext,
    current_call_count: int = 0,
) -> bool:
    if scope == "current_group":
        if context.message_type != "group" or context.group_id != entry_group_id:
            return False
    elif scope == "all_groups":
        if context.message_type != "group":
            return False
    elif scope == "self":
        if context.user_id != entry_created_by:
            return False
    elif scope == "private_only":
        if context.message_type != "private":
            return False
    elif scope == "self_in_current_group":
        if (
            context.message_type != "group"
            or context.group_id != entry_group_id
            or context.user_id != entry_created_by
        ):
            return False
    else:
        return False

    role = str(rule.get("roles", "any"))
    if role != "any" and context.sender_role != role:
        return False

    call_count = rule.get("call_count")
    if isinstance(call_count, dict):
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
        if current_call_count < min_count:
            return False
        if max_count and current_call_count > max_count:
            return False

    return True


def parse_legacy_study_text(text: str) -> tuple[str, str, dict[str, Any]]:
    """Convert legacy study text into a fixed-schema add request.

    Supported forms:
    - ``触发词 => 响应词``
    - ``触发词 -> 响应词``
    - ``触发词 回答 响应词``
    """

    source = text.strip()
    for sep in ("=>", "->", "回答", "回复"):
        if sep in source:
            trigger, response = source.split(sep, 1)
            trigger = trigger.strip()
            response = response.strip()
            if not trigger or not response:
                raise RuleError("学习内容需要同时包含触发词和响应词")
            return trigger, response, {}
    parts = source.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), {}
    raise RuleError("学习格式: study 触发词 => 响应词")
