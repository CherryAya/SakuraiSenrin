from typing import Final

from src.lib.i18n.types import LocaleCode
from src.locales.zh_cn import CATALOG as ZH_CATALOG

LOCALE: Final[LocaleCode] = "x-meme"

CATALOG: Final[dict[str, str]] = {
    **ZH_CATALOG,
    "docs.default.empty": "暂无说明，先脑补一下。",
    "help.index.hint": "发 `#help <插件名>`，直接开看。",
    "help.query.not_found": "没翻到这个插件：{query}\n先打 `#help` 看总表。",
    "admin.i18n.default.updated": "全局语言已切到 `{locale}`，安排上了。",
    "admin.i18n.group.updated": "群 `{group_id}` 语言已切到 `{locale}`，包变脸的。",
    "admin.i18n.group.cleared": "群 `{group_id}` 的语言覆盖已清空，回归默认流。",
    "admin.i18n.list.empty": "目前没有群在单独整语言花活。",
    "water.common.group_only": "这条得在群里整，私聊不接这活。",
    "water.common.working": "凛凛开算了，CPU 嗡嗡的，稍等。",
    "water.common.admin_confirm": "这事得群管理层拍板，普通群友先围观。",
    "water.rank.empty": "翻了半天账本，结果这期真没水，空空如也。",
    "water.query.profile_not_enough": "你这水量还在新手村，多聊几天再来查。",
    "water.query.unsupported": "这个查询姿势目前还没适配。",
}
