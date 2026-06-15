"""Unified theme tokens for plugin docs demo renderers."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, replace

DEFAULT_IMPRESSION_COLOR = "#F06292"


@dataclass(frozen=True)
class DemoTheme:
    """Theme tokens shared by plugin docs renderers."""

    # Core colors
    page_bg: str
    panel_bg: str
    panel_soft_bg: str
    accent: str
    strong: str
    deep: str
    hint: str
    line: str

    # Demo / card colors
    shell_bg: str
    shell_border: str
    user_bubble: str
    bot_bubble: str
    system_bubble: str
    system_text: str
    system_label: str
    footer_bg: str
    inline_code_bg: str
    inline_code_text: str

    # Supporting palette
    indigo: str
    indigo_soft: str
    indigo_text: str
    muted_light: str

    # Showcase-specific colors
    grid_color: str
    decor_color: str
    footer_divider: str
    standee_anchor_fill: str
    hero_title: str
    hero_summary: str
    pill_blue_bg: str
    pill_blue_text: str
    pill_pink_bg: str
    pill_pink_text: str
    terminal_bg: str
    terminal_text: str
    terminal_param: str
    terminal_flag: str
    note_text: str
    note_success: str
    note_danger: str
    demo_heading: str
    bot_text: str
    system_line: str
    standee_anchor_shadow: tuple[int, int, int, int]
    bubble_shadow: tuple[int, int, int, int]
    card_shadow: tuple[int, int, int, int]

    # Shared radii / spacing
    outer_margin: int
    shell_radius: int
    panel_radius: int
    card_radius: int
    chip_radius: int
    inline_code_radius: int
    inline_code_pad_x: int
    inline_code_pad_y: int
    grid_spacing: int

    # Showcase layout tokens
    canvas_width: int
    hero_top: int
    hero_side_padding: int
    hero_bottom_padding: int
    hero_content_gap: int
    hero_text_gap: int
    hero_summary_line_height: int
    hero_title_shadow_offset_y: int
    hero_standee_size: int
    hero_standee_overlap: int
    pill_height: int
    pill_gap: int
    section_gap: int
    instruction_padding_x: int
    instruction_padding_y: int
    instruction_radius: int
    instruction_shadow_blur: int
    instruction_shadow_offset_y: int
    trigger_radius: int
    trigger_padding_x: int
    trigger_padding_y: int
    trigger_gap: int
    note_gap: int
    note_dot_size: int
    demo_heading_gap_top: int
    demo_heading_gap_bottom: int
    avatar_size: int
    avatar_gap: int
    bubble_gap: int
    bubble_radius: int
    bubble_padding_x: int
    bubble_padding_y: int
    bubble_line_height: int
    system_line_gap: int
    footer_gap_top: int
    footer_height: int


_BASE_LAYOUT_THEME = DemoTheme(
    page_bg="#FAFAFC",
    panel_bg="#FFFFFF",
    panel_soft_bg="#FFF0F6",
    accent="#F06292",
    strong="#A61E4D",
    deep="#1C1E26",
    hint="#868E96",
    line="#E9ECEF",
    shell_bg="#FFFFFF",
    shell_border="#EFE9ED",
    user_bubble="#FFFFFF",
    bot_bubble="#FFF0F6",
    system_bubble="#FAFAFC",
    system_text="#ADB5BD",
    system_label="#ADB5BD",
    footer_bg="#FAFAFC",
    inline_code_bg="#FFF7FA",
    inline_code_text="#7D3653",
    indigo="#5C7CFA",
    indigo_soft="#F1F4FF",
    indigo_text="#364FC7",
    muted_light="#F7F3F5",
    grid_color="#EBCBCE",
    decor_color="#F06292",
    footer_divider="#EBCBCE",
    standee_anchor_fill="#FFFFFF",
    hero_title="#1C1E26",
    hero_summary="#5C5F66",
    pill_blue_bg="#F1F4FF",
    pill_blue_text="#5C7CFA",
    pill_pink_bg="#FFF1F6",
    pill_pink_text="#E64980",
    terminal_bg="#FFF0F6",
    terminal_text="#1C1E26",
    terminal_param="#E64980",
    terminal_flag="#2F9E44",
    note_text="#868E96",
    note_success="#40C057",
    note_danger="#FA5252",
    demo_heading="#868E96",
    bot_text="#A61E4D",
    system_line="#ADB5BD",
    standee_anchor_shadow=(0, 0, 0, 16),
    bubble_shadow=(0, 0, 0, 24),
    card_shadow=(0, 0, 0, 24),
    outer_margin=40,
    shell_radius=32,
    panel_radius=28,
    card_radius=32,
    chip_radius=20,
    inline_code_radius=12,
    inline_code_pad_x=8,
    inline_code_pad_y=4,
    grid_spacing=32,
    canvas_width=1280,
    hero_top=64,
    hero_side_padding=88,
    hero_bottom_padding=64,
    hero_content_gap=48,
    hero_text_gap=24,
    hero_summary_line_height=56,
    hero_title_shadow_offset_y=2,
    hero_standee_size=304,
    hero_standee_overlap=24,
    pill_height=40,
    pill_gap=16,
    section_gap=48,
    instruction_padding_x=48,
    instruction_padding_y=40,
    instruction_radius=32,
    instruction_shadow_blur=24,
    instruction_shadow_offset_y=8,
    trigger_radius=12,
    trigger_padding_x=24,
    trigger_padding_y=16,
    trigger_gap=24,
    note_gap=24,
    note_dot_size=10,
    demo_heading_gap_top=48,
    demo_heading_gap_bottom=32,
    avatar_size=72,
    avatar_gap=24,
    bubble_gap=24,
    bubble_radius=24,
    bubble_padding_x=32,
    bubble_padding_y=24,
    bubble_line_height=48,
    system_line_gap=24,
    footer_gap_top=40,
    footer_height=72,
)


def normalize_hex_color(
    value: str | None,
    *,
    fallback: str = DEFAULT_IMPRESSION_COLOR,
) -> str:
    raw = (value or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3 and all(char in "0123456789abcdefABCDEF" for char in raw):
        raw = "".join(char * 2 for char in raw)
    if len(raw) != 6 or any(char not in "0123456789abcdefABCDEF" for char in raw):
        return fallback.upper()
    return f"#{raw.upper()}"


def build_demo_theme(impression_color: str | None = None) -> DemoTheme:
    base_hex = normalize_hex_color(impression_color)
    base_rgb = _hex_to_rgb(base_hex)
    accent_rgb = _tune_hls(base_rgb, saturation=max(_rgb_to_hls(base_rgb)[2], 0.58))
    dark_rgb = _tune_hls(base_rgb, lightness=0.16, saturation=0.18)
    strong_rgb = _tune_hls(base_rgb, lightness=0.27, saturation=0.44)
    hint_rgb = _mix(dark_rgb, (255, 255, 255), 0.55)
    line_rgb = _mix(base_rgb, (255, 255, 255), 0.82)
    soft_fill_rgb = _tune_hls(base_rgb, lightness=0.92, saturation=0.45)
    softer_fill_rgb = _tune_hls(base_rgb, lightness=0.965, saturation=0.22)
    shell_border_rgb = _mix(base_rgb, (255, 255, 255), 0.88)
    page_bg_rgb = _tune_hls(base_rgb, lightness=0.985, saturation=0.12)
    grid_rgb = _tune_hls(base_rgb, lightness=0.86, saturation=0.28)
    decor_rgb = _tune_hls(base_rgb, lightness=0.62, saturation=0.52)
    footer_divider_rgb = _tune_hls(base_rgb, lightness=0.82, saturation=0.20)
    anchor_fill_rgb = _mix(base_rgb, (255, 255, 255), 0.92)
    pill_alt_bg_rgb = _shifted_rgb(
        base_rgb, hue_shift=0.05, lightness=0.93, saturation=0.32
    )
    pill_alt_text_rgb = _shifted_rgb(
        base_rgb, hue_shift=0.05, lightness=0.36, saturation=0.48
    )
    neutral_hint = "#ADB5BD"
    shadow_alpha = 24

    return replace(
        _BASE_LAYOUT_THEME,
        page_bg=_rgb_to_hex(page_bg_rgb),
        panel_soft_bg=_rgb_to_hex(soft_fill_rgb),
        accent=_rgb_to_hex(accent_rgb),
        strong=_rgb_to_hex(strong_rgb),
        deep=_rgb_to_hex(dark_rgb),
        hint=_rgb_to_hex(hint_rgb),
        line=_rgb_to_hex(line_rgb),
        shell_border=_rgb_to_hex(shell_border_rgb),
        bot_bubble=_rgb_to_hex(soft_fill_rgb),
        system_bubble=_rgb_to_hex(softer_fill_rgb),
        system_text=neutral_hint,
        system_label=neutral_hint,
        footer_bg=_rgb_to_hex(softer_fill_rgb),
        inline_code_bg=_rgb_to_hex(soft_fill_rgb),
        inline_code_text=_rgb_to_hex(strong_rgb),
        indigo=_rgb_to_hex(pill_alt_text_rgb),
        indigo_soft=_rgb_to_hex(pill_alt_bg_rgb),
        indigo_text=_rgb_to_hex(pill_alt_text_rgb),
        muted_light=_rgb_to_hex(soft_fill_rgb),
        grid_color=_rgb_to_hex(grid_rgb),
        decor_color=_rgb_to_hex(decor_rgb),
        footer_divider=_rgb_to_hex(footer_divider_rgb),
        standee_anchor_fill=_rgb_to_hex(anchor_fill_rgb),
        hero_title=_rgb_to_hex(dark_rgb),
        hero_summary=_rgb_to_hex(_mix(dark_rgb, (255, 255, 255), 0.38)),
        pill_blue_bg=_rgb_to_hex(soft_fill_rgb),
        pill_blue_text=_rgb_to_hex(accent_rgb),
        pill_pink_bg=_rgb_to_hex(pill_alt_bg_rgb),
        pill_pink_text=_rgb_to_hex(pill_alt_text_rgb),
        terminal_bg=_rgb_to_hex(soft_fill_rgb),
        terminal_text=_rgb_to_hex(dark_rgb),
        terminal_param=_rgb_to_hex(accent_rgb),
        terminal_flag="#2F9E44",
        note_text=_rgb_to_hex(hint_rgb),
        demo_heading=_rgb_to_hex(hint_rgb),
        bot_text=_rgb_to_hex(dark_rgb),
        system_line=neutral_hint,
        standee_anchor_shadow=(*base_rgb, 18),
        bubble_shadow=(*base_rgb, shadow_alpha),
        card_shadow=(*base_rgb, shadow_alpha),
    )


def _rgb_to_hls(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    red, green, blue = rgb
    return colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)


def _tune_hls(
    rgb: tuple[int, int, int],
    *,
    lightness: float | None = None,
    saturation: float | None = None,
) -> tuple[int, int, int]:
    hue, current_lightness, current_saturation = _rgb_to_hls(rgb)
    return _hls_to_rgb(
        hue,
        lightness if lightness is not None else current_lightness,
        saturation if saturation is not None else current_saturation,
    )


def _shifted_rgb(
    rgb: tuple[int, int, int],
    *,
    hue_shift: float,
    lightness: float,
    saturation: float,
) -> tuple[int, int, int]:
    hue, _, _ = _rgb_to_hls(rgb)
    return _hls_to_rgb((hue + hue_shift) % 1.0, lightness, saturation)


def _hls_to_rgb(
    hue: float, lightness: float, saturation: float
) -> tuple[int, int, int]:
    red, green, blue = colorsys.hls_to_rgb(
        max(0.0, min(1.0, hue)),
        max(0.0, min(1.0, lightness)),
        max(0.0, min(1.0, saturation)),
    )
    return (
        round(red * 255),
        round(green * 255),
        round(blue * 255),
    )


def _mix(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    bounded = max(0.0, min(1.0, ratio))
    mixed = tuple(
        round(lhs + (rhs - lhs) * bounded) for lhs, rhs in zip(left, right, strict=True)
    )
    return mixed[0], mixed[1], mixed[2]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    normalized = normalize_hex_color(value)
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


BASE_THEME = build_demo_theme(DEFAULT_IMPRESSION_COLOR)
