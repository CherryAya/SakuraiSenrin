"""Unified theme tokens for plugin docs demo renderers."""

from dataclasses import dataclass


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
    mesh_pink: str
    mesh_blue: str
    hero_title: str
    hero_summary: str
    pill_blue_bg: str
    pill_blue_text: str
    pill_pink_bg: str
    pill_pink_text: str
    terminal_bg: str
    terminal_text: str
    terminal_param: str
    note_text: str
    note_success: str
    note_danger: str
    demo_heading: str
    bot_text: str
    system_line: str
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


BASE_THEME = DemoTheme(
    page_bg="#FAFAFC",
    panel_bg="#FFFFFF",
    panel_soft_bg="#F7F3F5",
    accent="#E64980",
    strong="#A61E4D",
    deep="#1C1E26",
    hint="#868E96",
    line="#E9ECEF",
    shell_bg="#FFFFFF",
    shell_border="#EFE9ED",
    user_bubble="#FFFFFF",
    bot_bubble="#FFF0F6",
    system_bubble="#FAFAFC",
    system_text="#CED4DA",
    system_label="#CED4DA",
    footer_bg="#FAFAFC",
    inline_code_bg="#FFF7FA",
    inline_code_text="#7D3653",
    indigo="#5C7CFA",
    indigo_soft="#F1F4FF",
    indigo_text="#364FC7",
    muted_light="#F7F3F5",
    mesh_pink="#FFE8F0",
    mesh_blue="#E8F0FF",
    hero_title="#1C1E26",
    hero_summary="#5C5F66",
    pill_blue_bg="#F1F4FF",
    pill_blue_text="#5C7CFA",
    pill_pink_bg="#FFF1F6",
    pill_pink_text="#E64980",
    terminal_bg="#212529",
    terminal_text="#E9ECEF",
    terminal_param="#3BC9DB",
    note_text="#868E96",
    note_success="#40C057",
    note_danger="#FA5252",
    demo_heading="#868E96",
    bot_text="#A61E4D",
    system_line="#CED4DA",
    bubble_shadow=(0, 0, 0, 10),
    card_shadow=(0, 0, 0, 10),
    outer_margin=40,
    shell_radius=32,
    panel_radius=28,
    card_radius=26,
    chip_radius=15,
    inline_code_radius=10,
    inline_code_pad_x=8,
    inline_code_pad_y=4,
    canvas_width=1280,
    hero_top=64,
    hero_side_padding=88,
    hero_bottom_padding=64,
    hero_content_gap=48,
    hero_text_gap=24,
    hero_summary_line_height=48,
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
    bubble_line_height=46,
    system_line_gap=24,
    footer_gap_top=40,
    footer_height=32,
)


PALETTE_ACCENTS = {
    "study": ("#FFE4B5", "#FFF0CF"),
    "wordbank-approval": ("#F8D0D2", "#FBE0E3"),
    "wordbank": ("#C9DEF3", "#D9EAFB"),
    "default": ("#E8DEF8", "#F0E8FB"),
}
