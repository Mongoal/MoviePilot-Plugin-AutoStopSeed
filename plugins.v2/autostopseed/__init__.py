"""AutoStopSeed 插件：MoviePilot 创建的下载任务整理完成后自动停止做种。

设计依据（MoviePilot v2 源码）：
- MoviePilot 创建的每个种子都会被打上内置标签 TORRENT_TAG（默认 ``MOVIEPILOT``），
  见 ``app/modules/qbittorrent/__init__.py`` 添加下载处；qb 自身手动创建的种子不带该标签。
- 整理与下载是两个独立环节：整理可能由无下载来源的文件触发，也可能在下载未完成时
  被手动触发（多集种子先下完部分集）。MoviePilot 自身在打「已整理」标签前用
  ``__is_torrent_download_completed``（``app/chain/transfer.py``）检查种子整体下载完成，
  注释直接指出未整体完成就打标签会卡死剩余内容下载（issue #6009）。
- 本插件复用同一判定：停止做种前用一次 ``list_torrents(hashs=[hash])``（默认按
  TORRENT_TAG 过滤）同时校验「种子由 MP 创建」与「progress >= 100 整体下载完成」，
  任一不满足即跳过，绝不卡死未完成的下载任务。
"""
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType


class AutoStopSeed(_PluginBase):
    """整理完成后自动停止做种（暂停上传，不删文件）。"""

    # ===== 插件元信息（与 package.v2.json 保持一致）=====
    plugin_name = "自动停止做种"
    plugin_desc = "MoviePilot 创建的下载任务整理完成后，自动停止对应种子的做种（暂停上传，不删文件）。"
    plugin_icon = "pause.png"
    plugin_version = "1.0.0"
    plugin_author = "AutoStopSeed"
    plugin_label = "下载管理"
    plugin_config_prefix = "autostopseed_"
    plugin_order = 100
    auth_level = 1

    # ===== 运行时状态 =====
    _enabled: bool = False
    _notify: bool = True

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        # 先复位为默认值，保证多次初始化幂等
        self._enabled = False
        self._notify = True
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        self._notify = bool(config.get("notify", True))

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """本插件不注册远程命令。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """本插件不注册 API。"""
        return []

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "停止做种后发送消息通知",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": (
                                                "仅停止 MoviePilot 自己创建的种子（带内置标签）。"
                                                "文件不会被删除，可在下载器中手动恢复做种。"
                                            ),
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页。"""
        if not self._enabled:
            return None
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success",
                    "variant": "tonal",
                    "text": "自动停止做种已启用：整理完成后将停止对应 MoviePilot 种子的做种。",
                },
            }
        ]

    def stop_service(self) -> None:
        """停止插件后台服务。本插件无后台服务，空实现即可。"""
        return None

    # ===== 核心：监听整理完成事件 =====
    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        """整理完成后，自动停止对应下载任务的做种。

        注意：整理与下载是两个独立环节——
        - 整理可能由无下载来源的文件触发（拷贝/刮削入库）；
        - 下载未完成时也可能被手动触发整理（多集种子先下完部分集）。
        因此本方法在停止做种前必须复核两件事：种子由 MoviePilot 创建、且已整体下载完成。
        对只下载了一部分就整理的种子，跳过停做种，避免卡死剩余内容的下载。

        :param event: 整理完成事件，事件数据含 ``download_hash`` 与 ``downloader``。
        """
        if not self._enabled:
            return

        data = event.event_data or {}
        download_hash = data.get("download_hash")
        downloader = data.get("downloader")

        # 无下载来源（纯手动整理、刮削入库等，无对应下载任务）直接跳过
        if not download_hash:
            return

        # 复核：仅处理「MoviePilot 创建」且「整体下载完成」的种子。
        # 一次 list_torrents 调用同时验证两项，避免重复请求下载器。
        should_stop, reason = self._should_stop_seeding(download_hash, downloader)
        if not should_stop:
            logger.info(f"跳过停止做种: hash={download_hash}, 原因={reason}")
            return

        # 停止做种（暂停上传，不删文件）
        try:
            ok = self.chain.stop_torrents(hashs=download_hash, downloader=downloader)
        except Exception as e:
            logger.error(f"停止做种异常: hash={download_hash}, error={e}")
            return

        if ok:
            logger.info(
                f"已停止做种: hash={download_hash}, downloader={downloader}"
            )
            if self._notify:
                self._send_stop_notice(download_hash, downloader)
        else:
            logger.warning(
                f"停止做种失败: hash={download_hash}, downloader={downloader}"
            )

    def _should_stop_seeding(
            self, download_hash: str, downloader: Optional[str]
    ) -> Tuple[bool, str]:
        """判断是否应对该种子停止做种，返回 (是否停止, 原因说明)。

        同时校验两项（一次 ``list_torrents`` 完成，该接口默认按内置标签
        ``TORRENT_TAG`` 过滤，故查不到即非 MoviePilot 创建）：
        1. 种子由 MoviePilot 创建（带内置标签），排除 qb/transmission 手动添加的种子；
        2. 种子已整体下载完成（``progress >= 100``），避免对只下完一部分就手动整理
           的种子停做种、卡死剩余内容（对应 MoviePilot issue #6009 同类问题）。

        查询异常时保守放行（整理完成事件来源已大概率是 MP 种子）。

        :param download_hash: 种子哈希
        :param downloader: 下载器名称
        :return: (是否应停止做种, 原因)
        """
        try:
            torrents = self.chain.list_torrents(
                hashs=[download_hash], downloader=downloader
            )
        except Exception as e:
            # 查询失败时保守放行（与整理完成事件来源一致，已大概率是 MP 种子）
            logger.warning(
                f"查询种子状态失败，按放行处理: hash={download_hash}, error={e}"
            )
            return True, "查询异常放行"

        # 非内置标签种子（qb 手动添加等）查不到 → 跳过
        if not torrents:
            return False, "非 MoviePilot 创建"

        # 部分下载就手动整理的情况：progress < 100 → 跳过，避免卡死下载
        # 复用 MoviePilot 自身判断（app/chain/transfer.py __is_torrent_download_completed）
        if not all((getattr(t, "progress", 0) or 0) >= 100 for t in torrents):
            return False, "下载未完成"

        return True, "MP 创建且下载完成"

    def _send_stop_notice(self, download_hash: str, downloader: Optional[str]) -> None:
        """发送停止做种的消息通知。"""
        try:
            # post_message 内部会构造 Notification 并补全链接，这里直接传字段即可
            self.post_message(
                mtype=NotificationType.Organize,
                title="自动停止做种",
                text=f"已停止做种：{download_hash}\n下载器：{downloader or '默认'}",
            )
        except Exception as e:
            logger.warning(f"发送停止做种通知失败: {e}")
