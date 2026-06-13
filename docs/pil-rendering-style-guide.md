# PIL Rendering Style Guide

This guide defines the project-level visual and engineering rules for PIL-based
PNG renderers. It is based on the `water` plugin's daily tile leaderboard and
period leaderboard renderers, but the rules are meant for all plugins that
render information cards.

## Design Language

- Use a soft card UI: pale sakura backgrounds, rounded panels, light borders,
  muted text, and small high-signal accent areas.
- Keep the palette restrained. A renderer should usually use one warm base
  palette, one cool support palette, and semantic colors for trends or status.
- Prefer layered information cards over large decorative illustrations. Every
  visual block should carry data, hierarchy, or grouping.
- Use white space deliberately. Dense rows are acceptable, but edges, card
  padding, avatars, labels, charts, and footer text must align to a clear grid.
- Add subtle texture only when it improves depth. The `water` period board uses
  light gloss lines; this is acceptable. Heavy shadows, gradients, and noisy
  textures are not the default style.

## Tokens

Define renderer-local tokens near the top of the renderer or in a small theme
object. Do not scatter raw colors and sizes through drawing code.

Recommended color roles:

- `page_bg`: outer page background, usually `#FFF4F7` or a plugin-specific soft
  equivalent.
- `panel_bg`: primary card background, usually near white.
- `panel_soft_bg`: secondary card background for grouped lists.
- `accent`: section label, metadata, and low-emphasis highlight text.
- `strong`: primary metric, active bar, and important count color.
- `deep`: main title and primary row text.
- `hint`: timestamps, axis labels, secondary metadata, and footer text.
- `line`: separators, chart axes, and faint borders.
- `success`, `warning`, `danger`, `neutral`: semantic status colors.

Recommended rank and trend semantics:

- Rank 1: warm gold background and badge.
- Rank 2: lavender background and badge.
- Rank 3: mint background and badge.
- Positive trend: sakura pink.
- Negative trend: teal/green.
- New item: warm orange.
- No movement: gray-purple.

Recommended sizing roles:

- `scale`: explicit multiplier for high-resolution output.
- `width`: fixed card width for predictable chat rendering.
- `pad`: outer padding.
- `gap`: vertical rhythm between major cards.
- `row_h`, `row_gap`: list rhythm.
- `radius_l`, `radius_m`, `radius_s`: large panels, row cards, chips.

## Layout Patterns

- Build images top-down with a single `y` cursor. Major sections should update
  `y` by their measured height plus `gap`.
- Use a fixed outer width and content-safe horizontal padding. Do not position
  text against the raw image edge.
- Use full-width rounded panels for major sections: header, champion/summary,
  list, overview chart, and footer.
- Use rows for ranked lists. A row should have rank badge, avatar or fallback,
  title, secondary stats, compact chart, and trend/status chip.
- Keep footers low contrast. Generated time and copyright should never compete
  with content.
- For chat readability, prefer vertical card layouts over very wide dashboards.

## Components

### Header / Hero

- Include title, time range or context, and one compact badge if useful.
- Use stat cards for 2-4 top-level metrics. Each stat card should include a
  short label and a visually stronger value.
- Header decoration must not reduce text contrast or make the title harder to
  scan.

### Rank Row

- Use a pill or small rounded rectangle for rank.
- Use circular avatars. If avatar fetching fails, render a deterministic
  fallback with an initial, rank number, or generic label.
- Keep username and primary stats left-aligned; put compact charts and trend
  chips on the right.
- The top three rows may use distinct backgrounds. All lower rows should share a
  consistent neutral style.

### Badges And Chips

- Chips should be short and semantic: period badge, rank badge, trend, scope, or
  status.
- Chips require enough horizontal padding to avoid text touching rounded edges.
- Avoid using chips as decoration-only elements.

### Charts

- Normalize chart values against the local maximum, not against an arbitrary
  global constant, unless the chart explicitly compares against a fixed scale.
- Preserve a minimum visible height or alpha for zero/near-zero values.
- Highlight one important element at most, such as peak hour.
- For 24-hour activity, use one of these patterns:
  - Tile wall: 2 rows x 12 columns, each tile labeled `00`-`23`, alpha or fill
    intensity maps to activity.
  - Mini distribution: 24 compact rounded bars for row-level comparison.
  - Overview histogram: 24 bars with sparse labels at `00`, `06`, `12`, `18`,
    and `23`.

## Typography And Text

- Use `MAPLE_FONT_NAME` with `BuildImage.draw_text` for CJK-aware fitting where
  possible.
- Use `MAPLE_FONT_PATH` with `ImageFont.truetype` for direct `ImageDraw` text.
- Always provide `ImageFont.load_default()` fallback for missing font files.
- All user-facing strings must come from i18n (`tr(...)`) unless the text is
  purely numeric or structural.
- Normalize or truncate uncontrolled user text. Replace newlines and tabs before
  drawing usernames, group names, and labels.
- Prefer adaptive font size or explicit truncation over letting text overflow a
  panel.

## Engineering Rules

- Renderer functions should return PNG `bytes` or `None` when there is no
  renderable data.
- Keep data preparation outside drawing code. Drawing functions should receive
  plain dataclasses or dictionaries that already contain display-ready values.
- Do network and database work before rendering. During rendering, use fallback
  assets rather than making additional external calls.
- Use `asyncio.to_thread` for CPU-heavy or PIL-heavy rendering when called from
  async handlers.
- Catch renderer-level exceptions at the public entrypoint, log them, and return
  a text fallback or `None` where the caller already handles empty images.
- Avoid importing plugin bootstrap modules from tests or standalone preview
  scripts; import renderer submodules through the same isolation pattern used by
  tests when needed.

## Testing And Review

- Add smoke tests that assert the renderer returns PNG bytes beginning with the
  PNG signature.
- Cover empty data, missing avatar, long username/group name, zero chart values,
  top-three rows, and trend variants.
- For plugin command images, also test the handler fallback path when rendering
  returns `None`.
- Review generated images at chat-window width, not only at full resolution.
- Check alignment, clipping, text contrast, avatar crop, chart readability, and
  footer hierarchy before accepting a new renderer.

