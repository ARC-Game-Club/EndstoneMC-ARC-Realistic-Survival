# -*- coding: utf-8 -*-
"""移动→口渴加速消耗 冒烟测试：PlayerMoveEvent 标记走插件侧集合，定时器读取后清除。

背景：旧实现用 setattr(player, '_arc_moving_flag', True) 给玩家对象挂动态属性，
但 endstone 的 Player 是 pybind11 绑定类（未开 py::dynamic_attr），setattr 抛
AttributeError 且被裸 try/except 吞掉，导致移动倍率从不生效。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
_endstone_stub.add_src_to_path()

from endstone import GameMode

from endstone_arc_realistic_survival.arc_realistic_survival import ARCRealisticSurvivalPlugin


class FakePlayer:
    def __init__(self, name, xuid):
        self.name = name
        self.xuid = xuid
        self.game_mode = GameMode.SURVIVAL
        self.popups = []

    def send_popup(self, msg):
        self.popups.append(msg)


class FakeEvent:
    def __init__(self, player):
        self.player = player


class FakePluginManager:
    def get_plugin(self, name):
        return None


class FakeTask:
    def cancel(self):
        pass


class FakeScheduler:
    def __init__(self):
        self.fn = None

    def run_task(self, plugin, fn, delay, period):
        self.fn = fn
        return FakeTask()


class FakeServer:
    def __init__(self):
        self.plugin_manager = FakePluginManager()
        self.online_players = []
        self.scheduler = FakeScheduler()


class FakeFracture:
    def __init__(self):
        self.ticks = []

    def tick_second(self, player, moved_second):
        self.ticks.append((player.name, moved_second))

    def speed_factor_for(self, player):
        return 1.0

    def on_player_quit(self, player):
        pass


class FakeLang:
    def GetText(self, key, lang_code=None):
        return None


def make_plugin():
    plugin = ARCRealisticSurvivalPlugin()
    plugin.server = FakeServer()
    plugin.language_manager = FakeLang()
    plugin.fracture_manager = FakeFracture()
    plugin.nutrition_manager = None
    plugin.player_xuid_to_thirst = {}
    plugin._last_nutrition_run = 1e18
    return plugin


def run_tick(plugin):
    """强制触发一次口渴结算（重置上次结算时间后手动执行定时器闭包）。"""
    plugin._last_thirst_run = 0.0
    plugin.server.scheduler.fn()


print("== 0) 主插件 import 成功")

print("\n== 1) 移动事件写入插件侧集合，不依赖玩家对象动态属性")
plugin = make_plugin()
steve = FakePlayer("Steve", "x1")
plugin.on_player_move(FakeEvent(steve))
assert "x1" in plugin._moved_flags and "x1" in plugin._moved_seconds
assert not hasattr(steve, "_arc_moving_flag") and not hasattr(steve, "_arc_moved_second")
print("   flags in plugin sets ok")

print("\n== 2) 定时器结算：移动时按倍率扣，结算后清除标记")
plugin.player_xuid_to_thirst["x1"] = 100
plugin.server.online_players = [steve]
plugin._start_survival_timer()
assert plugin.server.scheduler.fn is not None
run_tick(plugin)
decay = 100 - int(plugin.player_xuid_to_thirst["x1"])
assert decay == 2, f"移动倍率 2.0 应扣 2 点，实际扣 {decay}"
assert "x1" not in plugin._moved_flags, "结算后应清除移动标记"
print(f"   移动扣 {decay} 点（基础 1 × 倍率 2.0）ok")

print("\n== 3) 定时器结算：未移动按基础速率扣")
run_tick(plugin)
decay2 = 100 - int(plugin.player_xuid_to_thirst["x1"]) - decay
assert decay2 == 1, f"未移动应只扣 1 点，实际扣 {decay2}"
print(f"   静止扣 {decay2} 点 ok")

print("\n== 4) 骨裂每秒移动标记：动过才收到 moved_second=True")
plugin.fracture_manager.ticks.clear()
plugin.on_player_move(FakeEvent(steve))
run_tick(plugin)
assert plugin.fracture_manager.ticks[-1] == ("Steve", True), plugin.fracture_manager.ticks
run_tick(plugin)
assert plugin.fracture_manager.ticks[-1] == ("Steve", False), plugin.fracture_manager.ticks
print(f"   ticks={plugin.fracture_manager.ticks[-2:]} ok")

print("\n== 5) 创造模式不记录移动标记")
creative = FakePlayer("Cree", "x2")
creative.game_mode = GameMode.CREATIVE
plugin.on_player_move(FakeEvent(creative))
assert "x2" not in plugin._moved_flags and "x2" not in plugin._moved_seconds
print("   ok")

print("\n== 6) 退出清理标记")
plugin.on_player_move(FakeEvent(steve))
assert "x1" in plugin._moved_flags and "x1" in plugin._moved_seconds


class FakeQuitEvent:
    player = steve


plugin.on_player_quit(FakeQuitEvent())
assert "x1" not in plugin._moved_flags and "x1" not in plugin._moved_seconds
print("   ok")

print("\n== 7) 移动事件对 pybind11 风格只读对象也安全（setattr 会炸的对象不再使用）")
class ReadOnlyPlayer:
    """模拟 pybind11 未开 dynamic_attr：任何新属性 setattr 都抛 AttributeError。"""

    name = "Rigid"
    xuid = "x3"
    game_mode = GameMode.SURVIVAL

    def __setattr__(self, key, value):
        raise AttributeError(f"cannot set {key!r}")


rigid = ReadOnlyPlayer()
plugin.on_player_move(FakeEvent(rigid))
assert "x3" in plugin._moved_flags and "x3" in plugin._moved_seconds
print("   ok")

print("\nALL MOVE-THIRST SMOKE TESTS PASSED")
