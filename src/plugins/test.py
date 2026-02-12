from nonebot.plugin import on_message

test_matcher = on_message()


@test_matcher.handle()
async def _() -> None:
    pass
