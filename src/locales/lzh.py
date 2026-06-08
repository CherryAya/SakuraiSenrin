from typing import Final

from src.lib.i18n.types import LocaleCode
from src.locales.zh_cn import CATALOG as ZH_CATALOG

LOCALE: Final[LocaleCode] = "lzh"

CATALOG: Final[dict[str, str]] = {
    **ZH_CATALOG,
    "docs.default.empty": "暫無所陳",
    "docs.default.no_description": "暫無所述",
    "docs.default.trigger": "觸發之法",
    "docs.default.permission": "權限",
    "docs.default.usage": "用法",
    "docs.default.passive": "被動觸發",
    "help.index.hint": "發 #help <插件名> 以觀詳文。",
    "help.index.empty": "今無可示之插件文檔。",
    "help.query.not_found": "未得插件文檔: {query}\n請先發 #help 觀可用之列。",
    "help.query.ambiguous.title": "插件查詢有歧義: {query}",
    "help.query.ambiguous.hint": "請用更精確之插件名。",
    "help.fallback.reason": "文檔降級之由: {reason}",
    "admin.i18n.locale.invalid": "不支援此語言: {locale}\n可用值: {choices}",
    "admin.i18n.group.required": "今非群聊，請明示群號。",
    "admin.i18n.group.invalid": "群號非法: {group_id}",
    "admin.i18n.default.updated": "已改全局默認語言為: {locale}",
    "admin.i18n.group.updated": "已改群 {group_id} 之語言覆蓋為: {locale}",
    "admin.i18n.group.cleared": "已清群 {group_id} 之語言覆蓋。",
    "admin.i18n.group.already_cleared": "群 {group_id} 本無語言覆蓋。",
    "admin.i18n.list.empty": "今無群級語言覆蓋。",
    "water.common.group_only": "此令當於群中用之。",
    "water.common.working": "凜凜方統計中，少待之……",
    "water.common.admin_confirm": "此事須群管理或群主可決。",
    "water.rank.empty": "凜凜檢賬簿而知，此期尚無可用之結算數。",
    "water.query.unsupported": "此查暫未支持。",
}
