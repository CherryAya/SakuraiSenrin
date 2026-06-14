from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import load_plugin_doc_bundle


def test_water_ranking_docs_include_shortcuts_and_hide_restricted_periods() -> None:
    source = Path("src/plugins/water/docs/README.MD")

    bundle = load_plugin_doc_bundle(
        source=source,
        default_name="吹水记录",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    ranking = next(feature for feature in bundle.index if feature.slug == "ranking")

    assert "#今日水王" in ranking.overview
    assert "#本周群榜" in ranking.overview
    assert "revoke / recall" in ranking.overview
    assert "#水王 矩阵榜 全局 总榜" not in ranking.flow_notes
    assert "年榜" not in ranking.overview
    assert "总榜" not in ranking.overview
