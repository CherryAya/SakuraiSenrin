"""i18n 门面（兼容层）：保留原有导入路径。

纯查表在 ``catalog``，事件绑定在 ``delivery``；新代码直接 import 二者。
"""

from __future__ import annotations

from .catalog import (
    CATALOGS as CATALOGS,
)
from .catalog import (
    DEFAULT_LOCALE as DEFAULT_LOCALE,
)
from .catalog import (
    LOCALE_ALIASES as LOCALE_ALIASES,
)
from .catalog import (
    LOCALE_NAME_KEYS as LOCALE_NAME_KEYS,
)
from .catalog import (
    LOCALE_NAMES as LOCALE_NAMES,
)
from .catalog import (
    format_duration as format_duration,
)
from .catalog import (
    get_catalog as get_catalog,
)
from .catalog import (
    normalize_locale as normalize_locale,
)
from .catalog import (
    tr as tr,
)
from .catalog import (
    tr_template as tr_template,
)
from .delivery import (
    finish_i18n as finish_i18n,
)
from .delivery import (
    get_group_locale as get_group_locale,
)
from .delivery import (
    msg as msg,
)
from .delivery import (
    resolve_locale as resolve_locale,
)
from .delivery import (
    send_group_i18n as send_group_i18n,
)
from .delivery import (
    send_i18n as send_i18n,
)
from .delivery import (
    send_private_i18n as send_private_i18n,
)
