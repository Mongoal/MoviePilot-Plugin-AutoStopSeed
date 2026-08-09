"""AutoStopSeed 插件单测（pytest 原生，完全独立，不依赖 MoviePilot 后端）。

覆盖两种互斥模式：
- 模式 B（默认）：``on_download_added`` 监听 ``DownloadAdded``，调用
  ``chain.update_torrent(seeding_time_limit=N)``，由下载器自动计时停止；
- 模式 A：``on_transfer_complete`` 监听 ``TransferComplete``，复核 MP 种子 + 下载完成后
  调用 ``chain.stop_torrents``。

以及配置解析、模式互斥、异常容错等。被测实例用 ``object.__new__`` 绕过
``_PluginBase.__init__``，手动注入 mock 的 ``chain`` / ``eventmanager``。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

# 依赖 conftest 注册的 app.* stub 与 plugins.v2 路径
from autostopseed import AutoStopSeed, MODE_ON_DOWNLOAD, MODE_ON_TRANSFER
from app.core.event import Event


# ---------- 辅助构造 ----------

def _make_plugin(enabled: bool = True, mode: str = MODE_ON_DOWNLOAD,
                 seeding_time: int = 1, notify: bool = True,
                 chain: MagicMock = None) -> AutoStopSeed:
    """构造一个已初始化的插件实例，绕过 _PluginBase.__init__。

    :param enabled: 是否启用
    :param mode: 模式（MODE_ON_DOWNLOAD / MODE_ON_TRANSFER）
    :param seeding_time: 做种时长（分钟）
    :param notify: 是否开启通知
    :param chain: 可选的自定义 chain mock；默认新建
    :return: 初始化完成的 AutoStopSeed 实例
    """
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = chain if chain is not None else MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin({
        "enabled": enabled,
        "mode": mode,
        "seeding_time": seeding_time,
        "notify": notify,
    })
    return plugin


def _make_event(hash_value=None, download_hash=None, downloader=None) -> Event:
    """构造事件对象。

    - DownloadAdded 事件数据键为 ``hash``；
    - TransferComplete 事件数据键为 ``download_hash``；
    由调用方按需传入对应参数。
    """
    data = {}
    if hash_value is not None:
        data["hash"] = hash_value
    if download_hash is not None:
        data["download_hash"] = download_hash
    if downloader is not None:
        data["downloader"] = downloader
    return Event(event_type="event", event_data=data)


def _torrent(progress: float = 100.0, **extra) -> SimpleNamespace:
    """构造一个带 progress 属性的种子对象（模拟 DownloaderTorrent）。

    真实 DownloaderTorrent 是 pydantic 模型，``progress`` 为 0-100 的百分比。
    插件用 ``getattr(t, "progress", 0)`` 读取，故 SimpleNamespace 即可。
    """
    return SimpleNamespace(progress=progress, **extra)


# ---------- 配置解析 / init_plugin ----------

def test_init_plugin_defaults_to_download_mode():
    """默认模式应为「下载添加时」（MODE_ON_DOWNLOAD）。"""
    plugin = _make_plugin()
    assert plugin._mode == MODE_ON_DOWNLOAD


def test_init_plugin_seeding_time_default():
    """做种时长默认 1 分钟。"""
    plugin = _make_plugin()
    assert plugin._seeding_time == 1


def test_init_plugin_transfer_mode():
    """可显式选择模式 A（整理完成时）。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER)
    assert plugin._mode == MODE_ON_TRANSFER


def test_init_plugin_invalid_mode_falls_back():
    """非法 mode 值应回退到默认模式 B。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin({"enabled": True, "mode": "nonexistent_mode"})
    assert plugin._mode == MODE_ON_DOWNLOAD


def test_init_plugin_seeding_time_negative_clamped_to_zero():
    """做种时长为负数应被钳制为 0（0 = 下载完即停）。"""
    plugin = _make_plugin(seeding_time=-5)
    assert plugin._seeding_time == 0


def test_init_plugin_seeding_time_invalid_falls_back():
    """做种时长非法值应回退到 1。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin({"enabled": True, "seeding_time": "abc"})
    assert plugin._seeding_time == 1


def test_init_plugin_idempotent_across_reinits():
    """多次初始化应幂等：状态由本次配置决定，不残留上一次。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin({"enabled": True, "mode": MODE_ON_TRANSFER, "seeding_time": 5})
    assert plugin._enabled and plugin._mode == MODE_ON_TRANSFER and plugin._seeding_time == 5
    # 第二次切回默认
    plugin.init_plugin({"enabled": False, "mode": MODE_ON_DOWNLOAD, "seeding_time": 1})
    assert not plugin._enabled
    assert plugin._mode == MODE_ON_DOWNLOAD
    assert plugin._seeding_time == 1


def test_init_plugin_empty_config_is_disabled():
    """空配置应得到默认禁用、模式 B、做种 1 分钟。"""
    plugin = object.__new__(AutoStopSeed)
    plugin.chain = MagicMock()
    plugin.systemconfig = MagicMock()
    plugin.systemmessage = MagicMock()
    plugin.eventmanager = MagicMock()
    plugin.init_plugin(None)
    assert plugin.get_state() is False
    assert plugin._mode == MODE_ON_DOWNLOAD
    assert plugin._seeding_time == 1


def test_get_state_follows_enabled():
    """get_state 跟随 enabled 开关。"""
    assert _make_plugin(enabled=True).get_state() is True
    assert _make_plugin(enabled=False).get_state() is False


# ---------- get_form / get_page ----------

def test_get_form_returns_schema_and_defaults():
    """get_form 应返回 (表单结构, 默认配置) 且默认值正确。"""
    plugin = AutoStopSeed()
    form, defaults = plugin.get_form()
    assert isinstance(form, list) and len(form) > 0
    assert defaults["enabled"] is False
    assert defaults["mode"] == MODE_ON_DOWNLOAD
    assert defaults["seeding_time"] == 1
    assert defaults["notify"] is True


def test_get_form_components_use_vform_root():
    """表单根组件应为 VForm（Vuetify JSON 模式）。"""
    form, _ = AutoStopSeed().get_form()  # noqa: 静态逻辑，直接实例化即可
    assert form[0]["component"] == "VForm"
    assert isinstance(form[0]["content"], list) and form[0]["content"]


def test_get_form_mode_select_has_both_options():
    """模式选择应提供两个互斥选项。"""
    form, _ = AutoStopSeed().get_form()
    # 找到 VSelect 组件的 items
    mode_values = []
    for row in form[0]["content"]:
        for col in row.get("content", []):
            comp = col.get("content", [{}])[0]
            if comp.get("component") == "VSelect":
                mode_values = [item["value"] for item in comp["props"]["items"]]
    assert set(mode_values) == {MODE_ON_DOWNLOAD, MODE_ON_TRANSFER}


def test_get_page_none_when_disabled():
    """未启用时 get_page 返回 None。"""
    plugin = _make_plugin(enabled=False)
    assert plugin.get_page() is None


def test_get_page_shows_current_mode():
    """启用时 get_page 返回非空结构并体现当前模式。"""
    plugin = _make_plugin(enabled=True, mode=MODE_ON_TRANSFER)
    page = plugin.get_page()
    assert isinstance(page, list) and len(page) > 0
    assert "整理完成时停止做种" in page[0]["props"]["text"]


# ---------- 模式 B：on_download_added ----------

def test_download_mode_sets_seeding_time_limit():
    """模式 B：DownloadAdded 应调用 update_torrent(seeding_time_limit=N)。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, seeding_time=3)
    plugin.chain.update_torrent.return_value = {"seeding_limits": True}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once_with(
        hash_string="abc", downloader="qBittorrent", seeding_time_limit=3
    )


def test_download_mode_default_seeding_time_is_one():
    """默认做种时长 1 分钟应正确传递。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD)
    plugin.chain.update_torrent.return_value = {"seeding_limits": True}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once_with(
        hash_string="abc", downloader="qBittorrent", seeding_time_limit=1
    )


def test_download_mode_zero_seeding_time():
    """做种时长 0（下载完即停）应能正确传递。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, seeding_time=0)
    plugin.chain.update_torrent.return_value = {"seeding_limits": True}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once_with(
        hash_string="abc", downloader="qBittorrent", seeding_time_limit=0
    )


def test_download_mode_disabled_does_nothing():
    """插件未启用时不调用 update_torrent。"""
    plugin = _make_plugin(enabled=False, mode=MODE_ON_DOWNLOAD)
    plugin.on_download_added(_make_event(hash_value="abc"))
    plugin.chain.update_torrent.assert_not_called()


def test_download_mode_wrong_mode_does_nothing():
    """模式 A 时收到 DownloadAdded 不应处理（模式互斥）。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER)
    plugin.on_download_added(_make_event(hash_value="abc"))
    plugin.chain.update_torrent.assert_not_called()


def test_download_mode_no_hash_does_nothing():
    """DownloadAdded 缺少 hash 时不调用 update_torrent。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD)
    plugin.on_download_added(_make_event(hash_value=None, downloader="qBittorrent"))
    plugin.chain.update_torrent.assert_not_called()


def test_download_mode_notify_sent_when_success():
    """设置成功且开启通知时应发消息。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, notify=True)
    plugin.chain.update_torrent.return_value = {"seeding_limits": True}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.post_message.assert_called_once()
    _, kwargs = plugin.chain.post_message.call_args
    assert "已设置做种限制" in kwargs.get("title", "")


def test_download_mode_no_notify_when_disabled():
    """notify=False 时即使设置成功也不发通知。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, notify=False)
    plugin.chain.update_torrent.return_value = {"seeding_limits": True}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_download_mode_rtorrent_unsupported_no_notify():
    """rTorrent 返回 seeding_limits=False（不支持）时应判定失败、不发通知。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, notify=True)
    plugin.chain.update_torrent.return_value = {"seeding_limits": False}

    plugin.on_download_added(_make_event(hash_value="abc", downloader="rTorrent"))

    plugin.chain.update_torrent.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_download_mode_none_result_treated_as_failure():
    """update_torrent 返回 None（下载器不存在等）应判定失败。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, notify=True)
    plugin.chain.update_torrent.return_value = None

    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once()
    plugin.chain.post_message.assert_not_called()


def test_download_mode_exception_swallowed():
    """update_torrent 抛异常时不应崩溃、不发通知。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD, notify=True)
    plugin.chain.update_torrent.side_effect = RuntimeError("下载器离线")

    # 不应抛出
    plugin.on_download_added(_make_event(hash_value="abc", downloader="qBittorrent"))

    plugin.chain.update_torrent.assert_called_once()
    plugin.chain.post_message.assert_not_called()


# ---------- _is_seeding_limit_set 纯函数 ----------

def test_is_seeding_limit_set_true_for_dict_true():
    """返回字典含 seeding_limits=True 判定为成功。"""
    assert AutoStopSeed._is_seeding_limit_set({"seeding_limits": True}) is True


def test_is_seeding_limit_set_false_for_dict_false():
    """返回字典含 seeding_limits=False（rTorrent 不支持）判定为失败。"""
    assert AutoStopSeed._is_seeding_limit_set({"seeding_limits": False}) is False


def test_is_seeding_limit_set_false_for_none():
    """返回 None 判定为失败。"""
    assert AutoStopSeed._is_seeding_limit_set(None) is False


def test_is_seeding_limit_set_false_for_non_dict():
    """返回非字典（如 bool）判定为失败。"""
    assert AutoStopSeed._is_seeding_limit_set(True) is False


def test_is_seeding_limit_set_false_for_missing_key():
    """返回字典但无 seeding_limits 键判定为失败。"""
    assert AutoStopSeed._is_seeding_limit_set({"other": True}) is False


# ---------- 模式 A：on_transfer_complete ----------

def test_transfer_mode_stops_completed_mp_torrent():
    """模式 A：MP 创建且下载完成的种子应调用 stop_torrents。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once_with(
        hashs="abc", downloader="qBittorrent"
    )


def test_transfer_mode_wrong_mode_does_nothing():
    """模式 B 时收到 TransferComplete 不应处理（模式互斥）。"""
    plugin = _make_plugin(mode=MODE_ON_DOWNLOAD)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_disabled_does_nothing():
    """插件未启用时收到 TransferComplete 不停种。"""
    plugin = _make_plugin(enabled=False, mode=MODE_ON_TRANSFER)
    plugin.on_transfer_complete(_make_event(download_hash="abc"))
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_no_hash_does_nothing():
    """TransferComplete 缺少 download_hash 时不停种。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER)
    plugin.on_transfer_complete(_make_event(download_hash=None))
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_non_moviepilot_torrent_skipped():
    """模式 A：非 MP 种子（list_torrents 复核为空）应跳过。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=True)
    plugin.chain.list_torrents.return_value = []
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()
    plugin.chain.post_message.assert_not_called()


def test_transfer_mode_incomplete_download_skipped():
    """模式 A：下载未完成（progress<100）即使手动整理也不停种，避免卡死下载。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(30.0)]
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_partially_completed_skipped():
    """模式 A：多文件种子任一未达 100% 视为未完成，跳过。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100), _torrent(60)]
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_progress_just_below_100_skipped():
    """模式 A：progress=99.9 应视为未完成，跳过。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=False)
    plugin.chain.list_torrents.return_value = [_torrent(99.9)]
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_progress_missing_treated_as_incomplete():
    """模式 A：种子对象无 progress 字段按 0 处理，跳过。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=False)
    plugin.chain.list_torrents.return_value = [{"hash": "abc"}]
    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )
    plugin.chain.stop_torrents.assert_not_called()


def test_transfer_mode_notify_sent_when_success():
    """模式 A：停种成功且开启通知时发消息。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.post_message.assert_called_once()
    _, kwargs = plugin.chain.post_message.call_args
    assert kwargs.get("title") == "已停止做种"


def test_transfer_mode_list_torrents_exception_treats_as_ok():
    """模式 A：复核查询异常时保守放行，仍尝试停种。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=False)
    plugin.chain.list_torrents.side_effect = RuntimeError("查询失败")
    plugin.chain.stop_torrents.return_value = True

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once_with(
        hashs="abc", downloader="qBittorrent"
    )


def test_transfer_mode_stop_exception_swallowed():
    """模式 A：stop_torrents 抛异常时不崩溃、不发通知。"""
    plugin = _make_plugin(mode=MODE_ON_TRANSFER, notify=True)
    plugin.chain.list_torrents.return_value = [_torrent(100)]
    plugin.chain.stop_torrents.side_effect = RuntimeError("下载器离线")

    plugin.on_transfer_complete(
        _make_event(download_hash="abc", downloader="qBittorrent")
    )

    plugin.chain.stop_torrents.assert_called_once()
    plugin.chain.post_message.assert_not_called()


# ---------- stop_service / 元信息 ----------

def test_stop_service_returns_none():
    """stop_service 应无副作用地返回 None。"""
    plugin = _make_plugin(enabled=True)
    assert plugin.stop_service() is None


def test_get_command_returns_empty_list():
    """插件不注册远程命令。"""
    assert AutoStopSeed.get_command() == []


def test_get_api_returns_empty_list():
    """插件不注册 API。"""
    plugin = _make_plugin(enabled=True)
    assert plugin.get_api() == []


def test_plugin_metadata_present():
    """插件元信息属性应已定义且与 package.v2.json 一致。"""
    assert AutoStopSeed.plugin_name == "自动停止做种"
    assert AutoStopSeed.plugin_version == "1.1.0"
    assert AutoStopSeed.plugin_config_prefix == "autostopseed_"
