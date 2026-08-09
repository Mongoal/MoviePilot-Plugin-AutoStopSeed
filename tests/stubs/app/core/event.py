"""``app.core.event`` 的极薄 stub。

只实现插件导入与事件注册所需的最小接口：
- ``Event``：事件对象，行为等价于真实实现的 pydantic 模型（``event_type`` / ``event_data``）。
- ``eventmanager.register``：返回透传装饰器，使 ``@eventmanager.register(...)`` 不报错且保留原函数。
- ``EventManager``：占位类，供 ``_PluginBase.__init__`` 实例化。

真实事件分发逻辑在测试中并不需要——用例直接调用被装饰的处理器并传入构造好的 ``Event``。
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Union


class Event:
    """事件对象 stub（对齐真实 pydantic ``Event`` 的字段）。"""

    def __init__(self, event_type: Any = "", event_data: Optional[dict] = None,
                 priority: int = 0):
        """构造事件对象。"""
        self.event_type = event_type
        self.event_data = event_data if event_data is not None else {}
        self.priority = priority


class EventManager:
    """事件管理器占位 stub。"""

    def register(self, etype: Any, priority: Optional[int] = None) -> Callable:
        """事件注册装饰器：透传原函数，保持方法可被普通调用。"""

        def decorator(f: Callable) -> Callable:
            return f

        return decorator

    def add_event_listener(self, etype: Any, handler: Callable,
                           priority: Optional[int] = None) -> None:
        """占位实现：测试不需要真实监听注册。"""
        return None

    def remove_event_listener(self, etype: Any, handler: Callable) -> None:
        """占位实现：测试不需要真实监听移除。"""
        return None

    def send_event(self, etype: Any, data: Optional[dict] = None) -> Optional[Event]:
        """占位实现：返回一个 Event 供调用方取值。"""
        return Event(event_type=etype, event_data=data)


# 全局实例：对齐真实模块的 ``eventmanager``
eventmanager = EventManager()
