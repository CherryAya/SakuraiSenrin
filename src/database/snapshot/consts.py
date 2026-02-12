from enum import StrEnum
from types import MappingProxyType

from src.lib.enums import LocalizedMixin


class SnapshotEventType(LocalizedMixin, StrEnum):
    USERNAME = "NAME"
    CARD = "CARD"
    GROUPNAME = "GROUPNAME"

    __labels__ = MappingProxyType(
        {
            USERNAME: "用户名",
            CARD: "群名片",
            GROUPNAME: "群组名",
        },
    )
