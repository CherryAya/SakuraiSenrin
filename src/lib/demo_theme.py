"""Unified senrinV3 theme tokens for plugin docs demo renderers."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, replace

DEFAULT_IMPRESSION_COLOR = "#F06292"
SENRIN_V3_THEME_NAME = "senrinV3"


@dataclass(frozen=True)
class DemoTheme:
    """Resolved theme tokens shared by plugin docs renderers."""

    theme_name: str
    impression_color: str

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
    system_border: str
    system_text: str
    system_label: str
    system_label_bg: str
    system_label_text: str
    footer_bg: str
    inline_code_bg: str
    inline_code_text: str
    avatar_text: str
    bot_avatar_bg: str
    bot_avatar_border: str

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
    hero_title_shadow: str
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
    showcase_accent_rail_bg: str
    showcase_support_rail_bg: str
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


@dataclass(frozen=True)
class DemoThemeDefinition:
    """Named theme definition with a default impression color."""

    name: str
    default_impression_color: str
    base_theme: DemoTheme


@dataclass(frozen=True)
class WordbankCardTheme:
    bg: str
    trigger_panel: str
    trigger_border: str
    response_panel: str
    response_border: str
    panel: str
    panel_soft: str
    header: str
    header_soft: str
    body: str
    muted: str
    accent: str
    accent_deep: str
    accent_soft: str
    border: str
    texture: str
    badge_text: str
    folded_shadow_fill: str
    folded_outline: str
    tree_line: str
    panel_outline: str
    page_more_shadow_fill: str
    page_more_outline: str
    success_fill: str
    success_text: str
    success_outline: str
    warning_fill: str
    warning_text: str
    warning_outline: str
    danger_fill: str
    danger_text: str
    danger_outline: str
    scope_global_fill: str
    scope_global_text: str
    scope_global_outline: str
    scope_local_fill: str
    scope_local_text: str
    scope_local_outline: str
    scope_private_fill: str
    scope_private_text: str
    scope_private_outline: str
    neutral_chip_fill: str
    neutral_chip_text: str
    neutral_chip_outline: str
    data_chip_fill: str
    data_chip_text: str
    data_chip_outline: str


@dataclass(frozen=True)
class WordbankLeaderboardTheme:
    bg: str
    bg_strong: str
    panel: str
    panel_soft: str
    panel_pink: str
    header: str
    body: str
    muted: str
    accent: str
    accent_deep: str
    accent_soft: str
    border: str
    hero: str
    gold: str
    silver: str
    bronze: str
    chip_bg: str
    chip_fg: str
    halo_fill: str
    halo_outline: str
    violet_panel: str
    violet_panel_outline: str
    violet_text: str
    amber_panel: str
    amber_panel_outline: str
    amber_text: str
    hero_summary_fill: str
    hero_summary_text: str
    hero_stat_fill: str
    row_pink_fill: str
    row_pink_text: str
    row_blue_fill: str
    row_blue_text: str
    row_amber_fill: str
    row_amber_text: str
    row_mint_fill: str
    row_mint_text: str
    avatar_violet_fill: str
    avatar_violet_outline: str
    avatar_violet_text: str
    avatar_mint_fill: str
    avatar_mint_outline: str
    avatar_mint_text: str
    avatar_pink_fill: str
    avatar_pink_outline: str
    white: str


@dataclass(frozen=True)
class WordbankTreemapTheme:
    bg: str
    panel: str
    panel_alt: tuple[str, ...]
    border: str
    header: str
    body: str
    muted: str
    accent: str
    badge_bg: str
    badge_text: str
    number_bg: str
    number_text: str
    card_bg: str
    card_accent: str
    divider: str
    white: str
    highlight_fill: str


@dataclass(frozen=True)
class WaterImageTheme:
    white: str
    page_bg: str
    hero_bg: str
    panel_bg: str
    panel_soft_bg: str
    profile_panel_soft_bg: str
    title_panel_bg: str
    item_bg: str
    item_bg_alt: str
    info_card_bg: str
    header_bg: str
    header_text: str
    subtext_color: str
    text_color: str
    highlight_color: str
    muted_color: str
    accent: str
    strong: str
    deep: str
    profile_deep: str
    hint: str
    line: str
    blue: str
    mint: str
    gold: str
    season: str
    success: str
    my_value: str
    group_value: str
    badge_bg: str
    badge_fg: str
    chip_bg: str
    chip_alt_bg: str
    title_main: str
    title_sub: str
    title_hint: str
    global_color: str
    matrix_color: str
    global_panel: str
    matrix_panel: str
    matrix_group_active_bg: str
    matrix_group_inactive_bg: str
    avatar_fallback_bg: str
    avatar_fallback_fg: str
    group_avatar_fallback_bg: str
    group_avatar_fallback_fg: str
    gloss_hero_tone: str
    gloss_panel_tone: str
    gloss_soft_tone: str
    gloss_profile_tone: str
    gloss_profile_soft_tone: str
    gloss_global_tone: str
    gloss_season_tone: str
    stat_total_bg: str
    stat_active_bg: str
    stat_delta_positive_bg: str
    stat_delta_negative_bg: str
    podium_gold_bg: str
    podium_gold_badge: str
    podium_silver_bg: str
    podium_silver_badge: str
    podium_bronze_bg: str
    podium_bronze_badge: str
    podium_badge_text: str
    row_default_badge_fill: str
    rank_row_fill: str
    rank_spark_fallback: str
    overview_highlight_bar: str
    progress_global_bg: str
    progress_season_bg: str
    achievement_chip_bg: str
    achievement_chip_text: str
    history_divider: str
    left_chip_alt_bg: str
    left_chip_meta: str
    right_chip_alt_bg: str
    right_chip_meta: str
    status_global_panel: str
    status_season_panel: str
    status_global_title: str
    status_season_title: str
    text_color_dark: str
    meta_color_dark: str
    trend_new: str
    trend_up: str
    trend_down: str
    trend_flat: str
    tile_base_colors: tuple[str, ...]


@dataclass(frozen=True)
class AdminInviteImageTheme:
    bg_color: tuple[int, int, int]
    text_color: tuple[int, int, int]
    item_bg_color: tuple[int, int, int]
    sub_text_color: tuple[int, int, int]
    highlight_color: tuple[int, int, int]


@dataclass(frozen=True)
class AvatarFallbackTheme:
    bg_color: tuple[int, int, int]
    text_color: tuple[int, int, int]


_SENRIN_V3_BASE_THEME = DemoTheme(
    theme_name=SENRIN_V3_THEME_NAME,
    impression_color=DEFAULT_IMPRESSION_COLOR,
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
    system_bubble="#FFF8FA",
    system_border="#D4EEF6",
    system_text="#6B7280",
    system_label="#6B7280",
    system_label_bg="#3BC9DB",
    system_label_text="#FFFFFF",
    footer_bg="#FAFAFC",
    inline_code_bg="#FFF7FA",
    inline_code_text="#7D3653",
    avatar_text="#FFFFFF",
    bot_avatar_bg="#F7FAFF",
    bot_avatar_border="#D8E3FF",
    indigo="#5C7CFA",
    indigo_soft="#F1F4FF",
    indigo_text="#364FC7",
    muted_light="#F7F3F5",
    grid_color="#EBCBCE",
    decor_color="#F06292",
    footer_divider="#EBCBCE",
    standee_anchor_fill="#FFFFFF",
    hero_title="#1C1E26",
    hero_title_shadow="#FFFFFF",
    hero_summary="#5C5F66",
    pill_blue_bg="#F1F4FF",
    pill_blue_text="#5C7CFA",
    pill_pink_bg="#FFF1F6",
    pill_pink_text="#E64980",
    terminal_bg="#FFF0F6",
    terminal_text="#1C1E26",
    terminal_param="#E64980",
    terminal_flag="#2F9E44",
    note_text="#6B7280",
    note_success="#40C057",
    note_danger="#FA5252",
    demo_heading="#5F6670",
    bot_text="#A61E4D",
    system_line="#ADB5BD",
    showcase_accent_rail_bg="#FFF4F7",
    showcase_support_rail_bg="#F1F4FF",
    standee_anchor_shadow=(240, 98, 146, 18),
    bubble_shadow=(135, 142, 216, 16),
    card_shadow=(135, 142, 216, 20),
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
    instruction_padding_y=48,
    instruction_radius=32,
    instruction_shadow_blur=24,
    instruction_shadow_offset_y=2,
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

SENRIN_V3_THEME = DemoThemeDefinition(
    name=SENRIN_V3_THEME_NAME,
    default_impression_color=DEFAULT_IMPRESSION_COLOR,
    base_theme=_SENRIN_V3_BASE_THEME,
)

SENRIN_V3_WORDBANK_CARD_THEME = WordbankCardTheme(
    bg="#FDFBF7",
    trigger_panel="#FFF0F5",
    trigger_border="#F8D6E4",
    response_panel="#FFFDFE",
    response_border="#F0E6EC",
    panel="#FFFDFE",
    panel_soft="#FFF7FB",
    header="#574B59",
    header_soft="#866C84",
    body="#5C5260",
    muted="#AA9EAE",
    accent="#FFA6C9",
    accent_deep="#E1759C",
    accent_soft="#FFF5F8",
    border="#EEE6EA",
    texture="#F5EBEF",
    badge_text="#FFFFFF",
    folded_shadow_fill="#FDEFF5",
    folded_outline="#F8D9E6",
    tree_line="#F3C7D7",
    panel_outline="#F0E8ED",
    page_more_shadow_fill="#FCEAF2",
    page_more_outline="#F7D8E6",
    success_fill="#ECF8F1",
    success_text="#4E7A64",
    success_outline="#CDEAD8",
    warning_fill="#FFF1DD",
    warning_text="#9B6C38",
    warning_outline="#F8D7AC",
    danger_fill="#FFE9EE",
    danger_text="#A56B7A",
    danger_outline="#F8CAD5",
    scope_global_fill="#FFE6F0",
    scope_global_text="#B7668A",
    scope_global_outline="#F7C8D9",
    scope_local_fill="#E8F3FF",
    scope_local_text="#5E7EA5",
    scope_local_outline="#CFE1F8",
    scope_private_fill="#F0ECF8",
    scope_private_text="#756B90",
    scope_private_outline="#DDD5EE",
    neutral_chip_fill="#F8EEF4",
    neutral_chip_text="#8A7286",
    neutral_chip_outline="#E7D8E3",
    data_chip_fill="#FFF4E8",
    data_chip_text="#9A6F54",
    data_chip_outline="#F0D8C3",
)

SENRIN_V3_WORDBANK_LEADERBOARD_THEME = WordbankLeaderboardTheme(
    bg="#FFF5FA",
    bg_strong="#FFE3EF",
    panel="#FFFDFE",
    panel_soft="#FFF2F7",
    panel_pink="#FFE9F1",
    header="#4E2135",
    body="#72455A",
    muted="#B37A92",
    accent="#E45C8C",
    accent_deep="#C94073",
    accent_soft="#FFD8E7",
    border="#F3C5D9",
    hero="#FFF7FB",
    gold="#E4A955",
    silver="#A284E8",
    bronze="#7AC4B1",
    chip_bg="#FFF0F6",
    chip_fg="#A85074",
    halo_fill="#FFEAF4",
    halo_outline="#FFE0EC",
    violet_panel="#FFF2FB",
    violet_panel_outline="#9C62E8",
    violet_text="#8868D7",
    amber_panel="#FFF5ED",
    amber_panel_outline="#D9863D",
    amber_text="#B97728",
    hero_summary_fill="#FFF1D7",
    hero_summary_text="#B97728",
    hero_stat_fill="#FFE9F2",
    row_pink_fill="#FFE6F0",
    row_pink_text="#C84B79",
    row_blue_fill="#EEF1FF",
    row_blue_text="#6F61CC",
    row_amber_fill="#FFF3E6",
    row_amber_text="#D68432",
    row_mint_fill="#EAFBF7",
    row_mint_text="#4D9E89",
    avatar_violet_fill="#F8F2FF",
    avatar_violet_outline="#ECE1FF",
    avatar_violet_text="#8868D7",
    avatar_mint_fill="#EFFAF7",
    avatar_mint_outline="#DDF6EE",
    avatar_mint_text="#4DA88D",
    avatar_pink_fill="#FFE2EE",
    avatar_pink_outline="#FFE6F0",
    white="#FFFFFF",
)

SENRIN_V3_WORDBANK_TREEMAP_THEME = WordbankTreemapTheme(
    bg="#FFF8FA",
    panel="#FFF1F5",
    panel_alt=(
        "#FFEAF1",
        "#FFF1E8",
        "#EEF6FF",
        "#F4F1FF",
        "#EFFBF5",
        "#FFF7DE",
    ),
    border="#F0D5E0",
    header="#2E2533",
    body="#4F4554",
    muted="#867884",
    accent="#D96E95",
    badge_bg="#FFFFFF",
    badge_text="#B44B70",
    number_bg="#FFFDFE",
    number_text="#C25279",
    card_bg="#FFFCFD",
    card_accent="#AF5477",
    divider="#F2DCE5",
    white="#FFFFFF",
    highlight_fill="#FFF5F8",
)

SENRIN_V3_WATER_IMAGE_THEME = WaterImageTheme(
    white="#FFFFFF",
    page_bg="#FFF4F7",
    hero_bg="#FFE8F0",
    panel_bg="#FFF9FB",
    panel_soft_bg="#FFF3F8",
    profile_panel_soft_bg="#FFF5F9",
    title_panel_bg="#FFE8F0",
    item_bg="#FFF9FB",
    item_bg_alt="#FFF1F6",
    info_card_bg="#FFF0F5",
    header_bg="#FFE3ED",
    header_text="#7A2F4A",
    subtext_color="#B05A79",
    text_color="#8F3D56",
    highlight_color="#E45A84",
    muted_color="#A77A88",
    accent="#7A2F4A",
    strong="#D84E7A",
    deep="#401828",
    profile_deep="#3F1A29",
    hint="#AA6B82",
    line="#F6D9E6",
    blue="#5B8CFF",
    mint="#67BAA6",
    gold="#D4973C",
    season="#D4973C",
    success="#43A396",
    my_value="#8B4FD4",
    group_value="#2F83C9",
    badge_bg="#FFF0C7",
    badge_fg="#9A6723",
    chip_bg="#F3E8FF",
    chip_alt_bg="#E6F4FF",
    title_main="#5E2138",
    title_sub="#7A2F4A",
    title_hint="#A54A6B",
    global_color="#4F7DF3",
    matrix_color="#F28A3B",
    global_panel="#F0F7FF",
    matrix_panel="#FFF0F6",
    matrix_group_active_bg="#FFF0F6",
    matrix_group_inactive_bg="#F8EEF4",
    avatar_fallback_bg="#FFDDE9",
    avatar_fallback_fg="#D84E7A",
    group_avatar_fallback_bg="#FFE3ED",
    group_avatar_fallback_fg="#7A2F4A",
    gloss_hero_tone="#FFF6FA",
    gloss_panel_tone="#FFF7FB",
    gloss_soft_tone="#FFF6FA",
    gloss_profile_tone="#F7EAF1",
    gloss_profile_soft_tone="#F2E8F3",
    gloss_global_tone="#D7E6FF",
    gloss_season_tone="#FFE8D0",
    stat_total_bg="#FFF0F6",
    stat_active_bg="#F0F6FF",
    stat_delta_positive_bg="#F1FFF9",
    stat_delta_negative_bg="#FFF0F6",
    podium_gold_bg="#FFE9C7",
    podium_gold_badge="#E0A141",
    podium_silver_bg="#EEE8FF",
    podium_silver_badge="#8D7AD8",
    podium_bronze_bg="#E8F8F3",
    podium_bronze_badge="#57A89A",
    podium_badge_text="#FFFFFF",
    row_default_badge_fill="#F4D8E5",
    rank_row_fill="#FFF9FB",
    rank_spark_fallback="#C0829B",
    overview_highlight_bar="#F5A340",
    progress_global_bg="#E5EEFF",
    progress_season_bg="#FCEEDC",
    achievement_chip_bg="#FFEFD6",
    achievement_chip_text="#B0712A",
    history_divider="#EFD2DD",
    left_chip_alt_bg="#EFE4FC",
    left_chip_meta="#5A3A74",
    right_chip_alt_bg="#DDF0FF",
    right_chip_meta="#355A78",
    status_global_panel="#F5F9FF",
    status_season_panel="#FFF8F1",
    status_global_title="#1E40AF",
    status_season_title="#B45309",
    text_color_dark="#151015",
    meta_color_dark="#382430",
    trend_new="#F0B36D",
    trend_up="#E96A96",
    trend_down="#66B3A5",
    trend_flat="#B8A1AE",
    tile_base_colors=("#E987AE", "#F1A58E", "#C8B5FF", "#9FDCE8"),
)

SENRIN_V3_ADMIN_INVITE_IMAGE_THEME = AdminInviteImageTheme(
    bg_color=(255, 217, 222),
    text_color=(180, 76, 76),
    item_bg_color=(255, 240, 245),
    sub_text_color=(200, 110, 110),
    highlight_color=(220, 90, 100),
)

SENRIN_V3_AVATAR_FALLBACK_THEME = AvatarFallbackTheme(
    bg_color=(255, 225, 230),
    text_color=(180, 76, 76),
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


def get_demo_theme(
    *,
    theme_name: str = SENRIN_V3_THEME.name,
    impression_color: str | None = None,
) -> DemoTheme:
    if theme_name != SENRIN_V3_THEME.name:
        msg = f"Unsupported demo theme: {theme_name}"
        raise ValueError(msg)
    return _resolve_senrin_v3_theme(impression_color)


def build_demo_theme(impression_color: str | None = None) -> DemoTheme:
    """Backward-compatible alias for resolving the default demo theme."""

    return get_demo_theme(impression_color=impression_color)


def _resolve_senrin_v3_theme(impression_color: str | None = None) -> DemoTheme:
    base_hex = normalize_hex_color(
        impression_color,
        fallback=SENRIN_V3_THEME.default_impression_color,
    )
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
    showcase_accent_rail_rgb = _tune_hls(
        base_rgb,
        lightness=0.972,
        saturation=0.30,
    )
    showcase_support_rail_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.05,
        lightness=0.955,
        saturation=0.20,
    )
    bot_avatar_bg_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.05,
        lightness=0.975,
        saturation=0.14,
    )
    bot_avatar_border_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.05,
        lightness=0.89,
        saturation=0.24,
    )
    neutral_hint = "#ADB5BD"
    canvas_shadow_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.04,
        lightness=0.68,
        saturation=0.22,
    )
    block_shadow_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.04,
        lightness=0.74,
        saturation=0.18,
    )
    badge_shadow_rgb = _shifted_rgb(
        base_rgb,
        hue_shift=0.02,
        lightness=0.62,
        saturation=0.30,
    )

    return replace(
        _SENRIN_V3_BASE_THEME,
        impression_color=base_hex,
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
        system_border=_rgb_to_hex(_mix(base_rgb, (221, 244, 248), 0.84)),
        system_text=neutral_hint,
        system_label=neutral_hint,
        system_label_bg=_rgb_to_hex(
            _tune_hls(base_rgb, lightness=0.54, saturation=0.68)
        ),
        system_label_text="#FFFFFF",
        footer_bg=_rgb_to_hex(softer_fill_rgb),
        inline_code_bg=_rgb_to_hex(soft_fill_rgb),
        inline_code_text=_rgb_to_hex(strong_rgb),
        bot_avatar_bg=_rgb_to_hex(bot_avatar_bg_rgb),
        bot_avatar_border=_rgb_to_hex(bot_avatar_border_rgb),
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
        showcase_accent_rail_bg=_rgb_to_hex(showcase_accent_rail_rgb),
        showcase_support_rail_bg=_rgb_to_hex(showcase_support_rail_rgb),
        standee_anchor_shadow=(*badge_shadow_rgb, 16),
        bubble_shadow=(*block_shadow_rgb, 15),
        card_shadow=(*canvas_shadow_rgb, 20),
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
    hue: float,
    lightness: float,
    saturation: float,
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


DEFAULT_DEMO_THEME = get_demo_theme()
BASE_THEME = DEFAULT_DEMO_THEME
