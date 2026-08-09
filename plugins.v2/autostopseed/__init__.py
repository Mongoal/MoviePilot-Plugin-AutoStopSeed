"""AutoStopSeed 插件：MoviePilot 创建的下载任务整理完成后自动停止做种。

设计依据（MoviePilot v2 源码）：
- MoviePilot 创建的每个种子都会被打上内置标签 TORRENT_TAG（默认 ``MOVIEPILOT``），
  见 ``app/modules/qbittorrent/__init__.py`` 添加下载处；qb 自身手动创建的种子不带该标签。
- 整理扫描（``list_torrents``）默认按 TORRENT_TAG 过滤，非 MP 创建的种子不会进入整理流程，
  因此能触发 ``TransferComplete`` 的种子几乎都是 MP 创建的。
- 为严格保证「只管理 MP 自己创建的种子」，本插件在停止前再以
  ``list_torrents(hashs=[download_hash])`` 复核一次：该接口默认带 TORRENT_TAG 过滤，
  返回为空即说明该种子并非 MP 创建（例如下载器目录混入的外部种子被手动整理），直接跳过。
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

        :param event: 整理完成事件，事件数据含 ``download_hash`` 与 ``downloader``。
        """
        if not self._enabled:
            return

        data = event.event_data or {}
        download_hash = data.get("download_hash")
        downloader = data.get("downloader")

        # 无下载来源（纯手动整理、刮削入库等）直接跳过
        if not download_hash:
            return

        # 严格复核：只处理 MoviePilot 自己创建的种子。
        # list_torrents 默认按 TORRENT_TAG 过滤，非 MP 创建的种子查不到即返回空。
        if not self._is_moviepilot_torrent(download_hash, downloader):
            logger.info(
                f"种子 {download_hash} 非 MoviePilot 创建，跳过停止做种"
            )
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

    def _is_moviepilot_torrent(self, download_hash: str, downloader: Optional[str]) -> bool:
        """判断指定种子是否为 MoviePilot 创建。

        借助 ``list_torrents(hashs=...)`` 默认按 TORRENT_TAG 过滤的特性：
        能查到即说明该种子带内置标签（由 MP 创建）；查不到则非 MP 创建。

        :param download_hash: 种子哈希
        :param downloader: 下载器名称
        :return: True 表示为 MoviePilot 创建的种子
        """
        try:
            torrents = self.chain.list_torrents(
                hashs=[download_hash], downloader=downloader
            )
        except Exception as e:
            # 查询失败时保守起见放行（与整理完成事件来源一致，已大概率是 MP 种子）
            logger.warning(
                f"查询种子标签失败，按放行处理: hash={download_hash}, error={e}"
            )
            return True
        return bool(torrents)

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
