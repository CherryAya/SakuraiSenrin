def test_water_module_imports_are_stable() -> None:
    from src.plugins.water.handlers import query  # noqa: F401
    from src.plugins.water.renderers import season_overview  # noqa: F401
    from src.plugins.water.services import (
        query_router,  # noqa: F401
        rank_season,  # noqa: F401
        season,  # noqa: F401
    )
