"""``app.plugins`` 的极薄 stub（注意：是模块文件，不是包）。

提供 ``_PluginBase`` 基类的最小实现，使被测插件可继承并实例化。

设计要点：
- 真实 ``_PluginBase`` 是 ABCMeta，且 ``__init__`` 会拉起 DB / 配置 / 事件总线等重量级依赖。
- 测试用例改用 ``object.__new__`` 绕过 ``__init__``，因此本 stub 的 ``_PluginBase`` **不强制
  抽象方法**（``init_plugin`` / ``get_state`` 等不报错），仅保留被测插件实际用到的方法：
  ``post_message`` 与 ``update_config``。
- ``post_message`` 与真实签名一致（``title/text/mtype/...`` 关键字），内部转发到
  ``self.chain.post_message``，便于用例通过 mock chain 验证调用。
"""
from __future__ import annotations

from typing import Any, Optional


class _PluginBase:
    """插件基类 stub。"""

    # 基类默认属性（子类会覆盖）
    plugin_name: Optional[str] = ""
    plugin_desc: Optional[str] = ""
    plugin_order: Optional[int] = 9999

    def __init__(self):
        """构造占位：测试不走此路径，仅为结构完整保留。"""
        # 真实实现会在这里初始化 chain/systemconfig/eventmanager 等；
        # 测试用例通过 object.__new__ 跳过，再手动注入 mock。
        self.chain = None
        self.systemconfig = None
        self.systemmessage = None
        self.eventmanager = None

    def post_message(self, channel: Any = None, mtype: Any = None,
                     title: Optional[str] = None, text: Optional[str] = None,
                     image: Optional[str] = None, link: Optional[str] = None,
                     userid: Any = None, username: Optional[str] = None,
                     **kwargs) -> None:
        """发送消息 stub：转发到 chain.post_message（与真实实现一致）。"""
        if self.chain is not None and hasattr(self.chain, "post_message"):
            self.chain.post_message(
                title=title, text=text, mtype=mtype
            )

    def update_config(self, config: dict, plugin_id: Optional[str] = None) -> bool:
        """更新配置 stub：返回 True。"""
        return True

    def get_data_path(self, plugin_id: Optional[str] = None) -> Any:
        """获取数据目录 stub。"""
        return None

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None) -> None:
        """保存插件数据 stub。"""
        return None

    def get_data(self, key: Optional[str] = None,
                 plugin_id: Optional[str] = None) -> Any:
        """读取插件数据 stub。"""
        return None
