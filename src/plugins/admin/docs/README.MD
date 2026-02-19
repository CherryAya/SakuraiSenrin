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