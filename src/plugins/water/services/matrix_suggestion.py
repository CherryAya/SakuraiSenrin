"""群矩阵智能合并建议服务。"""

import asyncio
from dataclasses import dataclass

from nonebot.adapters.onebot.v11.bot import Bot

from src.config import config
from src.plugins.water.database import water_repo
from src.repositories import group_repo, member_repo
from src.services.info import resolve_group_name

MIN_GROUP_MEMBER_BASE = 1  # TODO: config
MIN_OVERLAP_USERS = 0
SIMPSON_THRESHOLD = 0.68


@dataclass(frozen=True)
class MergeCandidate:
    matrix_id: str
    matched_group_ids: list[str]
    score: float
    overlap_users: int
    base_users: int
    matrix_users: int


class MatrixSuggestionService:
    def __init__(self) -> None:
        # 热路径本地缓存：群首次记录状态已确认后，后续消息不再击穿 DB。
        self._first_record_seen_cache: set[str] = set()
        self._group_locks: dict[str, asyncio.Lock] = {}

    def _get_group_lock(self, group_id: str) -> asyncio.Lock:
        lock = self._group_locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._group_locks[group_id] = lock
        return lock

    async def warm_up_first_record_cache(self) -> None:
        self._first_record_seen_cache = (
            await water_repo.get_marked_first_record_groups()
        )

    async def maybe_suggest_on_first_record(self, bot: Bot, group_id: str) -> None:
        if group_id in self._first_record_seen_cache:
            return

        async with self._get_group_lock(group_id):
            if group_id in self._first_record_seen_cache:
                return

            first_seen = await water_repo.mark_group_first_record_seen(group_id)
            # 无论是否首次，DB 状态都已确认，后续消息直接走本地缓存。
            self._first_record_seen_cache.add(group_id)
            if not first_seen:
                return

        await self._maybe_suggest(bot, group_id, trigger="first_record")

    async def maybe_suggest_on_new_member(
        self,
        bot: Bot,
        group_id: str,
        user_id: str,
    ) -> None:
        if user_id == str(bot.self_id):
            return
        await self._maybe_suggest(bot, group_id, trigger="new_member")

    async def _maybe_suggest(self, bot: Bot, group_id: str, trigger: str) -> None:
        current_matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
        ignored = await water_repo.get_ignored_matrix_suggestions()
        if group_id in ignored:
            return
        if await water_repo.has_matrix_merge_decision(group_id):
            return
        if await water_repo.get_pending_matrix_suggestion(group_id):
            return

        candidate = await self._find_best_candidate(
            group_id=group_id,
            current_matrix_id=current_matrix_id,
            ignored=ignored,
        )
        if candidate is None:
            return

        await water_repo.set_pending_matrix_suggestion(
            group_id=group_id,
            target_matrix_id=candidate.matrix_id,
        )
        await bot.send_group_msg(
            group_id=int(group_id),
            message=await self._build_suggestion_message(bot, group_id, candidate),
        )

    async def _find_best_candidate(
        self,
        group_id: str,
        current_matrix_id: str,
        ignored: set[str],
    ) -> MergeCandidate | None:
        base_users = await member_repo.get_distinct_user_count(group_id)
        if base_users < MIN_GROUP_MEMBER_BASE:
            return None

        working_groups = await group_repo.get_working_group_ids()
        target_group_ids = [
            target_group_id
            for target_group_id in working_groups
            if target_group_id != group_id and target_group_id not in ignored
        ]
        if not target_group_ids:
            return None

        group_matrix_map = await water_repo.get_or_create_group_matrix_ids(
            target_group_ids
        )

        matrix_groups: dict[str, list[str]] = {}
        for target_group_id, matrix_id in group_matrix_map.items():
            if matrix_id == current_matrix_id:
                continue
            if target_group_id in ignored:
                continue
            matrix_groups.setdefault(matrix_id, []).append(target_group_id)

        candidates: list[MergeCandidate] = []
        for matrix_id, groups in matrix_groups.items():
            matrix_users = 0
            max_overlap = 0
            score_sum = 0.0
            valid_groups: list[str] = []

            for target_group_id in groups:
                target_users = await member_repo.get_distinct_user_count(
                    target_group_id
                )
                if target_users < MIN_GROUP_MEMBER_BASE:
                    continue
                overlap = await member_repo.get_intersection_user_count(
                    group_id,
                    target_group_id,
                )
                if overlap < MIN_OVERLAP_USERS:
                    continue
                valid_groups.append(target_group_id)
                matrix_users += target_users
                max_overlap = max(max_overlap, overlap)
                score_sum += overlap / min(base_users, target_users)

            if not valid_groups or matrix_users <= 0:
                continue

            score = score_sum / len(valid_groups)
            if score < SIMPSON_THRESHOLD:
                continue

            candidate = MergeCandidate(
                matrix_id=matrix_id,
                matched_group_ids=sorted(valid_groups),
                score=score,
                overlap_users=max_overlap,
                base_users=base_users,
                matrix_users=matrix_users,
            )
            candidates.append(candidate)

        if not candidates:
            return None
        candidates.sort(key=lambda item: item.score, reverse=True)
        # 极端情况：两个候选矩阵分差过小，不做自动建议，避免误并。
        if (
            len(candidates) > 1
            and abs(candidates[0].score - candidates[1].score) < 0.03
        ):
            return None
        return candidates[0]

    async def _build_suggestion_message(
        self,
        bot: Bot,
        current_group_id: str,
        candidate: MergeCandidate,
    ) -> str:
        current_name = await resolve_group_name(bot, current_group_id)
        related_lines = []
        for gid in candidate.matched_group_ids:
            gname = await resolve_group_name(bot, gid)
            related_lines.append(f"「{gname}({gid})」")
        related_text = "\n".join(related_lines) if related_lines else ""
        return (
            "===== 零域矩阵 Matrix 合并建议 =====\n"
            "这些群🧐：\n\n"
            f"「{current_name}({current_group_id})」\n"
            f"{related_text}\n\n"
            f"成员重合率： {candidate.score:.2%}（{candidate.overlap_users}人） \n"
            f"建议合并到： {candidate.matrix_id}\n"
            "合并后影响：\n"
            "「这些群会一起算排名和成长」\n"
            "「历史数据会按同一分组累计」\n\n"
            "管理员发送：\n"
            "1) #water.merge yes  是同一群人，合并\n"
            "2) #water.merge no   不是或先不合并（后续不再提示）\n\n"
            f"仅允许操作一次，如后续想改，请到加入 {config.MAIN_GROUP_ID} 联系群主。"
        )


matrix_suggestion_service = MatrixSuggestionService()
