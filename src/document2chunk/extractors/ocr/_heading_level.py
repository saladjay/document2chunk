"""向后兼容 re-export —— 实际实现在 document2chunk.postprocess（designs/009）。

旧调用 ``calibrate(content, metadata)`` 仍可用（calibrate_levels 默认参数兼容）。
"""
from document2chunk.postprocess import calibrate_levels as calibrate  # noqa: F401
from document2chunk.postprocess import style_of  # noqa: F401
