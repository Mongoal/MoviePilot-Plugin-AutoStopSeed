"""AutoStopSeed 插件：自动停止做种，支持两种互斥模式。

模式 B（默认，下载触发）：监听 ``EventType.DownloadAdded``，下载添加时即给种子设置
做种时间限制 ``seeding_time_limit``（单位分钟）。做种计时与停止完全由下载器自身完成——
qb / Transmission 下载完成后做种满设定分钟数自动停止，无需轮询、不依赖整理环节。
- 底层能力见 ``app/modules/qbittorrent/qbittorrent.py change_torrent`` 与
  ``app/chain/__init__.py update_torrent``，参数 ``seeding_time_limit`` 单位为分钟。
- 注意 rTorrent 封装不支持做种时间限制（``app/modules/rtorrent/__init__.py`` 直接返回 False），
  使用 rTorrent 时本模式无效，应改用模式 A。

模式 A（可代替，整理触发）：监听 ``EventType.TransferComplete``，文件整理完成时停止做种。
- MoviePilot 没有「下载完成」事件，整理完成是唯一隐含"下载已完成"语义的事件；
- 整理与下载独立，整理可能在下载未完成时被手动触发，故模式 A 停止前用一次
  ``list_torrents(hashs=[hash])``（默认按 TORRENT_TAG 过滤）同时校验「种子由 MP 创建」
  与「progress >= 100 整体下载完成」，任一不满足即跳过，绝不卡死未完成的下载任务
  （对应 MoviePilot issue #6009 同类问题）。
"""
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

# 模式 B：下载添加时设置做种时间限制
MODE_ON_DOWNLOAD = "on_download"
# 模式 A：整理完成时停止做种
MODE_ON_TRANSFER = "on_transfer"


class AutoStopSeed(_PluginBase):
    """自动停止做种（默认下载添加时设做种限制，可选整理完成时停）。"""

    # ===== 插件元信息（与 package.v2.json 保持一致）=====
    plugin_name = "自动停止做种"
    plugin_desc = (
        "下载添加时设置做种时间限制（默认，做种满 N 分钟自动停），"
        "或整理完成时停止做种。仅作用于 MoviePilot 创建的种子。"
    )
    plugin_icon = "pause.png"
    plugin_version = "1.1.0"
    plugin_author = "AutoStopSeed"
    plugin_label = "下载管理"
    plugin_config_prefix = "autostopseed_"
    plugin_order = 100
    auth_level = 1

    # ===== 运行时状态 =====
    _enabled: bool = False
    _mode: str = MODE_ON_DOWNLOAD
    _seeding_time: int = 1
    _notify: bool = True

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        # 先复位为默认值，保证多次初始化幂等
        self._enabled = False
        self._mode = MODE_ON_DOWNLOAD
        self._seeding_time = 1
        self._notify = True
        if not config:
            return
        self._enabled = bool(config.get("enabled"))
        mode = config.get("mode") or MODE_ON_DOWNLOAD
        # 非法值回退到默认模式
        self._mode = mode if mode in (MODE_ON_DOWNLOAD, MODE_ON_TRANSFER) else MODE_ON_DOWNLOAD
        try:
            self._seeding_time = max(0, int(config.get("seeding_time", 1)))
        except (TypeError, ValueError):
            self._seeding_time = 1
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
                                        "component": "VSelect",
                                        "props": {
                                            "model": "mode",
                                            "label": "停止做种时机",
                                            "items": [
                                                {
                                                    "title": "下载添加时设做种限制（做种满 N 分钟自动停，不依赖整理）",
                                                    "value": MODE_ON_DOWNLOAD,
                                                },
                                                {
                                                    "title": "整理完成时停止做种（需开启自动整理）",
                                                    "value": MODE_ON_TRANSFER,
                                                },
                                            ],
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "seeding_time",
                                            "label": "做种时长（分钟，仅「下载添加时」模式生效）",
                                            "placeholder": "下载完成后做种满该分钟数自动停止，0 表示下载完即停",
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
                                            "label": "操作后发送消息通知",
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
                                                "仅作用于 MoviePilot 创建的种子。「下载添加时」模式由下载器"
                                                "（qBittorrent / Transmission）自动计时停止；rTorrent 不支持做种时间限制，"
                                                "请改用「整理完成时」模式。文件不会被删除，可在下载器手动恢复。"
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
            "mode": MODE_ON_DOWNLOAD,
            "seeding_time": 1,
            "notify": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页。"""
        if not self._enabled:
            return None
        mode_text = "下载添加时设做种限制" if self._mode == MODE_ON_DOWNLOAD else "整理完成时停止做种"
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success",
                    "variant": "tonal",
                    "text": f"自动停止做种已启用，当前模式：{mode_text}",
                },
            }
        ]

    def stop_service(self) -> None:
        """停止插件后台服务。本插件无后台服务，空实现即可。"""
        return None

    # ===== 模式 B：下载添加时设置做种时间限制 =====
    @eventmanager.register(EventType.DownloadAdded)
    def on_download_added(self, event: Event):
        """下载添加时，给种子设置做种时间限制，由下载器自动计时停止。

        :param event: 下载添加事件，事件数据含 ``hash`` 与 ``downloader``。
        """
        if not self._enabled or self._mode != MODE_ON_DOWNLOAD:
            return

        data = event.event_data or {}
        torrent_hash = data.get("hash")
        downloader = data.get("downloader")

        # 缺少种子哈希则无法设置
        if not torrent_hash:
            return

        try:
            result = self.chain.update_torrent(
                hash_string=torrent_hash,
                downloader=downloader,
                seeding_time_limit=self._seeding_time,
            )
        except Exception as e:
            logger.error(
                f"设置做种时间限制异常: hash={torrent_hash}, error={e}"
            )
            return

        # update_torrent 返回各项修改结果字典；seeding_time_limit 不被支持时该键为 False
        if self._is_seeding_limit_set(result):
            logger.info(
                f"已设置做种 {self._seeding_time} 分钟后停止: "
                f"hash={torrent_hash}, downloader={downloader}"
            )
            if self._notify:
                self._send_notice(
                    "已设置做种限制",
                    f"做种 {self._seeding_time} 分钟后自动停止\n"
                    f"哈希：{torrent_hash}\n下载器：{downloader or '默认'}",
                )
        else:
            logger.warning(
                f"设置做种时间限制失败（下载器可能不支持，如 rTorrent）: "
                f"hash={torrent_hash}, result={result}"
            )

    @staticmethod
    def _is_seeding_limit_set(result: Any) -> bool:
        """判断 update_torrent 的返回是否成功设置了做种时间限制。

        qBittorrent / Transmission 返回形如 ``{"seeding_limits": True, ...}`` 的字典；
        rTorrent 对不支持项返回 ``{"seeding_limits": False}``；异常或 None 视为失败。

        :param result: update_torrent 返回值
        :return: True 表示设置成功
        """
        if not isinstance(result, dict):
            return False
        # 链方法对各下载器的结果键名一致为 seeding_limits（见各模块 update_torrent）
        return bool(result.get("seeding_limits"))

    # ===== 模式 A：整理完成时停止做种 =====
    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        """整理完成后，自动停止对应下载任务的做种（仅模式 A 生效）。

        整理与下载是两个独立环节：整理可能由无下载来源的文件触发，也可能在下载未完成时
        被手动触发（多集种子先下完部分集）。因此本方法在停止做种前必须复核两件事：
        种子由 MoviePilot 创建、且已整体下载完成。对只下载了一部分就整理的种子，
        跳过停做种，避免卡死剩余内容的下载。

        :param event: 整理完成事件，事件数据含 ``download_hash`` 与 ``downloader``。
        """
        if not self._enabled or self._mode != MODE_ON_TRANSFER:
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
                self._send_notice(
                    "已停止做种",
                    f"哈希：{download_hash}\n下载器：{downloader or '默认'}",
                )
        else:
            logger.warning(
                f"停止做种失败: hash={download_hash}, downloader={downloader}"
            )

    def _should_stop_seeding(
            self, download_hash: str, downloader: Optional[str]
    ) -> Tuple[bool, str]:
        """判断是否应对该种子停止做种（模式 A 复核），返回 (是否停止, 原因说明)。

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

    def _send_notice(self, title: str, text: str) -> None:
        """发送消息通知。

        :param title: 通知标题
        :param text: 通知正文
        """
        try:
            self.post_message(
                mtype=NotificationType.Organize,
                title=title,
                text=text,
            )
        except Exception as e:
            logger.warning(f"发送通知失败: {e}")
