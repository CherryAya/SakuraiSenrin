from enum import StrEnum


class WritePolicy(StrEnum):
    BUFFERED = "buffered"  # 批量用 BatchWriter
    IMMEDIATE = "immediate"  # 即时用 DirectOps
