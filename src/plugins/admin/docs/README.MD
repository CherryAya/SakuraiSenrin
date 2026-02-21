<!--
 * Author: SakuraiCora<1479559098@qq.com>
 * Date: 2026-02-18 22:40:38
 * LastEditors: SakuraiCora<1479559098@qq.com>
 * LastEditTime: 2026-02-22 04:02:15
 * Description: 
-->
#admin.group 
    ban <gid>
    unban <gid>
    auth <gid>
    unauth <gid>
    leave <gid>
    status <gid>

#admin.user
    ban <uid> [-r] <reason> [-t] <duration>
    unban <uid> [-r] <reason>
    status <uid>

#admin.invite
    list | show | ls -> 考虑 PIL，做个 list
    approve <flag>
    reject | ignore <flag|all>
    