from __future__ import annotations

from pathlib import Path
import re

import pytest

HEX_COLOR_PATTERN = re.compile(r'"#[0-9A-Fa-f]{3,8}"')

THEMED_RENDERERS = (
    "src/plugins/admin/invite.py",
    "src/plugins/water/img.py",
    "src/plugins/wordbank/handlers/search_cards.py",
    "src/plugins/wordbank/handlers/group_detail_cards.py",
    "src/plugins/wordbank/handlers/leaderboard_cards.py",
    "src/plugins/wordbank/wordbank_search_treemap.py",
)


@pytest.mark.parametrize("relative_path", THEMED_RENDERERS)
def test_themed_renderers_do_not_inline_hex_colors(relative_path: str) -> None:
    content = Path(relative_path).read_text(encoding="utf-8")
    assert HEX_COLOR_PATTERN.search(content) is None, relative_path
