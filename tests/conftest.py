"""pytest 全局 conftest：注入 ``app.*`` stub 与被测插件包路径。

本仓库采用**完全独立**的单测方案，不依赖 MoviePilot 后端。这里在用例收集前完成两件事：

1. 把 ``tests/stubs`` 下的极薄 ``app.*`` stub 注册到 ``sys.modules``，使插件源码中的
   ``from app.core.event import ...`` 等导入解析到 stub 而非真实后端；
2. 把仓库根的 ``plugins.v2`` 加入 ``sys.path``，使 ``from autostopseed import AutoStopSeed`` 可用。

顺序很关键：必须先注册 ``app`` 子模块，再导入插件包；插件包在导入时即执行
``from app.* import ...``。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_STUBS_DIR = _TESTS_DIR / "stubs"
_PLUGINS_V2_DIR = _REPO_ROOT / "plugins.v2"


def _load_stub_module(qualified_name: str, file_path: Path) -> None:
    """从文件加载一个 stub 模块并注册到 sys.modules。

    :param qualified_name: 模块全限定名，如 ``app.core.event``
    :param file_path: stub 文件路径
    """
    spec = importlib.util.spec_from_file_location(qualified_name, file_path)
    assert spec is not None and spec.loader is not None, f"无法加载 stub: {file_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)


def _register_app_stubs() -> None:
    """注册所有插件导入所需的 ``app.*`` stub 模块。

    包占位（``app``、``app.core``、``app.schemas``、``app.db``）先注册，
    再注册叶子模块文件（``app.core.event`` 等）。注意 ``app.plugins`` 是模块文件而非包。
    """
    # 包占位
    _load_stub_module("app", _STUBS_DIR / "app" / "__init__.py")
    _load_stub_module("app.core", _STUBS_DIR / "app" / "core" / "__init__.py")
    _load_stub_module("app.schemas", _STUBS_DIR / "app" / "schemas" / "__init__.py")
    _load_stub_module("app.db", _STUBS_DIR / "app" / "db" / "__init__.py")

    # 叶子模块（插件导入链实际用到的）
    _load_stub_module("app.core.event", _STUBS_DIR / "app" / "core" / "event.py")
    _load_stub_module("app.log", _STUBS_DIR / "app" / "log.py")
    _load_stub_module("app.plugins", _STUBS_DIR / "app" / "plugins.py")
    _load_stub_module("app.schemas.types", _STUBS_DIR / "app" / "schemas" / "types.py")


def pytest_configure(config: pytest.Config) -> None:
    """用例收集前：注册 stub 并把 plugins.v2 加入 sys.path。"""
    _register_app_stubs()
    plugins_v2 = str(_PLUGINS_V2_DIR)
    if plugins_v2 not in sys.path:
        sys.path.insert(0, plugins_v2)
