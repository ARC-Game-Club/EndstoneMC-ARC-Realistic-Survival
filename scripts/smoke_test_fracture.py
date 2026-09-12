# -*- coding: utf-8 -*-
"""离线冒烟测试：FractureManager 骨裂/骨折双档系统（stub endstone）。"""
import sys
import time
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
from _endstone_stub import GameMode
_endstone_stub.add_src_to_path()

from endstone_arc_realistic_survival.DatabaseManager import DatabaseManager
from endstone_arc_realistic_survival.FractureManager import FractureManager


class FakeSettings:
    def __init__(self):
        self.store = {}

    def GetSetting(self, key):
        return self.store.get(key)

    def SetSetting(self, key, value):
        self.store[key] = value


class FakePlugin:
    def __init__(self):
        self.speed_recompute_calls = []

    def _log(self, level, msg):
        print(f"[{level}] {msg}")

    def _get_player_xuid(self, player):
        return player.xuid

    def _apply_thirst_movement_modifier(self, player):
        self.speed_recompute_calls.append(player.name)


class FakeSource:
    """对齐 Endstone DamageSource：type 为字符串属性。"""

    def __init__(self, damage_type):
        self.type = damage_type


class FakeEvent:
    def __init__(self, actor, damage, damage_type):
        self.actor = actor
        self.damage = damage
        self.damage_source = FakeSource(damage_type)


class FakePlayer:
    def __init__(self, name, xuid, game_mode=GameMode.SURVIVAL, health=20):
        self.name = name
        self.xuid = xuid
        self.game_mode = game_mode
        self.health = health
        self.toasts = []

    def send_toast(self, title, body):
        self.toasts.append((title, body))


tmp = tempfile.mkdtemp()
db = DatabaseManager(os.path.join(tmp, "test.db"))
settings = FakeSettings()
plugin = FakePlugin()

fm = FractureManager(plugin, db, settings, plugin._log, plugin._get_player_xuid)
fm.ensure_tables()
fm.load_settings()

print("\n== 1) 默认配置写入并加载")
assert fm.enabled is True
assert fm.fall_damage_min == 5.0
assert fm.severe_speed_multiplier == 0.5
assert settings.store["fracture_enabled"] == "true"
assert settings.store["fracture_severe_speed_multiplier"] == "0.5"
print(f"   骨裂 speed={fm.speed_multiplier} 骨折 speed={fm.severe_speed_multiplier} heal={fm.heal_seconds}s")

print("\n== 2) 概率公式")
assert fm.resolve_chance_percent(4) == 10.0, "公式从基础概率起算，低于阈值的过滤在 on_actor_damage"
assert fm.resolve_chance_percent(5.5) == 12.5
assert fm.resolve_chance_percent(6) == 15.0
assert fm.resolve_chance_percent(20) == 80.0, "应被封顶 80"
print("   5.5→12.5%, 6→15%, 20→80%（封顶） ok")

print("\n== 3) 非触发情况")
alice = FakePlayer("Alice", "x1", health=20)
plugin.speed_recompute_calls.clear()
fm.on_actor_damage(FakeEvent(alice, 4, "fall"))            # 伤害不足
fm.on_actor_damage(FakeEvent(alice, 10, "lava"))           # 非坠落
fm.on_actor_damage(FakeEvent(alice, 10, "void"))           # 虚空
fm.on_actor_damage(FakeEvent(alice, 10, "falling_block"))  # 落方块砸中，不算坠落
assert fm.fractured == {}
assert plugin.speed_recompute_calls == []
bob = FakePlayer("Bob", "x2", game_mode=GameMode.CREATIVE, health=20)
fm.on_actor_damage(FakeEvent(bob, 10, "fall"))             # 创造模式
assert fm.fractured == {}
print("   伤害不足/非坠落/创造模式 均不触发 ok")

print("\n== 4) 骨裂触发（概率拉满确保必中，伤害 10 < 血量 20 非致死）")
# 注意同时放开上限封顶，否则 80% 概率会导致测试偶发失败
fm.chance_base_percent = 100.0
fm.chance_max_percent = 100.0
plugin.speed_recompute_calls.clear()
fm.on_actor_damage(FakeEvent(alice, 10, "fall"))
assert fm.get_level(alice) == FractureManager.LEVEL_CRACK
assert fm.speed_factor_for(alice) == 0.75
assert plugin.speed_recompute_calls == ["Alice"], "骨裂后应立即重算移速"
row = db.query_one("SELECT * FROM player_fracture WHERE xuid='x1'")
assert row is not None and int(row["level"]) == 1 and row["heal_at"] > time.time()
assert alice.toasts[-1][0] == "骨裂"
print(f"   level=1 落库+toast={alice.toasts[-1][0]} ok")
fm.on_actor_damage(FakeEvent(alice, 15, "fall"))           # 非致死重复坠落
assert fm.get_level(alice) == 1, "已骨裂不重复触发"
print("   已骨裂重复坠落不刷新 ok")

print("== 4b) 旧版 API（source 无 type、只有 cause 枚举）回退兼容")


class LegacyCause:
    name = "Fall"


class LegacySource:
    cause = LegacyCause()


class LegacyEvent:
    def __init__(self, actor, damage):
        self.actor = actor
        self.damage = damage
        self.damage_source = LegacySource()


frank = FakePlayer("Frank", "x6", health=20)
fm.on_actor_damage(LegacyEvent(frank, 10))
assert fm.get_level(frank) == 1, "cause 枚举回退应可触发骨裂"
fm.fractured.pop("x6")
db.delete("player_fracture", "xuid=?", ("x6",))
print("   cause 枚举回退 ok")

print("\n== 5) 骨裂每秒掉血（仅移动时，且不致死）")
fm.tick_second(alice, moved=False)
assert alice.health == 20, "未移动不掉血"
fm.tick_second(alice, moved=True)
assert alice.health == 19, f"移动应掉血到 19，实际 {alice.health}"
alice.health = 2
fm.tick_second(alice, moved=True)
assert alice.health == 1, "应保留 1 点不致死"
fm.tick_second(alice, moved=True)
assert alice.health == 1, "已到下限不再掉血"
print("   移动掉血 1/s、静止不掉、保底 1 血 ok")

print("\n== 6) 骨裂自动痊愈")
fm.fractured["x1"]["heal_at"] = time.time() - 1  # 模拟时间已到
plugin.speed_recompute_calls.clear()
fm.tick_second(alice, moved=True)
assert "x1" not in fm.fractured
assert db.query_one("SELECT * FROM player_fracture WHERE xuid='x1'") is None
assert plugin.speed_recompute_calls == ["Alice"], "痊愈后应重算移速"
assert alice.toasts[-1][0] == "伤势痊愈"
assert fm.speed_factor_for(alice) == 1.0
print(f"   痊愈 toast={alice.toasts[-1][0]} ok")

print("\n== 7) 骨裂持久化与重进恢复（掉线跨过痊愈时间则清除）")
carol = FakePlayer("Carol", "x3", health=20)
fm.on_actor_damage(FakeEvent(carol, 15, "fall"))
fm.on_player_quit(carol)
assert "x3" not in fm.fractured, "退出应清内存"
db.execute("UPDATE player_fracture SET heal_at=? WHERE xuid='x3'", (time.time() - 5,))
fm2 = FractureManager(plugin, db, settings, plugin._log, plugin._get_player_xuid)
fm2.ensure_tables()
fm2.load_settings()
dave = FakePlayer("Dave", "x3")  # 同 xuid 模拟重进
fm2.load_player(dave)
assert "x3" not in fm2.fractured, "离线期间骨裂应已痊愈"
print("   退出落库、离线痊愈自动清除 ok")

print("\n== 8) 骨折触发：坠落伤害直接致死（damage >= 现有生命）")
erin = FakePlayer("Erin", "x4", health=20)
fm2.on_actor_damage(FakeEvent(erin, 20, "fall"))
assert fm2.get_level(erin) == FractureManager.LEVEL_BREAK, "致死坠落应进入骨折"
assert fm2.speed_factor_for(erin) == 0.5
row = db.query_one("SELECT * FROM player_fracture WHERE xuid='x4'")
assert int(row["level"]) == 2 and row["heal_at"] == 0.0, "骨折不自动痊愈"
assert erin.toasts[-1][0] == "骨折！"
print(f"   level=2 落库+toast={erin.toasts[-1][0]}，heal_at=0 ok")
fm2.on_actor_damage(FakeEvent(erin, 25, "fall"))
assert fm2.get_level(erin) == 2, "已骨折不重复触发"

print("\n== 9) 骨折不掉血、不自动痊愈")
health_before = erin.health
fm2.tick_second(erin, moved=True)
assert erin.health == health_before, "骨折移动不掉血"
fm2.tick_second(erin, moved=False)
assert erin.health == health_before
assert fm2.get_level(erin) == 2, "骨折不自动痊愈"
print("   骨折移动不掉血、无自动痊愈 ok")

print("\n== 10) 死亡：骨裂清除、骨折保留")
# 先造一个骨裂玩家（fm2 是新实例，同样放开概率确保必中）
fm2.chance_base_percent = 100.0
fm2.chance_max_percent = 100.0
grace = FakePlayer("Grace", "x5", health=20)
fm2.on_actor_damage(FakeEvent(grace, 10, "fall"))
assert fm2.get_level(grace) == 1
fm2.reset_on_death(grace)
assert fm2.get_level(grace) == 0, "死亡应清除骨裂"
assert db.query_one("SELECT * FROM player_fracture WHERE xuid='x5'") is None
# 骨折死亡保留
fm2.reset_on_death(erin)
assert fm2.get_level(erin) == 2, "死亡不应清除骨折"
assert db.query_one("SELECT * FROM player_fracture WHERE xuid='x4'") is not None
print("   骨裂死亡清除、骨折死亡保留 ok")

print("\n== 11) 骨裂玩家摔死 → 升级为骨折")
henry = FakePlayer("Henry", "x7", health=10)
fm2.on_actor_damage(FakeEvent(henry, 6, "fall"))   # 非致死 → 骨裂
assert fm2.get_level(henry) == 1
fm2.on_actor_damage(FakeEvent(henry, 10, "fall"))  # 致死 → 骨折
assert fm2.get_level(henry) == 2, "致死坠落应把骨裂升级为骨折"
row = db.query_one("SELECT * FROM player_fracture WHERE xuid='x7'")
assert int(row["level"]) == 2
fm2.reset_on_death(henry)
assert fm2.get_level(henry) == 2, "升级后的骨折死亡仍保留"
print("   骨裂→骨折升级 ok")

print("\n== 12) 重生提醒与治疗清除")
toasts_before = len(erin.toasts)
fm2.on_player_respawn(erin)
assert len(erin.toasts) == toasts_before + 1 and erin.toasts[-1][0] == "骨折未愈"
assert fm2.clear_fracture(erin, notify=True) is True
assert fm2.get_level(erin) == 0
assert db.query_one("SELECT * FROM player_fracture WHERE xuid='x4'") is None
assert erin.toasts[-1][0] == "伤势痊愈"
print("   重生骨折提醒 + clear_fracture 治疗 ok")

print("\n== 13) 骨折持久化重进恢复（heal_at=0 不误清）")
ivy = FakePlayer("Ivy", "x8", health=20)
fm2.on_actor_damage(FakeEvent(ivy, 30, "fall"))
assert fm2.get_level(ivy) == 2
fm2.on_player_quit(ivy)
fm3 = FractureManager(plugin, db, settings, plugin._log, plugin._get_player_xuid)
fm3.ensure_tables()
fm3.load_settings()
kate = FakePlayer("Kate", "x8")
fm3.load_player(kate)
assert fm3.get_level(kate) == 2, "骨折跨会话保留"
print("   骨折重进恢复 ok")

print("\n== 14) 骨裂时长随坠落伤害增长（300 + 30×超出量，封顶 900）")
t15a = FakePlayer("T15a", "t15a", health=20)
fm2.on_actor_damage(FakeEvent(t15a, 6, "fall"))
heal_at = fm2.fractured["t15a"]["heal_at"]
assert abs(heal_at - time.time() - 330) < 3, heal_at - time.time()
fm2.clear_fracture(t15a)
t15b = FakePlayer("T15b", "t15b", health=20)
fm2.on_actor_damage(FakeEvent(t15b, 15, "fall"))
assert abs(fm2.fractured["t15b"]["heal_at"] - time.time() - 600) < 3
fm2.clear_fracture(t15b)
t15c = FakePlayer("T15c", "t15c", health=200)  # 高血量让伤害 100 仍非致死
fm2.on_actor_damage(FakeEvent(t15c, 100, "fall"))
assert abs(fm2.fractured["t15c"]["heal_at"] - time.time() - 900) < 3, "应封顶 900"
print("   6→330s / 15→600s / 超高→封顶900s ok")

print("\n== 15) 止痛药：骨裂不减速、掉血保留、骨折无效")
pk = FakePlayer("Pk", "t16", health=20)
fm2.on_actor_damage(FakeEvent(pk, 10, "fall"))  # 骨裂
assert fm2.speed_factor_for(pk) == 0.75
ok, msg = fm2.apply_painkiller(pk)
assert ok
assert fm2.speed_factor_for(pk) == 1.0, "止痛后骨裂不应减速"
hp_before = pk.health
fm2.tick_second(pk, moved=True)
assert pk.health == hp_before - 1, "止痛后移动仍应掉血"
# 持久化
fm2.on_player_quit(pk)
fm4 = FractureManager(plugin, db, settings, plugin._log, plugin._get_player_xuid)
fm4.ensure_tables()
fm4.load_settings()
pk2 = FakePlayer("Pk2", "t16")
fm4.load_player(pk2)
assert fm4.speed_factor_for(pk2) == 1.0, "止痛状态应跨会话保留"
# 骨折用止痛药 → 无效
broken = FakePlayer("Broken", "t16b", health=20)
fm4.on_actor_damage(FakeEvent(broken, 20, "fall"))  # 致死 → 骨折
ok, msg = fm4.apply_painkiller(broken)
assert not ok and "夹板" in msg, msg
ok, msg = fm4.apply_painkiller(FakePlayer("Healthy", "t16c"))
assert not ok and "没有腿伤" in msg, msg
print("   骨裂止痛不减速/掉血保留/跨会话/骨折拒绝 ok")

print("\n== 16) 夹板：骨折降级为骨裂（最高档时长）、骨裂直接痊愈")
spl = FakePlayer("Spl", "t17", health=20)
fm4.on_actor_damage(FakeEvent(spl, 25, "fall"))  # 骨折
assert fm4.get_level(spl) == 2 and fm4.speed_factor_for(spl) == 0.5
result, msg = fm4.apply_leg_treatment(spl)
assert result == "downgraded", result
assert fm4.get_level(spl) == 1
assert abs(fm4.fractured["t17"]["heal_at"] - time.time() - 900) < 3, "降级后应为最高档骨裂时长"
assert fm4.speed_factor_for(spl) == 0.75, "降级后恢复骨裂减速"
assert int(fm4.fractured["t17"]["no_slow"]) == 0
result, msg = fm4.apply_leg_treatment(spl)  # 再用一次：骨裂直接痊愈
assert result == "cured" and fm4.get_level(spl) == 0
result, msg = fm4.apply_leg_treatment(spl)
assert result == "none"
print("   骨折降级(900s)/骨裂痊愈/无伤 ok")

print("\n== 17) 关闭开关后不触发不掉血")
fm3.fractured.clear()
fm3.enabled = False
leo = FakePlayer("Leo", "x9", health=10)
fm3.on_actor_damage(FakeEvent(leo, 10, "fall"))  # 致死也不触发
assert fm3.fractured == {}
assert fm3.speed_factor_for(leo) == 1.0
leo2 = FakePlayer("Leo", "x10", health=10)
fm3.fractured["x10"] = {"level": 2, "fractured_at": time.time(), "heal_at": 0.0}
fm3.tick_second(leo2, moved=True)
assert leo2.health == 10, "关闭后不掉血"
print("   关闭开关 ok")

print("\nALL FRACTURE SMOKE TESTS PASSED")
