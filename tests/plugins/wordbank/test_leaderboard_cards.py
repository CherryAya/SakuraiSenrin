from typing import Any

from nonebot.adapters.onebot.v11 import Message

from src.lib.message_plan import render_message_plan_entry
from src.plugins.wordbank.handlers.leaderboard_cards import (
    WordbankLeaderboardCardRenderer,
    build_wordbank_leaderboard_card_plan_entry,
)
from src.plugins.wordbank.services.core import (
    WordbankLeaderboardCardData,
    WordbankLeaderboardCardItem,
)


def _data(
    *,
    items: tuple[WordbankLeaderboardCardItem, ...],
) -> WordbankLeaderboardCardData:
    return WordbankLeaderboardCardData(
        title="苦瓜榜",
        subtitle="词条创建数量 本月 排行",
        period="month",
        badge_text="本月",
        range_text="统计范围 2026-06-01 至 2026-06-15 12:00",
        generated_at=1_718_000_000,
        total_creator_count=len(items),
        total_approved_count=sum(item.approved_count for item in items),
        champion_gap=max(0, items[0].approved_count - items[1].approved_count)
        if len(items) > 1
        else 0,
        top_share=0.6 if items else 0.0,
        items=items,
        range_start=1_717_200_000,
        range_end=1_718_000_000,
    )


def _render_wordbank_leaderboard_card(**kwargs: Any) -> Message:
    return render_message_plan_entry(
        build_wordbank_leaderboard_card_plan_entry(**kwargs)
    )


def test_render_wordbank_leaderboard_card_returns_image_message() -> None:
    message = _render_wordbank_leaderboard_card(
        data=_data(
            items=(
                WordbankLeaderboardCardItem(
                    user_id="10001",
                    display_name="凛凛",
                    approved_count=6,
                    score=6.0,
                    current_rank=1,
                    share=0.6,
                    latest_created_at=1_718_000_000,
                    group_count=3,
                    current_group_count=2,
                    all_groups_count=2,
                    self_count=0,
                    private_only_count=0,
                    self_in_current_group_count=0,
                ),
                WordbankLeaderboardCardItem(
                    user_id="10002",
                    display_name="妙妙",
                    approved_count=4,
                    score=4.0,
                    current_rank=2,
                    share=0.4,
                    latest_created_at=1_718_000_100,
                    group_count=2,
                    current_group_count=1,
                    all_groups_count=1,
                    self_count=0,
                    private_only_count=1,
                    self_in_current_group_count=0,
                ),
            )
        ),
        locale="zh-CN",
    )

    assert len(message) == 1
    assert message[0].type == "image"


def test_wordbank_leaderboard_renderer_supports_empty_state() -> None:
    renderer = WordbankLeaderboardCardRenderer()

    payload = renderer.render(data=_data(items=()), locale="zh-CN")

    assert payload.startswith(b"\x89PNG")


def test_wordbank_leaderboard_height_excludes_duplicate_champion_row() -> None:
    renderer = WordbankLeaderboardCardRenderer()
    items = (
        WordbankLeaderboardCardItem(
            user_id="10001",
            display_name="凛凛",
            approved_count=6,
            score=6.0,
            current_rank=1,
            share=0.6,
            latest_created_at=1_718_000_000,
            group_count=3,
            current_group_count=2,
            all_groups_count=2,
            self_count=0,
            private_only_count=0,
            self_in_current_group_count=0,
        ),
        WordbankLeaderboardCardItem(
            user_id="10002",
            display_name="妙妙",
            approved_count=4,
            score=4.0,
            current_rank=2,
            share=0.4,
            latest_created_at=1_718_000_100,
            group_count=2,
            current_group_count=1,
            all_groups_count=1,
            self_count=0,
            private_only_count=1,
            self_in_current_group_count=0,
        ),
    )

    one_height = renderer._measure_height(_data(items=items[:1]))  # pyright: ignore[reportPrivateUsage]
    two_height = renderer._measure_height(_data(items=items))  # pyright: ignore[reportPrivateUsage]

    assert two_height - one_height == 124
