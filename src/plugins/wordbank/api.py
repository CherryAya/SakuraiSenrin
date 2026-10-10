"""wordbank 对外 API。

其他插件只应通过本模块消费 wordbank 能力；直接依赖 `handlers/`、`services/`、
`text_parsing` 等内部模块会让重构 wordbank 内部结构时波及消费方。
"""

# 形状与消息模型
# 可观测性辅助
from .debug import describe_batch_errors as describe_batch_errors
from .debug import describe_message_segments as describe_message_segments
from .debug import describe_shape as describe_shape

# 前向输入探测
from .forward_batch import (
    build_response_input_payload as build_response_input_payload,
)
from .forward_batch import is_forward_input as is_forward_input
from .handlers import (
    build_message_shape_from_message as build_message_shape_from_message,
)
from .handlers import (
    fetch_image_bytes_from_message as fetch_image_bytes_from_message,
)
from .handlers import (
    handle_guided_study_shape_result as handle_guided_study_shape_result,
)
from .handlers import (
    handle_study_with_media_result as handle_study_with_media_result,
)
from .handlers.commands import (
    default_i18n_text as default_i18n_text,
)
from .handlers.commands import (
    localize_command_error as localize_command_error,
)
from .handlers.commands import (
    parse_guided_weight as parse_guided_weight,
)
from .handlers.commands import (
    parse_study_group_block_choice as parse_study_group_block_choice,
)
from .handlers.commands import (
    parse_study_mode_choice as parse_study_mode_choice,
)
from .handlers.media_helpers import extract_image_urls as extract_image_urls
from .handlers.media_helpers import (
    shape_from_trigger_text_value as shape_from_trigger_text_value,
)

# 引导流程的类型与载荷
from .handlers.submission import SubmissionLifecycle as SubmissionLifecycle
from .handlers.submission import SubmissionPayload as SubmissionPayload
from .message_model import MessageShape as MessageShape

# 服务与运行时状态
from .services import wordbank_media_service as wordbank_media_service
from .services import wordbank_service as wordbank_service

# 规则与文本解析
from .services.rules import RuleError as RuleError
from .services.rules import (
    build_legacy_study_shortcut_rule as build_legacy_study_shortcut_rule,
)
from .text_parsing import has_meaningful_text as has_meaningful_text
from .text_parsing import rest_after_token as rest_after_token
from .text_parsing import tokenize_shell_like as tokenize_shell_like

__all__ = [
    "MessageShape",
    "RuleError",
    "SubmissionLifecycle",
    "SubmissionPayload",
    "build_legacy_study_shortcut_rule",
    "build_message_shape_from_message",
    "build_response_input_payload",
    "default_i18n_text",
    "describe_batch_errors",
    "describe_message_segments",
    "describe_shape",
    "extract_image_urls",
    "fetch_image_bytes_from_message",
    "handle_guided_study_shape_result",
    "handle_study_with_media_result",
    "has_meaningful_text",
    "is_forward_input",
    "localize_command_error",
    "parse_guided_weight",
    "parse_study_group_block_choice",
    "parse_study_mode_choice",
    "rest_after_token",
    "shape_from_trigger_text_value",
    "tokenize_shell_like",
    "wordbank_media_service",
    "wordbank_service",
]
