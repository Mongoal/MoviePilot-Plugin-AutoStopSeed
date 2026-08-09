"""AutoStopSeed 插件单测（pytest 原生，完全独立，不依赖 MoviePilot 后端）。

覆盖范围：
- ``init_plugin`` 配置读取与默认值、多次初始化的幂等性；
- ``get_state`` 跟随启用开关；
- ``get_form`` 返回结构有效且含默认配置；
- ``get_page`` 启用/未启用时的返回；
- 核心 ``on_transfer_complete`` 的全部分支：
    * 插件未启用 → 不停种；
    * 无 download_hash → 不停种；
    * 非 MP 创建种子（list_torrents 复核为空）→ 不停种；
    * MP 创建种子（复核非空）→ 调用 stop_torrents(hash, downloader)；
    * stop_torrents 抛异常 → 不崩溃、不调用通知；
    * notify=True → 停种成功后发通知；notify=False → 不发通知；
- ``stop_service`` 无副作用。

被测实例构造：用 ``object.__new__`` 绕过 ``_PluginBase.__init__``（避免拉起重量级依赖），
再手动注入 mock 的 ``chain``、``eventmanager``。这与官方插件仓单测的「只测纯逻辑」原则一致。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# 依赖 conftest 注册的 app.* stub 与 plugins.v2 路径
from autostopseed import AutoStopSeed
from app.core.event import Event


# ---------- 辅助构造 ----------

def _make_plugin(enabled: bool = True, notify: bool = True,
                 chain: MagicMock = None) -> AutoStopSeed:
    """构造一个已初始化的插件实例，绕过 _PluginBase.__init__。

    :param enabled: 是否启用
    :param notify: 是否开启通知
    :param chain: 可选的自定义 chain mock；默认新建
    :return: 初始化完成的 AutoStopSeed 实例
    """
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = chain if chain is not None else MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin({"enabled": enabled, "notify": notify})
    return plugin


def _make_event(download_hash=None, downloader=None) -> Event:
    """构造一个 TransferComplete 形态的事件对象。"""
    data = {}
    if download_hash is not None:
        data["download_hash"] = download_hash
    if downloader is not None:
        data["downloader"] = downloader
    return Event(event_type="transfer.complete", event_data=data)


def _torrent(progress: float = 100.0, **extra) -> SimpleNamespace:
    """构造一个带 progress 属性的种子对象（模拟 DownloaderTorrent）。

    真实 DownloaderTorrent 是 pydantic 模型，``progress`` 为 0-100 的百分比。
    插件用 ``getattr(t, "progress", 0)`` 读取，故 SimpleNamespace 即可。
    """
    return SimpleNamespace(progress=progress, **extra)


# ---------- init_plugin / get_state ----------

def test_init_plugin_reads_enabled_flag():
    """init_plugin 应正确读取 enabled 开关。"""
    plugin = _make_plugin(enabled=True)
    assert plugin.get_state() is True
    assert plugin._enabled is True


def test_init_plugin_disabled():
    """未启用时 get_state 返回 False。"""
    plugin = _make_plugin(enabled=False)
    assert plugin.get_state() is False


def test_init_plugin_notify_default_true():
    """notify 缺省时应为 True。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    # 只传 enabled，不传 notify
    plugin.init_plugin({"enabled": True})
    assert plugin._notify is True


def test_init_plugin_idempotent_across_reinits():
    """多次初始化应幂等：状态由本次配置决定，不残留上一次。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()

    plugin.init_plugin({"enabled": True, "notify": True})
    assert plugin._enabled is True and plugin._notify is True

    # 第二次初始化为关闭，应覆盖上一次的状态
    plugin.init_plugin({"enabled": False, "notify": False})
    assert plugin._enabled is False and plugin._notify is False


def test_init_plugin_with_empty_config_is_disabled():
    """空配置应得到默认的禁用状态。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin(None)
    assert plugin.get_state() is False
    assert plugin._enabled is False


# ---------- get_form / get_page ----------

def test_get_form_returns_schema_and_defaults():
    """get_form 应返回 (表单结构, 默认配置) 且默认配置含必要键。

    get_form 是静态逻辑（不读运行时状态），可在直接实例化的 stub 基类对象上调用。
    """
    plugin = AutoStopSeed()
    form, defaults = plugin.get_form()
    assert isinstance(form, list) and len(form) > 0
    assert "enabled" in defaults
    assert "notify" in defaults
    assert defaults["enabled"] is False
    assert defaults["notify"] is True


def test_get_form_components_use_vform_root():
    """表单根组件应为 VForm（Vuetify JSON 模式）。"""
    form, _ = AutoStopSeed().get_form()  # noqa: 静态逻辑，直接实例化即可
    assert form[0]["component"] == "VForm"
    # content 应为非空列表
    assert isinstance(form[0]["content"], list) and form[0]["content"]


def test_get_page_none_when_disabled():
    """未启用时 get_page 返回 None。"""
    plugin = _make_plugin(enabled=False)
    assert plugin.get_page() is None


def test_get_page_returns_content_when_enabled():
    """启用时 get_page 返回非空结构。"""
    plugin = _make_plugin(enabled=True)
    page = plugin.get_page()
    assert isinstance(page, list) and len(page) > 0


# ---------- on_transfer_complete 核心 ----------

def test_transfer_disabled_does_nothing():
    """插件未启用时不应调用任何 chain 方法。"""
    plugin = _make_plugin(enabled=False)
    plugin.on_transfer_complete(_make_event(download_hash="abc"))
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_no_hash_does_nothing():
    """事件缺少 download_hash 时不应停种。"""
    plugin = _make_plugin(enabled=True)
    plugin.on_transfer_complete(_make_event(download_hash=None))
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_non_moviepilot_torrent_skipped():
    """list_torrents 复核为空（非 MP 创建）应跳过停种。"""
    plugin = _make_plugin(enabled=True)
    plugin.chain.list_torrents.return_value = []  # 非 MP 种子查不到
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()
    # 通知也不应发送
    plugin.chain.post_message.assert_not_called()


def test_transfer_moviepilot_torrent_stops_seeding():
    """MP 创建且下载完成（progress=100）的种子应调用 stop_torrents(hash, downloader)。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]  # MP 种子 + 已完成
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once_with(
        hashs="abc", downloader="qBittorrent"
    )


def test_transfer_notify_sent_when_enabled():
    """notify=True 且停种成功时应发送消息通知。"""
    plugin = _make_plugin(enabled=True, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.post_message.assert_called_once()
    # 验证通知内容包含标题与种子哈希
    _, kwargs = plugin.chain.post_message.call_args
    assert kwargs.get("title") == "自动停止做种"
    assert "abc" in kwargs.get("text", "")


def test_transfer_no_notify_when_disabled():
    """notify=False 时即使停种成功也不发通知。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_transfer_no_notify_when_stop_fails():
    """停种失败（返回 False）时不应发通知。"""
    plugin = _make_plugin(enabled=True, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = False

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_transfer_stop_torrents_exception_swallowed():
    """stop_torrents 抛异常时不应崩溃、不应发通知。"""
    plugin = _make_plugin(enabled=True, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.side_effect = RuntimeError("下载器离线")

    # 不应抛出
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_transfer_list_torrents_exception_treats_as_moviepilot():
    """list_torrents 复核异常时应保守放行（按 MP 种子处理）并继续停种。

    设计上查询失败时保守放行，避免因临时网络问题漏停；此处验证该容错路径。
    """
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.side_effect = RuntimeError("查询失败")
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    # 查询异常 → 放行 → 仍尝试停种
    plugin.chain.stop_torrents.assert_called_once_with(
        hashs="abc", downloader="qBittorrent"
    )


def test_transfer_list_torrents_uses_hash_as_list_and_downloader():
    """复核时应以 [hash] 形式查询并带上 downloader。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.list_torrents.assert_called_once_with(
        hashs=["abc"], downloader="qBittorrent"
    )


def test_transfer_default_downloader_passed_through():
    """downloader 为 None 时也应正常传给 stop_torrents。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(_make_event(download_hash="abc", downloader=None))

    plugin.chain.stop_torrents.assert_called_once_with(
        hashs="abc", downloader=None
    )


# ---------- 下载未完成不应停种（核心安全分支） ----------

def test_transfer_incomplete_download_skipped():
    """下载未完成（progress<100）即使手动触发整理，也不应停做种，避免卡死剩余下载。

    场景：10 集种子里只下完 3 集就被手动整理 → 不能停做种，否则剩下 7 集永远下不完。
    """
    plugin = _make_plugin(enabled=True, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(30.0)]  # MP 种子但只下 30%
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_not_called()
    plugin.chain.post_message.assert_not_called()


def test_transfer_partially_completed_torrents_skipped():
    """多个文件任务中只要有任一未达 100%，整粒种子视为未完成，不应停做种。

    对应 ``all(progress >= 100 for t in torrents)`` 的 all() 语义：
    多文件种子任意文件未下完即整体未完成。
    """
    plugin = _make_plugin(enabled=True, notify=True)
    plugin.chain.list_torrents.return_value = [
        _torrent(100),   # 文件1 已完成
        _torrent(60),    # 文件2 未完成
    ]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_progress_zero_torrent_skipped():
    """progress 为 0（刚开始下载即手动整理）不应停做种。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(0)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_progress_just_below_100_skipped():
    """progress=99.9（接近但未达 100）应视为未完成，跳过停种。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(99.9)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_progress_exactly_100_stops():
    """progress 恰好 100 视为完成，正常停做种（边界值）。"""
    plugin = _make_plugin(enabled=True, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once()


def test_transfer_progress_missing_treated_as_incomplete():
    """种子对象无 progress 字段时按 0 处理，视为未完成，跳过停种。"""
    plugin = _make_plugin(enabled=True, notify=False)
    # 用 dict（无 progress 属性）模拟异常数据，getattr 返回 0
    plugin.chain.list_torrents.return_value = [{"hash": "abc"}]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_not_called()


# ---------- stop_service ----------

def test_stop_service_returns_none():
    """stop_service 应无副作用地返回 None。"""
    plugin = _make_plugin(enabled=True)
    assert plugin.stop_service() is None


# ---------- 静态/命令注册 ----------

def test_get_command_returns_empty_list():
    """插件不注册远程命令。"""
    assert AutoStopSeed.get_command() == []


def test_get_api_returns_empty_list():
    """插件不注册 API。"""
    plugin = _make_plugin(enabled=True)
    assert plugin.get_api() == []


# ---------- 插件元信息一致性 ----------

def test_plugin_metadata_present():
    """插件元信息属性应已定义且与 package.v2.json 一致（关键项）。"""
    assert AutoStopSeed.plugin_name == "自动停止做种"
    assert AutoStopSeed.plugin_version == "1.0.0"
    assert AutoStopSeed.plugin_config_prefix == "autostopseed_"
