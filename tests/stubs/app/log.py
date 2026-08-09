"""``app.log`` 的极薄 stub。

提供 ``logger``，方法签名对齐 Python 标准库 logging（``info/warning/error/debug``），
仅做无副作用收集，便于测试观察且不产生噪声。
"""
from __future__ import annotations

import logging

# 复用标准库 logging，避免重新造轮子；测试可按需调高日志级别
logger = logging.getLogger("AutoStopSeed.test")
