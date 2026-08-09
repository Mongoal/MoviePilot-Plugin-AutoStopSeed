"""``app.schemas.types`` 的极薄 stub。

仅提供插件用到的枚举成员：``EventType.TransferComplete`` 与
``NotificationType.Organize``。值为字符串，对齐真实枚举的 ``.value``。
"""
from enum import Enum


class EventType(Enum):
    """事件类型枚举 stub。"""

    TransferComplete = "transfer.complete"


class NotificationType(Enum):
    """通知类型枚举 stub。"""

    Organize = "整理入库"
