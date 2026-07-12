"""i18n package exports.

Keep this module light so generated-key scripts can import `src.lib.i18n.types`
without pulling in NoneBot runtime dependencies.
"""

from __future__ import annotations

from .types import LocaleCode

__all__ = ["LocaleCode"]
