"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-10-07
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07
Description: 运行时异常上下文，用于异常上报时补全群组、用户与消息来源信息
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(slots=True)
class ErrorContext:
    """单次事件处理链路上累计的异常上下文。"""

    group_id: str = ""
    group_name: str = ""
    user_id: str = ""
    user_name: str = ""
    message_id: str = ""
    message_type: str = ""
    event_type: str = ""
    matcher_module: str = ""
    matcher_name: str = ""
    trace_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            (
                self.group_id,
                self.user_id,
                self.message_id,
                self.event_type,
                self.matcher_module,
                self.trace_id,
            )
        )

    def merge(self, other: ErrorContext) -> None:
        """以非空值补全当前上下文，保留先写入的信息。"""
        for name in (
            "group_id",
            "group_name",
            "user_id",
            "user_name",
            "message_id",
            "message_type",
            "event_type",
            "matcher_module",
            "matcher_name",
            "trace_id",
        ):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        for key, value in other.extra.items():
            self.extra.setdefault(key, value)

    def as_dict(self) -> dict[str, str]:
        payload = {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "event_type": self.event_type,
            "matcher_module": self.matcher_module,
            "matcher_name": self.matcher_name,
            "trace_id": self.trace_id,
        }
        payload = {key: value for key, value in payload.items() if value}
        for key, value in self.extra.items():
            if value:
                payload.setdefault(key, value)
        return payload

    def describe_lines(self) -> tuple[str, ...]:
        """输出面向管理员的可读上下文行。"""
        lines: list[str] = []
        if self.group_id or self.group_name:
            group_text = self.group_id or "-"
            if self.group_name:
                group_text = f"{group_text}({self.group_name})"
            lines.append(f"来源群: {group_text}")
        if self.user_id or self.user_name:
            user_text = self.user_id or "-"
            if self.user_name:
                user_text = f"{user_text}({self.user_name})"
            lines.append(f"触发者: {user_text}")
        if self.message_id:
            message_text = self.message_id
            if self.message_type:
                message_text = f"{message_text} [{self.message_type}]"
            lines.append(f"消息 ID: {message_text}")
        if self.event_type:
            lines.append(f"事件类型: {self.event_type}")
        if self.matcher_module or self.matcher_name:
            matcher_text = self.matcher_module or "-"
            if self.matcher_name:
                matcher_text = f"{matcher_text}::{self.matcher_name}"
            lines.append(f"处理器: {matcher_text}")
        if self.trace_id:
            lines.append(f"trace_id: {self.trace_id}")
        for key, value in self.extra.items():
            if value:
                lines.append(f"{key}: {value}")
        return tuple(lines)


_current_context: ContextVar[ErrorContext] = ContextVar(
    "sakuraisenrin_error_context",
    default=ErrorContext(),
)


def get_error_context() -> ErrorContext:
    """获取当前上下文对象，可直接就地补充字段。"""
    return _current_context.get()


def snapshot_error_context() -> ErrorContext:
    """获取当前上下文的浅拷贝，避免后续修改影响已上报内容。"""
    return copy_context(_current_context.get())


def copy_context(context: ErrorContext) -> ErrorContext:
    return ErrorContext(
        group_id=context.group_id,
        group_name=context.group_name,
        user_id=context.user_id,
        user_name=context.user_name,
        message_id=context.message_id,
        message_type=context.message_type,
        event_type=context.event_type,
        matcher_module=context.matcher_module,
        matcher_name=context.matcher_name,
        trace_id=context.trace_id,
        extra=dict(context.extra),
    )


def reset_error_context() -> None:
    _current_context.set(ErrorContext())


@contextmanager
def error_context_scope(context: ErrorContext | None = None) -> Iterator[ErrorContext]:
    """在独立作用域内绑定上下文，退出时自动还原父级上下文。"""
    scoped = context if context is not None else ErrorContext()
    token = _current_context.set(scoped)
    try:
        yield scoped
    finally:
        _current_context.reset(token)


def bind_error_context(**fields: str) -> ErrorContext:
    """向当前上下文补充字段。

    采用 copy-on-write：基于当前值派生新对象再写回 ContextVar，
    避免多个事件共享同一个可变实例而互相污染。
    """
    current = snapshot_error_context()
    extra = fields.pop("extra", None)
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if not hasattr(current, key):
            current.extra[key] = text
            continue
        setattr(current, key, text)
    if isinstance(extra, dict):
        for key, value in extra.items():
            text = str(value).strip() if value is not None else ""
            if text:
                current.extra.setdefault(key, text)
    _current_context.set(current)
    return current


def describe_error_context(context: ErrorContext | None = None) -> str:
    """把上下文渲染为多行文本，未知上下文返回空字符串。"""
    resolved = context if context is not None else snapshot_error_context()
    if resolved.is_empty():
        return ""
    return "\n".join(resolved.describe_lines())


__all__ = [
    "ErrorContext",
    "bind_error_context",
    "describe_error_context",
    "error_context_scope",
    "get_error_context",
    "reset_error_context",
    "snapshot_error_context",
]
