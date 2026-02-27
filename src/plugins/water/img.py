import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import random
from typing import Any

from PIL import ImageFont
from pil_utils import BuildImage

from src.lib.utils.img import QQAvatar

SYS_FONT_NAME = "Maple Mono NF CN"
FALLBACK_FONT_PATH = "./data/font/MapleMono-NF-CN-Regular.ttf"


@dataclass
class WaterInfo:
    user_id: str
    group_id: str
    created_at: datetime


class WaterRankRenderer:
    def __init__(self) -> None:
        self.BG_COLOR = "#F9DEE2"
        self.TEXT_COLOR = "#B44C4C"
        self.ITEM_BG_COLOR = "#FFF0F5"
        self.HIGHLIGHT_COLOR = "#DC5A64"
        self.RANK_THEMES = {
            1: {"bg": "#FBD089", "badge": "#FFAE00", "badge_txt": "#FFFFFF"},
            2: {"bg": "#D2CECE", "badge": "#ABABAB", "badge_txt": "#FFFFFF"},
            3: {"bg": "#F4D7C5", "badge": "#C4835F", "badge_txt": "#FFFFFF"},
        }
        self.SCALE = 3.2
        self.RENDER_WIDTH = int(900 * self.SCALE)
        self.PADDING = int(60 * self.SCALE)

        try:
            self.num_small_font = ImageFont.truetype(
                FALLBACK_FONT_PATH, int(18 * self.SCALE)
            )
            self.num_tiny_font = ImageFont.truetype(
                FALLBACK_FONT_PATH, int(22 * self.SCALE * 0.55)
            )
        except OSError:
            self.num_small_font = ImageFont.load_default()
            self.num_tiny_font = ImageFont.load_default()

    def _safe_truncate(self, text: str, max_len: int = 16) -> str:
        text = text.replace("\n", " ").replace("\r", "").replace("\t", " ")
        return text[:max_len] + "..." if len(text) > max_len else text

    def _generate_tile_chart(self, hourly_data: list[int]) -> BuildImage:
        rows, cols = 2, 12
        tile_spacing = int(4 * self.SCALE)
        tile_size = int(22 * self.SCALE)
        chart_width = cols * tile_size + (cols - 1) * tile_spacing
        chart_height = rows * tile_size + (rows - 1) * tile_spacing

        chart = BuildImage.new("RGBA", (chart_width, chart_height), (0, 0, 0, 0))
        max_count = max(hourly_data) or 1

        for hour in range(24):
            row, col = hour // cols, hour % cols
            x0 = col * (tile_size + tile_spacing)
            y0 = row * (tile_size + tile_spacing)

            alpha = int(40 + (255 - 40) * (hourly_data[hour] / max_count))
            color_hex = f"{self.TEXT_COLOR}{alpha:02X}"

            chart.draw_rounded_rectangle(
                (x0, y0, x0 + tile_size, y0 + tile_size),
                radius=int(4 * self.SCALE),
                fill=color_hex,
                outline=f"{self.TEXT_COLOR}FF",
                width=int(1 * self.SCALE),
            )

            text_fill = (
                (255, 255, 255, 255 if alpha < 150 else min(alpha + 100, 255))
                if alpha > 128
                else f"{self.TEXT_COLOR}FF"
            )
            center_x, center_y = (
                x0 + tile_size / 2,
                y0 + tile_size / 2,
            )
            chart.draw.text(
                (center_x, center_y),
                f"{hour:02d}",
                fill=text_fill,
                font=self.num_tiny_font,
                anchor="mm",
            )

        return chart

    def _render_user_row(self, rank: int, user: dict[str, Any]) -> BuildImage:
        item_h = int(110 * self.SCALE)
        avatar_size = int(64 * self.SCALE)
        base_y = int(10 * self.SCALE)

        row = BuildImage.new("RGBA", (self.RENDER_WIDTH, item_h + base_y), (0, 0, 0, 0))
        theme = self.RANK_THEMES.get(rank, {"bg": self.ITEM_BG_COLOR, "badge": None})

        row.draw_rounded_rectangle(
            (self.PADDING, base_y, self.RENDER_WIDTH - self.PADDING, base_y + item_h),
            radius=int(15 * self.SCALE),
            fill=theme["bg"],
        )
        avatar_x = self.PADDING + int(20 * self.SCALE)
        avatar_y = base_y + (item_h - avatar_size) // 2
        row.paste(
            user["avatar_img"].circle().resize((avatar_size, avatar_size)),
            (avatar_x, avatar_y),
            alpha=True,
        )

        trend = user.get("trend")
        t_str, t_color = (
            ("NEW", (255, 180, 50))
            if trend is None
            else (f"↑ {trend}", (255, 110, 110))
            if trend > 0
            else (f"↓ {abs(trend)}", (90, 200, 100))
            if trend < 0
            else ("− 0", (180, 180, 190))
        )

        badge_x, badge_y = (
            self.PADDING - int(10 * self.SCALE),
            base_y - int(10 * self.SCALE),
        )
        pill_w, pill_h = int(52 * self.SCALE), int(24 * self.SCALE)
        row.draw_rounded_rectangle(
            (badge_x, badge_y, badge_x + pill_w, badge_y + pill_h),
            radius=pill_h // 2,
            fill=t_color,
            outline="white",
            width=int(2.5 * self.SCALE),
        )
        row.draw.text(
            (badge_x + pill_w / 2, badge_y + pill_h / 2),
            t_str,
            fill="white",
            font=self.num_small_font,
            anchor="mm",
        )

        text_x = avatar_x + avatar_size + int(20 * self.SCALE)
        chart = self._generate_tile_chart(user["hourly_data"])
        chart_x = self.RENDER_WIDTH - self.PADDING - chart.width - int(20 * self.SCALE)
        max_name_width = chart_x - text_x - int(10 * self.SCALE)
        display_name = self._safe_truncate(user["username"], max_len=16)
        name_box_top = base_y + int(10 * self.SCALE)
        name_box_bottom = base_y + int(55 * self.SCALE)
        b_pad_x, b_pad_y = int(8 * self.SCALE), int(4 * self.SCALE)
        row_center_y = base_y + int(35 * self.SCALE)

        if theme["badge"]:
            badge_text = f"TOP {rank}"
            b_pad_x, b_pad_y = int(8 * self.SCALE), int(5 * self.SCALE)
            b_width = int(row.draw.textlength(badge_text, font=self.num_small_font))
            badge_h = int(18 * self.SCALE) + b_pad_y * 2

            box_rect = (
                text_x,
                row_center_y - badge_h // 2,
                text_x + b_width + b_pad_x * 2,
                row_center_y + badge_h // 2,
            )

            row.draw_rounded_rectangle(
                box_rect, radius=int(6 * self.SCALE), fill=theme["badge"]
            )

            row.draw.text(
                ((box_rect[0] + box_rect[2]) / 2, row_center_y + int(0.5 * self.SCALE)),
                badge_text,
                fill=theme["badge_txt"],
                font=self.num_small_font,
                anchor="mm",
            )
            name_x = box_rect[2] + int(10 * self.SCALE)

        else:
            badge_text = f"#{rank:02d}"
            b_width = int(row.draw.textlength(badge_text, font=self.num_small_font))

            row.draw.text(
                (text_x, row_center_y),
                badge_text,
                fill="#A58A8D",
                font=self.num_small_font,
                anchor="lm",
            )
            name_x = text_x + b_width + int(8 * self.SCALE)

        skia_fix_y = int(-0.5 * self.SCALE)
        name_box_top = row_center_y - int(20 * self.SCALE) + skia_fix_y
        name_box_bottom = row_center_y + int(20 * self.SCALE) + skia_fix_y

        box_coords = (
            name_x,
            name_box_top,
            name_x + max_name_width - (name_x - text_x),
            name_box_bottom,
        )

        try:
            row.draw_text(
                box_coords,
                display_name,
                max_fontsize=int(24 * self.SCALE),
                min_fontsize=int(12 * self.SCALE),
                fill=self.TEXT_COLOR,
                halign="left",
                valign="center",
            )
        except ValueError:
            row.draw_text(
                box_coords,
                "🏳️名字过于混沌🏳️",
                max_fontsize=int(20 * self.SCALE),
                min_fontsize=int(12 * self.SCALE),
                fill=self.TEXT_COLOR,
                halign="left",
                valign="center",
            )

        row.draw.text(
            (text_x, base_y + int(65 * self.SCALE)),
            f"今日发言: {user['count']} 条",
            fill=self.HIGHLIGHT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )
        chart_y = base_y + (item_h - chart.height) // 2
        row.paste(chart, (chart_x, chart_y), alpha=True)

        return row

    async def render_async(
        self,
        group_name: str,
        today_king: str,
        group_rank: int,
        users_data: list[dict[str, Any]],
    ) -> bytes:
        item_h, item_spacing = int(110 * self.SCALE), int(20 * self.SCALE)
        header_height = int(260 * self.SCALE)
        total_height = (
            header_height
            + len(users_data) * (item_h + item_spacing)
            + int(200 * self.SCALE)
        )

        main_img = BuildImage.new(
            "RGB", (self.RENDER_WIDTH, total_height), self.BG_COLOR
        )
        y = self.PADDING

        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(50 * self.SCALE),
            ),
            "🐉🐉🐉🐉🐉🐉🐉快来水快来水快来水🐉🐉🐉🐉🐉🐉🐉",
            max_fontsize=int(40 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        y += int(70 * self.SCALE)

        safe_group_name = self._safe_truncate(group_name, max_len=30)
        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(40 * self.SCALE),
            ),
            safe_group_name,
            max_fontsize=int(26 * self.SCALE),
            min_fontsize=int(16 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        y += int(50 * self.SCALE)

        info_text = f"今日水王: {today_king}\n本群排名: 第{group_rank}名"
        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(60 * self.SCALE),
            ),
            info_text,
            max_fontsize=int(20 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += int(80 * self.SCALE)

        tasks = [
            asyncio.to_thread(self._render_user_row, rank, user)
            for rank, user in enumerate(users_data, 1)
        ]
        row_images = await asyncio.gather(*tasks)

        base_y_offset = int(10 * self.SCALE)
        for row in row_images:
            main_img.paste(row, (0, y - base_y_offset), alpha=True)
            y += item_h + item_spacing

        now = datetime.now()
        footer_y = y + int(20 * self.SCALE)
        main_img.draw.text(
            (self.PADDING, footer_y),
            f"© 2020-{now.year} SakuraiSenrin. All rights reserved.",
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )
        time_footer_y = footer_y + int(30 * self.SCALE)
        main_img.draw.text(
            (self.PADDING, time_footer_y),
            f"生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )

        msg_y = time_footer_y + int(50 * self.SCALE)
        main_img.draw_text(
            (0, msg_y, self.RENDER_WIDTH, msg_y + int(30 * self.SCALE)),
            "------------哦嚯嚯！下一个水王会是你吗？٩(๑>◡<๑)۶凛凛很期待喔！------------",
            max_fontsize=int(18 * self.SCALE),
            fill=self.HIGHLIGHT_COLOR,
            halign="center",
            valign="center",
            font_families=[SYS_FONT_NAME],
        )

        final_img = main_img.crop(
            (0, 0, self.RENDER_WIDTH, msg_y + int(80 * self.SCALE))
        )
        return (await asyncio.to_thread(final_img.save, "PNG")).getvalue()


async def generate_water_rank_image_by_pillow(
    group_id: str, group_name: str, water_info_sequence: Sequence[WaterInfo]
) -> bytes | None:
    today_date = datetime.now().date()
    group_counts, user_counts, user_hourly = {}, {}, {}

    for msg in water_info_sequence:
        group_counts[msg.group_id] = group_counts.get(msg.group_id, 0) + 1
        if msg.group_id == group_id and msg.created_at.date() == today_date:
            uid = msg.user_id
            user_counts[uid] = user_counts.get(uid, 0) + 1
            if uid not in user_hourly:
                user_hourly[uid] = [0] * 24
            user_hourly[uid][msg.created_at.hour] += 1

    sorted_groups = sorted(group_counts.items(), key=lambda x: -x[1])
    group_rank = next(
        (i + 1 for i, (gid, _) in enumerate(sorted_groups) if gid == group_id), 999
    )

    sorted_users = sorted(user_counts.items(), key=lambda x: -x[1])[:10]
    if not sorted_users:
        return None

    king_uid, king_count = sorted_users[0]

    avatar_tasks = [QQAvatar.fetch_user(uid) for uid, _ in sorted_users]
    avatars = await asyncio.gather(*avatar_tasks)

    mock_trends = [3, 0, -1, None, 2, -5, 1, 0, None, -2]
    EXTREME_USERNAMES = [
        "꧁༺傲世★狂少༻꧂",  # 游戏特殊符号
        "👨‍👩‍👧‍👦👨‍💻🏳️‍⚧️🍎🌍",  # ZWJ 零宽连字组合 Emoji 与 旗帜
        "T̴h̷i̵s̸ ̵i̷s̴ ̵Z̷a̵l̸g̷o̷",  # Zalgo 溢出乱码文本（火星文）
        "مرحبا العالم (Arabic)",  # RTL (从右到左) 语言
        "สวัสดีชาวโลก (Thai)",  # 复杂垂直字形堆叠
        "Iñtërnâtiônàlizætiøn",  # 多语种拉丁文特殊发音符号
        "(╯°□°）╯︵ ┻━┻",  # 颜文字测试
        "𪚥𮧵𪾢𩇩 (Rare CJK)",  # 极度生僻的 CJK 汉字扩展区
        "This\nIs\tA\rTest",  # 恶意控制字符（换行/制表符）
        "슈퍼마리오 (Korean)",  # 韩文渲染
        "Я люблю тебя (Russian)",  # 西里尔字母
        "   空白 边界  测试   ",  # 连续空格与首尾空白
        "这是一个超级无敌长长长长长长长长长长长长长长长长长长的名字用来测试物理框防爆截断机制",
    ]

    users_data = [
        {
            "user_id": uid,
            "username": random.choice(EXTREME_USERNAMES),  # 🔥 随机抽取噩梦级测试用例
            "count": count,
            "hourly_data": user_hourly[uid],
            "avatar_img": avatars[i],
            "trend": mock_trends[i] if i < len(mock_trends) else 0,
        }
        for i, (uid, count) in enumerate(sorted_users)
    ]

    renderer = WaterRankRenderer()
    return await renderer.render_async(
        group_name, f"UID:{king_uid} ({king_count}次)", group_rank, users_data
    )


async def run_mock() -> bytes | None:
    group_id = "12345678"
    now = datetime.now()
    test_users = [str(random.randint(10000000, 999999999)) for _ in range(200)]

    mock_data = [
        WaterInfo(
            user_id=random.choice(test_users),
            group_id=group_id,
            created_at=now.replace(
                hour=random.randint(0, 23), minute=random.randint(0, 59)
            ),
        )
        for _ in range(30000)
    ]

    img_bytes = await generate_water_rank_image_by_pillow(
        group_id=group_id,
        group_name="❄️凛雪列車｜描摹重日冷影❄️ [分轨1群] (严禁发涩图违者女装)",
        water_info_sequence=mock_data,
    )

    return img_bytes


if __name__ == "__main__":
    img_bytes = asyncio.run(run_mock())
    if img_bytes:
        with open("test_water_rank.png", "wb") as f:
            f.write(img_bytes)
