"""渲染质量断言（与可行性测试分离）。

可行性（各输入跑通到渲染调用）见 test_rank.py；本文件看真实渲染
效果并做像素级断言。单个用例较慢（约 5-20s/次），改动渲染器时运行。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image
from pil_utils import BuildImage
import pytest

from src.plugins.water.renderers import (
    WaterGroupDailyRankCardItem,
    WaterGroupReportImageData,
    WaterRankCardItem,
    build_water_group_report_image,
)


def _avatar() -> BuildImage:
    return BuildImage.new("RGBA", (128, 128), "#F6B7D2")


def _report_data(
    *, group_rank_count: int, name: str = "测试群"
) -> WaterGroupReportImageData:
    avatar = _avatar()
    return WaterGroupReportImageData(
        title="Senrin水王日报",
        badge="",
        range_text="统计日期: 2026.06.14 · 今日实时快照",
        compare_text="对比日期: 2026.06.13 · 消息 +30 · 活跃成员 +1",
        generated_at=1_747_960_000,
        total_msg_count=420,
        active_user_count=66,
        hourly_counts=[(idx % 6) + 1 for idx in range(24)],
        peak_hour=5,
        previous_total_msg_count=390,
        top_items=[
            WaterRankCardItem(
                entity_id="10001",
                display_name="Alice",
                secondary_label="用户 10001",
                avatar=avatar,
                msg_count=120,
                active_days=15,
                active_hours=28,
                hourly_counts=[(idx % 5) + 1 for idx in range(24)],
                current_rank=1,
                trend=3,
            )
        ],
        group_rank_title="群聊当日排名",
        group_rank_summary="本群当前排名 #3 / 12 · 较昨日 +1",
        group_rank_items=[
            WaterGroupDailyRankCardItem(
                group_id=f"2000{idx}",
                display_name=f"{name}{idx}",
                avatar=avatar,
                msg_count=120 - idx * 10,
                current_rank=idx + 3,
                trend=1,
                is_focus_group=(idx == 0),
            )
            for idx in range(group_rank_count)
        ],
    )


async def _render(data: WaterGroupReportImageData) -> Image.Image:
    raw = await build_water_group_report_image(data, "zh-CN")
    assert raw is not None
    return Image.open(BytesIO(raw))


@pytest.mark.asyncio
async def test_report_image_is_scaled_two_x_with_expected_width() -> None:
    """报告图按 2 倍分辨率渲染，画布宽度固定 1120*2。"""
    image = await _render(_report_data(group_rank_count=2))

    assert image.width == 2240
    assert image.height > 0


@pytest.mark.asyncio
async def test_report_image_grows_with_group_rank_rows() -> None:
    """群排名行数增加时画布随之变高（防布局漏算）。"""
    short = await _render(_report_data(group_rank_count=2))
    tall = await _render(_report_data(group_rank_count=6))

    assert tall.height > short.height


@pytest.mark.asyncio
async def test_report_image_long_group_names_do_not_widen_canvas() -> None:
    """超长群名走截断，画布宽度保持稳定。"""
    normal = await _render(_report_data(group_rank_count=2))
    long_name = await _render(
        _report_data(group_rank_count=2, name="这是一个非常非常非常非常长的群聊名称")
    )

    assert long_name.width == normal.width


@pytest.mark.asyncio
async def test_report_image_content_region_is_not_blank() -> None:
    """卡片区应绘制出内容，而非整片背景色。"""
    image = (await _render(_report_data(group_rank_count=2))).convert("RGB")
    background = image.getpixel((2, 2))
    sample_points = [
        (image.width // 2, int(image.height * 0.1)),
        (image.width // 4, int(image.height * 0.3)),
        (image.width // 2, int(image.height * 0.6)),
    ]

    assert any(image.getpixel(point) != background for point in sample_points)
