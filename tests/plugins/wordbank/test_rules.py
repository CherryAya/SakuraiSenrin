import pytest

from src.lib.types import JsonValue
from src.plugins.wordbank.services.rules import (
    MAX_CALL_COUNT_WINDOW_SECONDS,
    RuleContext,
    RuleError,
    build_legacy_study_shortcut_rule,
    canonicalize_rule,
    normalize_role_alias,
    normalize_scope_alias,
    parse_legacy_study_text,
    rule_allows,
)


def test_canonicalize_defaults_and_short_trigger_probability() -> None:
    rule = canonicalize_rule({}, is_group=True, short_trigger=True)

    assert rule.rule == {}
    assert rule.scope == "current_group"
    assert rule.priority == 30
    assert rule.probability == 0.5
    assert rule.weight == 3

    private_rule = canonicalize_rule({}, is_group=False, short_trigger=False)
    assert private_rule.scope == "self"


def test_canonicalize_rejects_conflicting_scope_and_bad_weight() -> None:
    with pytest.raises(RuleError, match="scope"):
        canonicalize_rule(
            {"scope": ["self", "all_groups"]},
            is_group=True,
            short_trigger=False,
        )

    with pytest.raises(RuleError, match="权重"):
        canonicalize_rule({"weight": 9}, is_group=True, short_trigger=False)


def test_self_current_group_scope_is_internalized() -> None:
    rule = canonicalize_rule(
        {"scope": ["self", "current_group"], "roles": "admin"},
        is_group=True,
        short_trigger=False,
    )

    assert rule.scope == "self_in_current_group"
    assert rule.priority == 50
    assert rule.rule == {"roles": "admin"}


def test_scope_and_role_aliases_are_normalized() -> None:
    assert normalize_scope_alias("本群") == "current_group"
    assert normalize_scope_alias("所有群") == "all_groups"
    assert normalize_scope_alias("仅自己") == "self"
    assert normalize_scope_alias("私聊") == "private_only"

    assert normalize_role_alias("群主") == "owner"
    assert normalize_role_alias("管理") == "admin"
    assert normalize_role_alias("成员") == "member"
    assert normalize_role_alias("o") == "owner"
    assert normalize_role_alias("a") == "admin"
    assert normalize_role_alias("m") == "member"


def test_canonicalize_rule_accepts_chinese_scope_and_role_aliases() -> None:
    rule = canonicalize_rule(
        {"scope": "本群", "roles": "管理"},
        is_group=True,
        short_trigger=False,
    )

    assert rule.scope == "current_group"
    assert rule.rule == {"roles": "admin"}


def test_rule_allows_scope_role_and_call_count() -> None:
    context = RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="admin",
    )

    assert rule_allows(
        scope="self_in_current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule={"roles": "admin", "call_count": {"window_seconds": 60, "min": 1}},
        context=context,
        current_call_count=1,
    )
    assert not rule_allows(
        scope="self_in_current_group",
        entry_group_id="20002",
        entry_created_by="10001",
        rule={"roles": "admin"},
        context=context,
    )
    assert not rule_allows(
        scope="self_in_current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule={
            "roles": "admin",
            "call_count": {
                "window_seconds": MAX_CALL_COUNT_WINDOW_SECONDS + 1,
                "min": 0,
            },
        },
        context=context,
        current_call_count=99,
    )


def test_rule_allows_all_groups_in_private_context() -> None:
    context = RuleContext(
        group_id="",
        user_id="10001",
        message_type="private",
        sender_role="member",
    )

    assert rule_allows(
        scope="all_groups",
        entry_group_id="20001",
        entry_created_by="10002",
        rule={},
        context=context,
    )


def test_canonicalize_rejects_too_large_call_window() -> None:
    with pytest.raises(RuleError, match="3 个月"):
        canonicalize_rule(
            {
                "call_count": {
                    "window_seconds": MAX_CALL_COUNT_WINDOW_SECONDS + 1,
                    "min": 0,
                    "max": 0,
                }
            },
            is_group=True,
            short_trigger=False,
        )


def test_rule_allows_role_hierarchy() -> None:
    owner_context = RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="owner",
    )
    admin_context = RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="admin",
    )

    assert rule_allows(
        scope="current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule={"roles": "admin"},
        context=owner_context,
    )
    assert not rule_allows(
        scope="current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule={"roles": "owner"},
        context=admin_context,
    )


def test_parse_legacy_study_text() -> None:
    trigger, response, rule = parse_legacy_study_text("晚安 => 做个好梦")

    assert trigger == "晚安"
    assert response == "做个好梦"
    assert rule == {}

    with pytest.raises(RuleError, match="学习格式"):
        parse_legacy_study_text("只有一个词")

    trigger, response, rule = parse_legacy_study_text("晚安=>第一行\n第二行  第三列")
    assert trigger == "晚安"
    assert response == "第一行\n第二行  第三列"
    assert rule == {}


def test_parse_legacy_study_shortcut_modes() -> None:
    trigger, response, rule = parse_legacy_study_text("a t 晚安 做个好梦")

    assert trigger == "晚安"
    assert response == "做个好梦"
    assert rule == {"scope": "current_group"}

    trigger, response, rule = parse_legacy_study_text("a t 晚安 第一行\n第二行  第三列")
    assert trigger == "晚安"
    assert response == "第一行\n第二行  第三列"
    assert rule == {"scope": "current_group"}

    assert parse_legacy_study_text("a f 晚安 做个好梦")[2] == {"scope": "all_groups"}
    assert parse_legacy_study_text("m f 晚安 做个好梦")[2] == {"scope": "self"}
    assert parse_legacy_study_text("m t 晚安 做个好梦")[2] == {
        "scope": {"self", "current_group"}
    }
    assert parse_legacy_study_text("a t 晚安 做个好梦", is_group=False)[2] == {
        "scope": "self"
    }
    assert parse_legacy_study_text("a f 晚安 做个好梦", is_group=False)[2] == {
        "scope": "all_groups"
    }

    assert build_legacy_study_shortcut_rule("a", "t") == {"scope": "current_group"}
    assert build_legacy_study_shortcut_rule("a", "f") == {"scope": "all_groups"}
    assert build_legacy_study_shortcut_rule("m", "t") == {
        "scope": {"self", "current_group"}
    }
    assert build_legacy_study_shortcut_rule("a", "t", is_group=False) == {
        "scope": "self"
    }
    assert build_legacy_study_shortcut_rule("a", "f", is_group=False) == {
        "scope": "all_groups"
    }


def test_canonicalize_private_only_in_private_chat_downgrades_to_self() -> None:
    rule = canonicalize_rule(
        {"scope": "private_only"},
        is_group=False,
        short_trigger=False,
    )

    assert rule.scope == "self"

    with pytest.raises(RuleError, match="学习格式"):
        parse_legacy_study_text("a t 晚安")


@pytest.mark.parametrize("bad", [None, True, False, "60", 60.5, [60]])
def test_rule_allows_tolerates_malformed_window_seconds(bad: JsonValue) -> None:
    """回归：call_count 的非法值必须回落为「不满足」，而不是抛异常。

    历史故障：``int(cast(...))`` 遇到 ``{"window_seconds": null}`` 会抛
    TypeError——``.get`` 的默认值对「键存在但值为 null」不生效。该异常不被
    调用方的 except (RuleError, ValueError) 捕获，会冒泡到被动链路被静默吞掉，
    导致触发词无声失效。
    """
    context = RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="admin",
    )
    rule: dict[str, JsonValue] = {
        "roles": "admin",
        "call_count": {"window_seconds": bad, "min": 0},
    }
    verdict = rule_allows(
        scope="self_in_current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule=rule,
        context=context,
        current_call_count=1,
    )

    assert verdict is False, f"window_seconds={bad!r} 应判为不满足"


def test_rule_allows_tolerates_malformed_min_max() -> None:
    """min/max 非法同样不得抛异常。"""
    context = RuleContext(
        group_id="20001",
        user_id="10001",
        message_type="group",
        sender_role="admin",
    )
    assert rule_allows(
        scope="self_in_current_group",
        entry_group_id="20001",
        entry_created_by="10001",
        rule={
            "roles": "admin",
            "call_count": {"window_seconds": 60, "min": None, "max": "x"},
        },
        context=context,
        current_call_count=1,
    )


@pytest.mark.parametrize("bad", [True, False, "abc"])
def test_canonicalize_rejects_boolean_weight(bad: JsonValue) -> None:
    """weight 必须拒绝布尔值：``int(True) == 1`` 会绕过「权重 1..5」的下界校验。

    注意 ``None``（缺省）、单元素列表（解包）、整值浮点 ``3.0`` 与数字字符串
    ``"3"`` 都是既有合法形态，收紧口径时不得误伤——见下一条用例。
    """
    with pytest.raises(RuleError, match="权重"):
        canonicalize_rule({"weight": bad}, is_group=True, short_trigger=False)


@pytest.mark.parametrize("bad", [True, False, "abc"])
def test_canonicalize_rejects_boolean_probability(bad: JsonValue) -> None:
    with pytest.raises(RuleError, match="概率"):
        canonicalize_rule({"probability": bad}, is_group=True, short_trigger=False)


@pytest.mark.parametrize("bad", [[3, 4], [0.5, 0.6]])
def test_canonicalize_rejects_multi_value_constraint(bad: JsonValue) -> None:
    """多元素列表要走「只能设置一个约束」的既有分支。"""
    with pytest.raises(RuleError, match="只能设置一个约束"):
        canonicalize_rule({"weight": bad}, is_group=True, short_trigger=False)


@pytest.mark.parametrize("value", [3, 3.0, "3", [3], None])
def test_canonicalize_still_accepts_valid_weight_forms(value: JsonValue) -> None:
    """确保收紧口径没有误伤既有合法形态（含 None 缺省与单元素列表解包）。"""
    rule = canonicalize_rule({"weight": value}, is_group=True, short_trigger=False)

    assert rule.weight == 3
