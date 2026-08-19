# SakuraiSenrin Project Guide

Version: v1.2  
Last Updated: 2026-07-01  
Scope: `src/plugins/**`, `src/hooks/**`, `tests/plugins/**`

---

## Project Overview

This is a NoneBot2-based QQ bot project with a plugin architecture. Your role is to help develop, maintain, and test plugins while following the architectural constraints and code quality standards defined here.

## Core Architecture

### Startup Flow
1. Entry: `bot.py`
2. Initialization: `init_fonts()` → `nonebot.init()` → OneBot V11 adapter registration
3. `on_startup`: `init_db()` initializes `core/log/snapshot` databases
4. `on_bot_connect`: Repository `warm_up()` and API sync for user/group caches
5. Plugin loading: `nonebot.load_plugins("src/hooks/")` → `nonebot.load_plugins("src/plugins/")`

### Runtime Interception (`src/hooks/processor.py`)
The `run_preprocessor` executes:
1. Runtime sync: user, group, group member cache backfill
2. Runtime checks: global ignore, superuser bypass, blacklist blocking, group authorization, mute-all check
3. Plugins can bypass with `PluginMetadata.extra.no_check` (system-level only)

### Data Layer Separation
1. `core_db`: Main business entities (user/group/member/invitation/blacklist/plugin switches)
2. `log_db` (sharded): Audit logs and plugin usage logs
3. `snapshot_db` (sharded): Name/nickname change snapshots
4. Repository layer: `cache -> diff -> writer/db` (plugins should not write raw SQL)

---

## Plugin Development Rules

### Mandatory Layering

Plugins MUST follow this structure:

```
src/plugins/<plugin_name>/
  __init__.py               # Plugin entry (multi-file plugins)
  docs/README.MD            # Required for COMMAND plugins; optional for PASSIVE/EVENT/CRON
  handlers/                 # Command parsing, event routing, interaction text
  services/                 # Business rules, flow orchestration, idempotency
  database/                 # Optional: plugin-specific data models
    instances.py
    tables.py
    ops.py
    repo.py
    types.py
    writers.py
```

**Rules:**
- Entry file: matcher registration, metadata declaration, thin orchestration only
- `handlers/`: command parsing, event routing, interaction messages
- `services/`: business logic, flow control, idempotency
- `database/`: only when plugin has independent persistence needs
- NO complex SQL, cross-module state machines, or large procedural logic in handlers

### Naming Standards
1. Plugin directories: lowercase semantic names (`water`, `notice`)
2. Command plugins: use `on_command` / `CommandGroup` with centralized prefix/aliases
3. File names: express responsibility, NO `misc.py` or `tmp.py`
4. Single-file plugins: can be `src/plugins/*.py` with embedded docs

---

## Plugin Metadata Contract (Mandatory)

Every loadable plugin MUST declare `PluginMetadata` with `extra` containing:

```python
from src.lib.plugin_meta import create_plugin_metadata

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,  # from src/lib/consts.py
        "permission": Permission.SUPERUSER,  # from src/database/core/consts.py
        "docs": {
            "kind": "plugin",
            "source": {
                "kind": "readme",
                "readme_path": str(DOCS_SOURCE),
            },
            "tree": {
                "slug": "notice.user",  # MUST be globally unique
                "parent_slug": "notice",  # Required for child nodes
                "category": "system",
                "order": 100,
            },
            "visibility": {
                "visible": False,
                "hidden": False,
                "internal": False,
            },
            "permission": Permission.SUPERUSER,
            "aliases": ("用户事件处理", "notice.user"),
        },
    },
)
```

**Required Fields:**
- `author`, `version`, `trigger`, `permission`, `docs`
- `docs.kind`, `docs.source`, `docs.tree.slug`, `docs.visibility.visible`
- `docs.tree.parent_slug` (for child nodes)
- `docs.permission`, `docs.aliases` (recommended)

**Optional:**
- `no_check`: ONLY for system notification/protection plugins that need to bypass runtime interception

**Constraints:**
- Default: do NOT set `no_check`
- Admin commands: MUST set explicit permission (`SUPERUSER` or group admin role checks)
- All plugins MUST implement `extra.docs` (non-compliant otherwise)
- NO independent `usage` text; help docs unified via `extra.docs` schema

### Documentation Requirements
1. Multi-file COMMAND plugins: MUST provide `docs/README.MD`
2. Single-file plugins: docs can be embedded in `.py` file
3. PASSIVE plugins: can simplify README but MUST provide queryable node
4. Package-level `__init__.py` used only for imports/`__all__` is exempt from `extra.docs`

---

## Message Security (CRITICAL)

### OneBot Message Injection Constraints (Mandatory)

**NEVER use `Message(...)` directly with text strings.** This causes CQ code re-parsing and message injection vulnerabilities.

**MUST:**
1. Pure text messages: use `MessageSegment.text(...)` explicitly
2. Images/mentions/replies: explicitly assemble `MessageSegment.image(...)`, `MessageSegment.at(...)`, `MessageSegment.reply(...)`
3. Composite messages: build segment-by-segment, NO raw CQ text into `Message(...)`
4. Empty construction: `Message()` ONLY for "create empty, then append segments" pattern
5. User input/README/i18n/database/external data: treat as untrusted, MUST use explicit `MessageSegment` assembly

**ALLOWED:**
- `Message` import for type hints, return type annotations, `CommandArg()` / `Arg()` parameter declarations, `isinstance` checks, or message object iteration

**FORBIDDEN:**
- `Message(raw_text)` where `raw_text` contains user input or interpolated strings
- Constructing CQ codes manually and passing to `Message(...)`

### Unified Message Plan Layer (Mandatory)

**MUST:**
1. Plugins/hooks MUST construct `DeliveryPlan` and use `deliver_message_plan(...)` for official output
2. Plugins/hooks MUST NOT directly import or call `deliver_single_message(...)` or `resolve_delivery_target(...)` as official output entry
3. Multiple messages/waiting tips/forward merging/reply segments/image segments: express as `MessagePlanEntry` / blocks first, let unified delivery layer decide final send form
4. `deliver_single_message(...)` ONLY allowed in infrastructure, compatibility layer, or inside unified message plan layer

### Message Delivery & Forward Merging (Mandatory)

All "send messages", "reuse historical messages", "send forward merging" capabilities unified in:
- `src/lib/message_plan.py`
- `src/lib/message_delivery.py`
- `src/lib/message_assets.py`
- `src/lib/message_api_hooks.py`
- `src/lib/onebot_forward.py`

Detailed specs: `docs/development/message-delivery.md`

**MUST:**
1. Plugins output `DeliveryPlan`, let `deliver_message_plan(...)` decide single/batch/forward
2. Plugins decide "what logical content to send", NOT "should this be single/batch/forward"
3. Plugins MUST NOT call `send_custom_forward(...)`, `deliver_forward_messages(...)` directly
4. Plugins MUST NOT use `send_group_forward_msg` / `send_private_forward_msg` as regular business implementation
5. Plugins MUST NOT retain "send to self first, then forward" business path (staging ONLY in `ensure_forward_node(...)` for platform limits)
6. `message_asset` is send asset cache, NOT business database; plugins MUST NOT directly query/write its tables
7. Messages with `reply`, `at`, or context-binding semantics CANNOT be reused globally for cache hits
8. Forward merging reuse MUST be order-sensitive; NO sparse reuse skipping middle nodes
9. Changes to node order/summary/structure for help/guide/demo/batch lists: MUST check if forward cache semantics still valid
10. Semantic changes in `message_plan` / `message_delivery`: MUST add tests covering single/waiting/batch/cache hit/rebuild/prefix reuse/fallback paths
11. New branches for message reuse/plan rendering/forward judgment: MUST add debug logs for hash/cache hit/stop reuse reason/fallback reason

---

## Runtime Behavior

1. High-concurrency hot paths: default `WritePolicy.BUFFERED` to reduce I/O jitter
2. Admin strong-consistency operations: use `WritePolicy.IMMEDIATE`
3. External API calls: MUST be failure-tolerant and degrade gracefully
4. Event handling: prevent bot self-triggering loops (check `event.user_id == bot.self_id`)
5. Scheduled tasks: MUST have unique `id`, idempotency control, traceable failure logs

---

## Data & Audit

1. Prefer repository/service calls, NOT direct table access in plugin handlers
2. Permission/ban/state changes that need audit trail: MUST log to `log_db`
3. User-visible text changes (name/nickname): write to `snapshot_db`
4. New plugin with independent database:
   - Justify `StateStore`, `EventStore`, `CounterStore` (sharedDB v2) storage model choice
   - Provide initialization entry and `init_all_tables()`
   - Provide minimal testable repo interface, avoid business layer directly depending on ops

---

## Testing Standards (baseline: water plugin)

Test directory structure:
```
tests/plugins/<plugin_name>/
  helpers.py
  test_handlers.py
  test_services.py
  test_repository_edges.py
```

**Minimum Coverage:**
1. Parameter parsing and invalid input handling
2. Permission gates and role checks
3. Idempotent paths (duplicate operations, lock conflicts, no-op states)
4. Degradation paths when external dependencies fail
5. Core success paths and key response messages
6. `extra.docs` schema, tree relationships, help auto-discovery behavior

**Best Practices:**
1. Use `AsyncMock` to isolate external I/O
2. Isolate package entries with startup side effects (see `tests/conftest.py` handling of `src.plugins.water`)
3. Test code MUST also follow Message injection constraints:
   - Pure text test messages: use safe helpers or `MessageSegment.text(...)`
   - Images/mentions/replies: explicitly construct corresponding `MessageSegment`
   - `Message` imports for type annotations ONLY are allowed

---

## Code Quality Gates

Before committing ANY Python code changes, MUST pass:

```bash
uv run ruff check --fix
uv run ruff format
uv run pyright
```

Recommended to also run:

```bash
uv run pytest
```

**Constraints:**
1. NO merge with known lint/type errors
2. If auto-fix introduces behavior changes, MUST add tests
3. If ONLY doc changes (like this file), can skip Python quality commands

---

## Development Workflow

1. **Plan -> Confirm -> Act** (give plan first, then execute changes)
2. New plugins: submit minimal skeleton first, then add features and tests (avoid large one-time patches)
3. PR descriptions MUST include:
   - Change points
   - Risk points
   - Regression verification commands and results
   - Documentation interface change descriptions (`extra.docs` schema, slug/parent_slug, output examples)
4. NO casual fixes to unrelated modules causing scope creep (unless explicitly declared in PR)

---

## Definition of Done (DoD)

A plugin requirement is "complete" when ALL of:

1. Code implementation follows layering and permission rules in this file
2. `extra.docs` schema provided and discoverable/tree-buildable/renderable by `help`
3. If command plugin, `docs/README.MD` or equivalent standardized module docs completed
4. Tests cover core behavior and edge paths
5. Quality gates passed (when Python changes exist)
6. NO new high-priority security or data consistency risks

---

## Commit Policy

After each completed feature or independently deliverable unit of work:

1. Create exactly ONE git commit before moving to next feature
2. Use gitmoji-based commit message format
3. Format: `<gitmoji> <type>(<scope>): <summary>`
4. Example: `✨ feat(xxx): 完成xxx功能`

---

## Instructions for Claude Code

When working on this project:

1. **Read before modifying**: Read target directory context and existing patterns before code changes
2. **Quality gates are mandatory**: Do NOT skip quality gates defined in this file (unless user explicitly requests and acknowledges risks)
3. **Minimal changes**: Reuse existing utility functions, repos, writers, enums
4. **Confirm risky operations**: When discovering runtime dangerous operations (destructive commands, unauthorized access), MUST get user confirmation first
5. **Default to adding/updating tests**: NOT just feature code
6. **Follow Message security**: ALWAYS use `MessageSegment.text(...)` for plain text, NEVER `Message(...)` with raw strings
7. **Use unified message plan**: Output via `DeliveryPlan` and `deliver_message_plan(...)`, NOT direct send functions
8. **Respect documentation contract**: ALL plugins MUST have `extra.docs` properly configured
9. **Run quality checks**: Always run `ruff check --fix`, `ruff format`, `pyright` before considering work complete
10. **Write in Chinese**: Code comments (when needed), commit messages, PR descriptions should be in Chinese to match project language

---

## Quick Reference

**Key Files:**
- Entry: `bot.py`
- Hooks: `src/hooks/processor.py`
- Message security: `src/lib/message_plan.py`, `src/lib/message_delivery.py`
- Plugin metadata helper: `src/lib/plugin_meta.py`
- Constants: `src/lib/consts.py`, `src/database/core/consts.py`

**Key Patterns:**
- Plugin metadata: `create_plugin_metadata()` with `extra.docs`
- Safe text messages: `MessageSegment.text(...)`
- Message output: `DeliveryPlan` → `deliver_message_plan(...)`
- Data access: Repository layer, NOT direct SQL in handlers

**Quality Commands:**
```bash
uv run ruff check --fix
uv run ruff format
uv run pyright
uv run pytest
```
