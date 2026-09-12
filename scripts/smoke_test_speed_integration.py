# -*- coding: utf-8 -*-
"""ARS 移速集成冒烟测试：移速因子全部经 arc_attribute_core（硬依赖）路由。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
_endstone_stub.add_src_to_path()

from endstone_arc_realistic_survival.arc_realistic_survival import ARCRealisticSurvivalPlugin


class FakeFracture:
    def __init__(self, factor):
        self.factor = factor

    def speed_factor_for(self, player):
        return self.factor


class FakeAttrCore:
    def __init__(self):
        self.added = []
        self.removed = []

    def api_add_factor(self, player, key, source=None, amount=0.0, duration=None, operation="multiply"):
        self.added.append((key, source, round(amount, 6), duration))
        return True

    def api_remove_factor(self, player, key, source):
        self.removed.append((key, source))
        return True


class FakePluginManager:
    def __init__(self, core):
        self.core = core

    def get_plugin(self, name):
        return self.core if name == "arc_attribute_core" else None


class FakeServer:
    def __init__(self, core=None):
        self.plugin_manager = FakePluginManager(core)
        self.online_players = []


class FakePlayer:
    def __init__(self, name, xuid):
        self.name = name
        self.xuid = xuid
        self.walk_speed = 0.10
        self.is_sprinting = False


def make_plugin(core=None, logs=None):
    plugin = ARCRealisticSurvivalPlugin()
    plugin.server = FakeServer(core)
    plugin.player_xuid_to_thirst = {}
    if logs is not None:
        plugin._safe_log = lambda level, msg: logs.append((level, msg))
    return plugin


print("== 0) 主插件 import 成功 + depend 硬依赖声明")
assert ARCRealisticSurvivalPlugin.depend == ["arc_attribute_core"]
print(f"   depend={ARCRealisticSurvivalPlugin.depend}")

print("\n== 1) 三路因子同步：口渴 100 + 骨裂 0.75")
core = FakeAttrCore()
plugin = make_plugin(core)
steve = FakePlayer("Steve", "x1")
plugin.player_xuid_to_thirst["x1"] = 100   # 口渴 >80 → +20%
plugin.fracture_manager = FakeFracture(0.75)  # 骨裂 -25%
plugin._apply_thirst_movement_modifier(steve)
added = {(k, s): a for k, s, a, d in core.added}
assert added[("walk_speed", "ars:thirst")] == 0.2
assert added[("walk_speed", "ars:leg")] == -0.25
assert ("walk_speed", "ars:base") in set(core.removed), "基速倍率 1.0 → 应移除而非挂 0 因子"
assert steve.walk_speed == 0.10, "ARS 不应直写 walk_speed"
print(f"   added={sorted(added)} removed={sorted(set(core.removed))} ok")

print("\n== 2) 腿伤因子变化与痊愈移除")
plugin.fracture_manager = FakeFracture(0.5)  # 骨折 -50%
core.added.clear()
core.removed.clear()
plugin._apply_thirst_movement_modifier(steve)
added = {(k, s): a for k, s, a, d in core.added}
assert added[("walk_speed", "ars:leg")] == -0.5
core.added.clear()
core.removed.clear()
plugin.fracture_manager = FakeFracture(1.0)  # 痊愈
plugin._apply_thirst_movement_modifier(steve)
assert ("walk_speed", "ars:leg") in set(core.removed), "痊愈应移除 ars:leg 因子"
print("   因子更新与痊愈移除 ok")

print("\n== 3) clear（创造旁路）移除全部 ARS 因子")
plugin.player_xuid_to_thirst["x1"] = 50
plugin._clear_thirst_movement_modifier(steve)
removed_sources = {s for _, s in core.removed}
assert {"ars:base", "ars:thirst", "ars:leg"} <= removed_sources
print(f"   removed={sorted(removed_sources)} ok")

print("\n== 4) 基速倍率非 1.0 时挂 ars:base")
plugin.walk_speed_base_multiplier = 1.5
core.added.clear()
plugin._apply_thirst_movement_modifier(steve)
added = {(k, s): a for k, s, a, d in core.added}
assert added[("walk_speed", "ars:base")] == 0.5
print("   ars:base +0.5 ok")

print("\n== 5) 核心缺失：告警一次、不异常、不回退")
logs = []
plugin2 = make_plugin(core=None, logs=logs)
steve2 = FakePlayer("Steve2", "x2")
plugin2.player_xuid_to_thirst["x2"] = 100
plugin2._apply_thirst_movement_modifier(steve2)
plugin2._apply_thirst_movement_modifier(steve2)
errors = [m for lv, m in logs if lv == "error" and "arc_attribute_core" in m]
assert len(errors) == 1, f"应只告警一次: {errors}"
assert steve2.walk_speed == 0.10
print("   告警一次、无异常、无本地写入 ok")

print("\n== 6) 缓存：core 探测只查一次 plugin_manager")
lookups = []
core3 = FakeAttrCore()


class CountingPM(FakePluginManager):
    def get_plugin(self, name):
        lookups.append(name)
        return super().get_plugin(name)


plugin3 = make_plugin()
plugin3.server.plugin_manager = CountingPM(core3)
plugin3.player_xuid_to_thirst["x3"] = 100
steve3 = FakePlayer("Steve3", "x3")
plugin3._apply_thirst_movement_modifier(steve3)
plugin3._apply_thirst_movement_modifier(steve3)
assert len(lookups) == 1, f"应只查询一次，实际 {len(lookups)}"
print("   探测缓存 ok")

print("\n== 7) join/reset 入口都走同步")
core3.added.clear()
core3.removed.clear()
plugin3._apply_thirst_movement_modifier(steve3)  # 原 _reset_walk_speed_baseline 入口
assert core3.added or core3.removed
print("   同步入口统一 ok")

print("\nALL SPEED INTEGRATION SMOKE TESTS PASSED")
