from enum import Enum, IntFlag
from typing import Any


class LocalizedMixin:
    @classmethod
    def get_label(cls, member: Any) -> str:
        labels = getattr(cls, "__labels__", {})
        val = member.value if isinstance(member, Enum) else member
        if val in labels:
            return labels[val]
        if isinstance(labels, Enum):
            labels = labels.value
        elif isinstance(member, IntFlag) and member.value != 0:
            decomposed_labels = []
            for m in member.__class__:
                is_atomic = (m.value & (m.value - 1)) == 0
                if m.value == 0 or not is_atomic:
                    continue
                if (val & m.value) == m.value:
                    decomposed_labels.append(cls.get_label(m))
            if decomposed_labels:
                return " | ".join(decomposed_labels)
        return str(val)

    @property
    def label(self) -> str:
        return self.get_label(self)

    def __str__(self) -> str:
        return self.label
