"""Derived help registry for wordbank-powered directory plugins."""

from __future__ import annotations

from src.database.core.consts import Permission
from src.lib.plugin_docs import (
    DocsDemoTurn,
    VirtualFeatureDocSpec,
    VirtualPluginDocSpec,
)


def _tool_feature(
    *,
    slug: str,
    title: str,
    summary: str,
    trigger: str,
    overview: str,
    aliases: tuple[str, ...] = (),
) -> VirtualFeatureDocSpec:
    return VirtualFeatureDocSpec(
        slug=slug,
        title=title,
        summary=summary,
        aliases=aliases,
        trigger=trigger,
        overview=overview,
        preconditions="需要对应词条已经由群友上传并通过审核。",
        failures="若当前环境没有这组词条、触发词拼写不匹配，或相关图片资源失效，则不会返回预期玩法说明。",
        demo_turns=(
            DocsDemoTurn("USER", trigger),
            DocsDemoTurn("BOT", f"{title}：{summary}"),
        ),
    )


def _tool_overview(index: int, title: str) -> str:
    return f"输入触发词 `妙妙小工具{index}` 时，会回复 `{index}.{title}` 的玩法说明。"


def build_wordbank_derived_help(locale: str) -> tuple[VirtualPluginDocSpec, ...]:
    _ = locale
    return (
        VirtualPluginDocSpec(
            slug="derived.wordbank.miaomiao-toolkit",
            title="凛凛的妙妙小工具目录",
            summary="以下为一些群友上传的有趣词条玩法说明，输入对应触发词即可查看说明。",
            description="基于 wordbank 派生出的目录型帮助节点。",
            trigger="词条触发 / #help 查询",
            author="SakuraiSenrin",
            version="0.1.0",
            impression_color="#74C0FC",
            aliases=("凛凛的妙妙小工具", "妙妙小工具", "妙妙小工具目录"),
            permission=Permission.NORMAL,
            category="fun",
            order=85,
            visible=True,
            origin_plugin_slug="wordbank",
            features=(
                _tool_feature(
                    slug="fortune",
                    title="运势",
                    summary="查看运势玩法对应的词条说明。",
                    trigger="妙妙小工具1",
                    overview=_tool_overview(1, "运势"),
                    aliases=("1", "运势", "妙妙小工具1"),
                ),
                _tool_feature(
                    slug="jrlp",
                    title="jrlp",
                    summary="查看 jrlp 玩法对应的词条说明。",
                    trigger="妙妙小工具2",
                    overview=_tool_overview(2, "jrlp"),
                    aliases=("2", "jrlp", "妙妙小工具2"),
                ),
                _tool_feature(
                    slug="rock-paper-scissors",
                    title="猜拳",
                    summary="查看猜拳玩法对应的词条说明。",
                    trigger="妙妙小工具3",
                    overview=_tool_overview(3, "猜拳"),
                    aliases=("3", "猜拳", "妙妙小工具3"),
                ),
                _tool_feature(
                    slug="drift-bottle",
                    title="漂流瓶",
                    summary="查看漂流瓶玩法对应的词条说明。",
                    trigger="妙妙小工具4",
                    overview=_tool_overview(4, "漂流瓶"),
                    aliases=("4", "漂流瓶", "妙妙小工具4"),
                ),
                _tool_feature(
                    slug="check-in",
                    title="打卡",
                    summary="查看打卡玩法对应的词条说明。",
                    trigger="妙妙小工具5",
                    overview=_tool_overview(5, "打卡"),
                    aliases=("5", "打卡", "妙妙小工具5"),
                ),
                _tool_feature(
                    slug="truth-or-dare",
                    title="真心话与大冒险",
                    summary="查看真心话与大冒险玩法对应的词条说明。",
                    trigger="妙妙小工具6",
                    overview=_tool_overview(6, "真心话与大冒险"),
                    aliases=("6", "真心话与大冒险", "妙妙小工具6"),
                ),
                _tool_feature(
                    slug="teach-wordbank",
                    title="笨蛋也能学会的教词条方法",
                    summary="查看教词条方法对应的词条说明。",
                    trigger="妙妙小工具7",
                    overview=_tool_overview(7, "笨蛋也能学会的教词条方法"),
                    aliases=("7", "教词条方法", "妙妙小工具7"),
                ),
                _tool_feature(
                    slug="xianzun-quotes",
                    title="随机仙尊语录",
                    summary="查看随机仙尊语录玩法对应的词条说明。",
                    trigger="妙妙小工具8",
                    overview=_tool_overview(8, "随机仙尊语录"),
                    aliases=("8", "随机仙尊语录", "妙妙小工具8"),
                ),
                _tool_feature(
                    slug="my-selfie",
                    title="我的自拍",
                    summary="查看我的自拍玩法对应的词条说明。",
                    trigger="妙妙小工具9",
                    overview=_tool_overview(9, "我的自拍"),
                    aliases=("9", "我的自拍", "妙妙小工具9"),
                ),
                _tool_feature(
                    slug="doro-ending",
                    title="随机doro结局",
                    summary="查看随机 doro 结局玩法对应的词条说明。",
                    trigger="妙妙小工具10",
                    overview=_tool_overview(10, "随机doro结局"),
                    aliases=("10", "随机doro结局", "妙妙小工具10"),
                ),
            ),
        ),
    )
