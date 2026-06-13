"""Fixed-schema wordbank rules."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any, Literal, TypedDict, cast

from src.lib.i18n.keys import MessageKey
from src.plugins.wordbank.services.errors import WordbankUserError

Scope = Literal[
    "current_group",
    "all_groups",
    "self",
    "private_only",
    "self_in_current_group",
]
Role = Literal["any", "owner", "admin", "member"]

VALID_SCOPES: set[str] = {
    "current_group",
    "all_groups",
    "self",
    "private_only",
    "self_in_current_group",
}
VALID_ROLES: set[str] = {"any", "owner", "admin", "member"}
ROLE_LEVELS: dict[Role, int] = {
    "any": 0,
    "member": 1,
    "admin": 2,
    "owner": 3,
}
MAX_CALL_COUNT_WINDOW_SECONDS = 730 * 24 * 60 * 60

SCOPE_PRIORITY: dict[str, int] = {
    "all_groups": 10,
    "private_only": 20,
    "current_group": 30,
    "self": 40,
    "self_in_current_group": 50,
}


class RuleError(WordbankUserError):
    """Raised when a wordbank rule cannot be canonicalized."""


def _rule_error(fallback: str, key: MessageKey, **params: object) -> RuleError:
    return RuleError(fallback, key=key, **params)


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
            raise _rule_error(
                f"{field} 只能设置一个约束",
                "wordbank.error.single_constraint",
                field=field,
            )
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
            raise _rule_error(
                "scope 只能设置一个生效范围",
                "wordbank.error.scope_single",
            )
        value = next(iter(values))
    scope = str(value).strip()
    if scope not in VALID_SCOPES:
        raise _rule_error(
            f"不支持的生效范围: {scope}",
            "wordbank.error.scope_unsupported",
            scope=scope,
        )
    return cast(Scope, scope)


def _normalize_role(value: Any) -> Role:
    value = _single_value(value, "roles")
    if value is None or value == "":
        return "any"
    role = str(value).strip()
    if role not in VALID_ROLES:
        raise _rule_error(
            f"不支持的角色限制: {role}",
            "wordbank.error.role_unsupported",
            role=role,
        )
    return cast(Role, role)


def _normalize_probability(value: Any, *, short_trigger: bool) -> float:
    if value is None or value == "":
        return 0.5 if short_trigger else 1.0
    value = _single_value(value, "probability")
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise _rule_error(
            "概率必须是 0.0 到 1.0 之间的数字",
            "wordbank.error.probability_invalid",
        ) from exc
    if probability < 0 or probability > 1:
        raise _rule_error(
            "概率必须是 0.0 到 1.0 之间的数字",
            "wordbank.error.probability_invalid",
        )
    return probability


def _normalize_weight(value: Any) -> int:
    if value is None or value == "":
        return 3
    value = _single_value(value, "weight")
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise _rule_error(
            "权重必须是 1 到 5 之间的整数",
            "wordbank.error.weight_invalid",
        ) from exc
    if weight < 1 or weight > 5:
        raise _rule_error(
            "权重必须是 1 到 5 之间的整数",
            "wordbank.error.weight_invalid",
        )
    return weight


def _normalize_call_count(value: Any) -> CallCountRule | None:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise _rule_error(
            "调用次数窗口必须使用固定结构",
            "wordbank.error.call_structure",
        )
    allowed = {"window_seconds", "min", "max"}
    unknown = set(value) - allowed
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise _rule_error(
            f"调用次数窗口包含不支持字段: {fields}",
            "wordbank.error.call_unknown",
            fields=fields,
        )
    try:
        window_seconds = int(value.get("window_seconds", 0))
        min_count = int(value.get("min", 0))
        max_count = int(value.get("max", 0))
    except (TypeError, ValueError) as exc:
        raise _rule_error(
            "调用次数窗口参数必须是整数",
            "wordbank.error.call_integer",
        ) from exc
    if window_seconds <= 0:
        raise _rule_error(
            "调用次数窗口必须大于 0 秒",
            "wordbank.error.call_window_positive",
        )
    if window_seconds > MAX_CALL_COUNT_WINDOW_SECONDS:
        raise _rule_error(
            "调用次数窗口不能超过 24 个月",
            "wordbank.error.call_window_too_large",
        )
    if min_count < 0 or max_count < 0:
        raise _rule_error(
            "调用次数上下限不能小于 0",
            "wordbank.error.call_non_negative",
        )
    if max_count and min_count > max_count:
        raise _rule_error(
            "调用次数最小值不能大于最大值",
            "wordbank.error.call_min_lte_max",
        )
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
        fields = ", ".join(sorted(unknown))
        raise _rule_error(
            f"规则包含不支持字段: {fields}",
            "wordbank.error.rule_unknown",
            fields=fields,
        )

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
    sender_role = context.sender_role
    if (
        role != "any"
        and role in VALID_ROLES
        and sender_role in VALID_ROLES
        and ROLE_LEVELS[cast(Role, sender_role)] < ROLE_LEVELS[cast(Role, role)]
    ):
        return False
    if role != "any" and (role not in VALID_ROLES or sender_role not in VALID_ROLES):
        return False

    call_count = rule.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0))
        if window_seconds <= 0 or window_seconds > MAX_CALL_COUNT_WINDOW_SECONDS:
            return False
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
        if current_call_count < min_count:
            return False
        if max_count and current_call_count > max_count:
            return False

    return True


def _legacy_study_shortcut_rule(
    trig_mode: str,
    group_block: str,
    *,
    is_group: bool,
) -> dict[str, Any]:
    if trig_mode == "a":
        if not is_group:
            return {"scope": "private_only"}
        return {"scope": "current_group" if group_block == "t" else "all_groups"}
    if is_group and group_block == "t":
        return {"scope": {"self", "current_group"}}
    return {"scope": "self"}


def build_legacy_study_shortcut_rule(
    trig_mode: str,
    group_block: str,
    *,
    is_group: bool = True,
) -> dict[str, Any]:
    return _legacy_study_shortcut_rule(
        trig_mode,
        group_block,
        is_group=is_group,
    )


def parse_legacy_study_text(
    text: str,
    *,
    is_group: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    """Convert legacy study text into a fixed-schema add request.

    Supported forms:
    - ``触发词 => 响应词``
    - ``触发词 -> 响应词``
    - ``触发词 回答 响应词``
    - ``a t 触发词 响应词``
    """

    source = text.strip()
    for sep in ("=>", "->", "回答", "回复"):
        if sep in source:
            trigger, response = source.split(sep, 1)
            trigger = trigger.strip()
            response = response.strip()
            if not trigger or not response:
                raise _rule_error(
                    "学习内容需要同时包含触发词和响应词",
                    "wordbank.error.study_pair_required",
                )
            return trigger, response, {}
    try:
        tokens = shlex.split(source)
    except ValueError as exc:
        raise _rule_error(
            "学习格式: study 触发词 => 响应词",
            "wordbank.error.study_format",
        ) from exc
    if (
        len(tokens) >= 4
        and tokens[0].casefold() in {"a", "m"}
        and tokens[1].casefold() in {"t", "f"}
    ):
        trigger = tokens[2].strip()
        response = " ".join(tokens[3:]).strip()
        if not trigger or not response:
            raise _rule_error(
                "学习内容需要同时包含触发词和响应词",
                "wordbank.error.study_pair_required",
            )
        return (
            trigger,
            response,
            _legacy_study_shortcut_rule(
                tokens[0].casefold(),
                tokens[1].casefold(),
                is_group=is_group,
            ),
        )
    if tokens and tokens[0].casefold() in {"a", "m"}:
        raise _rule_error(
            "学习格式: study 触发词 => 响应词",
            "wordbank.error.study_format",
        )
    parts = source.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), {}
    raise _rule_error(
        "学习格式: study 触发词 => 响应词",
        "wordbank.error.study_format",
    )
