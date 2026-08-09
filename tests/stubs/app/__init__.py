"""测试用 app 包占位（仅用于让 ``app.*`` 可导入）。

本目录下所有模块均为「极薄 stub」，目的是在**不依赖 MoviePilot 后端**的前提下
让被测插件包（``plugins.v2/autostopseed``）能成功导入。实际逻辑由各 stub 提供
最小可用实现，真正的副作用（DB / 网络 / 事件总线）由 conftest 与各用例 mock。
"""
