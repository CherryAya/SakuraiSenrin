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
    "water.query.rank.menu.intro": "榜单现在按 主体 + 范围 + 时间 这三件套来查。",
    "water.query.rank.menu.shortcuts": "快捷入口：",
    "water.query.rank.menu.example.matrix": "#水王 矩阵榜 全局 {period}",
    "water.query.rank.menu.legal.user": "用户榜: 本群 / 本矩阵 / 全局 + {periods}",
    "water.query.rank.menu.legal.group": "群聊榜: 本矩阵 / 全局 + {periods}",
    "water.query.rank.menu.legal.matrix": "矩阵榜: 全局 + {periods}",
    "water.query.rank.guided.intro": "先选榜单主体：{choices}",
    "water.query.rank.guided.footer": "发 revoke / recall 可取消，连续错 3 次会自动退出。",
    "water.query.rank.guided.subject_prompt": "先选榜单主体：{choices}",
    "water.query.rank.guided.subject_invalid": "主体没对上，请发：{choices}",
    "water.query.rank.guided.scope_prompt": "再选范围：{choices}",
    "water.query.rank.guided.scope_invalid": "这个范围和当前主体对不上，重新选。",
    "water.query.rank.guided.period_prompt": "最后选时间：{choices}",
    "water.query.rank.guided.period_invalid": "时间没选对，请发：{choices}",
    "water.query.rank.guided.summary": "你刚刚选的是：{subject} / {scope} / {period}",
    "water.query.rank.error.missing_dimensions": "还缺 {dimensions}，补成 #水王 <主体> <范围> <时间> 才能查。",
    "water.query.rank.error.unknown_tokens": "这里有几个词我没认出来：{tokens}。",
    "water.query.rank.error.duplicate_subject": "主体填一个就够了，别叠 buff。",
    "water.query.rank.error.duplicate_scope": "范围填一个就行，别双开。",
    "water.query.rank.error.duplicate_period": "时间填一个就够，别全都要。",
    "water.query.rank.error.shortcut_with_args": "快捷入口不用带参数，直接发 {command} 就行。",
    "water.query.rank.error.invalid_period_menu": "这个时间不合法。能用的是：{periods}",
    "water.query.rank.error.invalid_combo": "这个主体和范围凑不起来，建议改成 {suggestion}",
    "water.query.rank.error.invalid": "这个查询姿势还没适配，按标准格式再来一次。",
    "water.query.profile_not_enough": "你这水量还在新手村，多聊几天再来查。",
    "water.query.unsupported": "这个查询姿势目前还没适配。",
}
