from nonebot.adapters.onebot.v11.message import Message

from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers.search_cards import (
    SearchCardQuery,
    render_search_results_card,
)


def _item(
    trigger_group_id: int,
    *,
    matched_by: str = "text:trigger",
) -> WordbankSearchItem:
    return WordbankSearchItem(
        trigger_group_id=trigger_group_id,
        status="approved",
        trigger_text=f"触发{trigger_group_id}",
        response_text=f"响应{trigger_group_id}",
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        matched_by=matched_by,
    )


def test_render_search_results_card_returns_image_message() -> None:
    message = render_search_results_card(
        items=(_item(12), _item(13, matched_by="image:response")),
        query=SearchCardQuery(
            keyword="晚安",
            field="all",
            creator_id="10001",
            has_image=True,
            page=1,
            total_count=2,
            limit=10,
        ),
        locale="zh-CN",
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"
