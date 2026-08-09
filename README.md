# AutoStopSeed（自动停止做种）

MoviePilot **V2 插件**：MoviePilot 创建的下载任务**整理完成后，自动停止对应种子的做种**（暂停上传，不删除文件）。

## 为什么需要它

订阅或手动搜索下载完成后，MoviePilot 会持续做种上传。部分场景（带宽有限、PT 站点分享率已达标、纯归档使用）希望**入库后即停止做种**，但又不希望删掉文件或种子。

本插件在「整理完成」这一刻自动暂停做种，文件原样保留，随时可在下载器里手动恢复。

## 工作原理

基于 MoviePilot v2 原生机制，无需任何额外配置：

1. **监听事件**：插件订阅系统事件 `EventType.TransferComplete`（文件整理完成时由 `app/chain/transfer.py` 发出），事件数据携带 `download_hash` 与 `downloader`。
2. **只管 MoviePilot 自己创建的种子**：
   - MoviePilot 创建的每个种子都会被打上内置标签 `TORRENT_TAG`（默认 `MOVIEPILOT`）。
   - 插件在停止前用 `list_torrents(hashs=[hash])` 复核：该接口默认按内置标签过滤，**查不到的种子（如 qb 手动添加、外部导入）直接跳过**，绝不会误停。
3. **停止做种**：调用链方法 `self.chain.stop_torrents(hash, downloader)`（qBittorrent / Transmission / rTorrent 均已实现），效果是暂停上传、文件不删。

> 与「下载器自带做种时间/分享率限制」的区别：本插件是「整理完成立即停」，更精确；下载器限制是「按时间/比率停」。

## 安装

把本仓库注册为 MoviePilot 插件市场仓库后安装即可：

1. MoviePilot → 设定 → 插件 → 添加插件市场，填入本仓库 GitHub 地址。
2. 在插件市场找到「自动停止做种」并安装。
3. 启用插件，按需配置。

## 配置项

| 配置 | 说明 | 默认 |
|------|------|------|
| 启用插件 | 总开关 | 关 |
| 停止做种后发送消息通知 | 停止成功后推送一条消息 | 开 |

## 常见问题

**Q：会不会删掉我的文件？**
不会。`stop_torrents` 只暂停做种（上传），文件与种子都保留。

**Q：会不会停掉我用 qBittorrent 自己加的种子？**
不会。插件在停止前会按 MoviePilot 内置标签复核，只有 MP 创建的种子才会被处理。

**Q：手动整理一个外部种子时会触发吗？**
通常不会。MoviePilot 的整理扫描默认就只扫带内置标签的种子；即便极端情况下混入，插件复核这一关也会跳过。

**Q：如何恢复做种？**
到下载器（qBittorrent / Transmission）里对相应任务点「继续」即可。

## 单元测试

本仓库自带**完全独立**的单元测试，**不依赖 MoviePilot 后端**：

```bash
python -m pytest tests/ -v
```

测试通过 stub 隔离 `app.*` 依赖，用 `object.__new__` 绕过插件 `__init__`，只验证纯逻辑。详见 `tests/` 目录。

## 版本

- v1.0.0：初始版本。

## License

MIT
