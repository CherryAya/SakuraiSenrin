# Plugin Demo Visual Checklist

Use this checklist after regenerating plugin demo PNG files.

## Automated Checks

- Run `uv run python scripts/plugin_docs.py validate`.
- Run `uv run pytest tests/test_plugin_docs.py`.
- Confirm every demo image is `1280px` wide and has a non-zero, content-driven height.

## Visual Checks

- Alignment: title, chips, bubbles, avatars, panel, and footer share consistent edges.
- Spacing: header, conversation panel, bubbles, and footer have balanced whitespace.
- Bounds: no text, avatar, chip, bubble, or footer content leaves its container.
- Overlap: no color block covers text, avatars, bubbles, panel borders, or footer text.
- Text: long commands and replies wrap cleanly without clipping or touching edges.
- Message semantics: one sent message with newlines uses one speaker line plus continuation lines; multiple sent messages repeat `USER:`, `BOT:`, or `SYSTEM:`.
- Bubbles: USER, BOT, and SYSTEM bubbles fit content without looking oversized.
- Character art: the Senrin standee must stay in the header whitespace and never compete with title, chips, or command text.
- Avatar crop: BOT avatars should show a recognizable face crop and stay aligned with the first line of the paired bubble.
- Color: keep the visible palette restrained to white, sakura pink, indigo, muted gray, and soft system yellow.
- Hierarchy: plugin title is strongest, feature title is secondary, command and footer are muted.
- Consistency: all demo files use the same radius, typography, avatar size, and footer structure.
- Readability: shrink representative images to chat-window width and confirm commands and replies remain readable.

## Required Sample Review

- `src/plugins/help/docs/demos/help-index.png`
- `src/plugins/admin/docs/invite/demos/admin-invite-reply-shortcut.png`
- `src/plugins/water/docs/demos/water-admin-maintenance.png`
- `src/hooks/docs/processor/demos/hook-processor-runtime-check.png`
