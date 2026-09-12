# -*- coding: utf-8 -*-
"""离线冒烟测试：ConsumeEffectManager 统一进食效果（stub endstone）。"""
import tempfile
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
_endstone_stub.add_src_to_path()

from endstone_arc_realistic_survival.DatabaseManager import DatabaseManager
from endstone_arc_realistic_survival.ConsumeEffectManager import ConsumeEffectManager
from endstone_arc_realistic_survival.pack_effects import ARC_PACK_EFFECTS
import endstone_arc_realistic_survival.arc_realistic_survival as main_mod  # 验证主插件 import 无误

print("== 0) 主插件模块 import 成功")

tmp = tempfile.mkdtemp()
db = DatabaseManager(os.path.join(tmp, "test.db"))

# ---- 预置旧表（模拟老服务器升级）----
db.execute("CREATE TABLE thirst_items (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL UNIQUE, item_name TEXT, thirst_delta INTEGER NOT NULL DEFAULT 0, buffs TEXT, created_at TEXT, updated_at TEXT)")
db.execute("CREATE TABLE nutrition_items (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL UNIQUE, item_name TEXT, vitamin_a INTEGER NOT NULL DEFAULT 0, vitamin_c INTEGER NOT NULL DEFAULT 0, iron INTEGER NOT NULL DEFAULT 0, protein INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT)")
db.insert("thirst_items", {"item_id": "sgs_farm:rice_cooked", "item_name": "米饭", "thirst_delta": 40, "buffs": '[{"name":"speed","duration":30,"amplifier":1}]', "created_at": "x", "updated_at": "x"})
db.insert("thirst_items", {"item_id": "minecraft:bread", "item_name": "面包", "thirst_delta": -5, "buffs": None, "created_at": "x", "updated_at": "x"})
db.insert("nutrition_items", {"item_id": "minecraft:bread", "item_name": "面包", "vitamin_a": 9, "vitamin_c": 9, "iron": 9, "protein": 9, "created_at": "x", "updated_at": "x"})


# ---- fake plugin ----
class FakeZVM:
    def __init__(self):
        self.player_infection = {}
        self.enabled = True

    def apply_delta(self, player, delta, source_label=""):
        xuid = player["xuid"]
        old = float(self.player_infection.get(xuid, 0.0))
        new = max(0.0, min(100.0, old + float(delta)))
        self.player_infection[xuid] = new
        return new


class FakeNutri:
    def __init__(self):
        self.calls = []

    def apply_deltas(self, player, deltas, item_label=""):
        self.calls.append((player["name"], dict(deltas), item_label))
        return {k: 50 for k in deltas}


class FakeFracture:
    """对齐 FractureManager 新 API：止痛 + 腿伤处理（夹板）。"""

    def __init__(self):
        self.leg = {}  # xuid -> "crack" | "fracture"

    def apply_painkiller(self, player):
        x = player["xuid"]
        if x not in self.leg:
            return (False, "没有腿伤")
        if self.leg[x] == "fracture":
            return (False, "骨折需要夹板")
        return (True, "")

    def apply_leg_treatment(self, player):
        x = player["xuid"]
        if x not in self.leg:
            return ("none", "")
        if self.leg[x] == "fracture":
            self.leg[x] = "crack"
            return ("downgraded", "")
        self.leg.pop(x)
        return ("cured", "")


class FakePlugin:
    def __init__(self):
        self.player_xuid_to_thirst = {}
        self.thirst_initial = 100
        self.thirst_max = 100
        self.thirst_consume_debug = False
        self.nutrition_manager = FakeNutri()
        self.zombie_virus_manager = FakeZVM()
        self.fracture_manager = FakeFracture()

    def _log_consume_debug(self, msg):
        print("   [debug]", msg)

    def _apply_thirst_delta(self, player, delta, reason="", notify=True):
        xuid = player["xuid"]
        cur = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        new = max(0, min(self.thirst_max, cur + delta))
        self.player_xuid_to_thirst[xuid] = new
        return new

    def _sync_creative_snap_thirst(self, p, v):
        pass

    def _sync_creative_snap_nutrition(self, p, d):
        pass

    def _sync_creative_snap_infection(self, p, v):
        pass

    def _is_infection_enabled(self):
        return self.zombie_virus_manager.enabled

    def _get_player_xuid(self, player):
        return player["xuid"]

    def _collect_item_identity_strings(self, item):
        return list(item["ids"])


def log_fn(level, msg):
    print(f"[{level}] {msg}")


plugin = FakePlugin()
cm = ConsumeEffectManager(plugin, db, log_fn, plugin._get_player_xuid, plugin._collect_item_identity_strings)
cm.ensure_tables()
cm.load_items_config()

n_rows = db.query_one("SELECT COUNT(*) AS cnt FROM consume_items")["cnt"]
print(f"\n== 1) 首次加载：consume_items 行数 = {n_rows}（预期 >= 内置默认 {len(ARC_PACK_EFFECTS)} + 迁移 2）")
assert n_rows >= len(ARC_PACK_EFFECTS) + 2

rice = cm.find_cfg_by_id("sgs_farm:rice_cooked")
print(f"== 2) 旧表迁移: rice_cooked = {rice}")
assert rice and rice["thirst_delta"] == 40 and rice["buffs"] and rice["item_name"] == "米饭"
bread = cm.find_cfg_by_id("minecraft:bread")
print(f"== 2b) 旧表覆盖默认: bread = {bread}")
assert bread["thirst_delta"] == -5 and bread["vitamin_a"] == 9 and bread["protein"] == 9
water = cm.find_cfg_by_id("arc:bottled_water")
print(f"== 2c) 内置 arc 默认: water = {water}")
assert water["thirst_delta"] == 42 and water["show_toast"] == 1

# 幂等：重复加载行数不变；改表后 DB 优先
cm.load_items_config()
n_rows2 = db.query_one("SELECT COUNT(*) AS cnt FROM consume_items")["cnt"]
print(f"== 3) 重复加载幂等: rows={n_rows2}（应等于 {n_rows}）")
assert n_rows2 == n_rows
db.execute("UPDATE consume_items SET thirst_delta=99 WHERE item_id='arc:bottled_water'")
cm.load_items_config()
w2 = cm.find_cfg_by_id("ARC:BOTTLED_WATER")
print(f"== 3b) DB 值优先于内置默认: water.thirst={w2['thirst_delta']}（应为 99）")
assert w2["thirst_delta"] == 99
db.execute("UPDATE consume_items SET thirst_delta=42 WHERE item_id='arc:bottled_water'")

# ---- 应用链路 ----
print("\n== 4) 喝水（consume 路径，口渴满值钳制 + arc 防双触发）")
steve = {"name": "Steve", "xuid": "x1", "ids": ["arc:bottled_water", "BOTTLED_WATER"]}
ok = cm.on_player_consume(steve, {"ids": ["arc:bottled_water"]})
assert ok
thirst_now = plugin.player_xuid_to_thirst["x1"]
print(f"   口渴现在 = {thirst_now}（初始 100 + 42 应被钳到 100）")
assert thirst_now == 100
assert plugin.nutrition_manager.calls == []  # 水没营养
print("   2s 内重复吃（arc 防双触发）:")
ok2 = cm.on_player_consume(steve, {"ids": ["arc:bottled_water"]})
assert ok2  # dedup 也返回 True
print("   ok（dedup 生效，口渴未再变动）")

print("\n== 5) 吃面包（营养 + 负口渴，非 arc 不 dedup）")
steve2 = {"name": "Alex", "xuid": "x2", "ids": ["minecraft:bread"]}
plugin.player_xuid_to_thirst["x2"] = 50
ok = cm.on_player_consume(steve2, {"ids": ["minecraft:bread"]})
assert ok
assert plugin.player_xuid_to_thirst["x2"] == 45
call = plugin.nutrition_manager.calls[-1]
print(f"   nutrition deltas = {call[1]}, label = {call[2]}, thirst = {plugin.player_xuid_to_thirst['x2']}")
assert call[1] == {"vitamin_a": 9, "vitamin_c": 9, "iron": 9, "protein": 9}
cm.on_player_consume(steve2, {"ids": ["minecraft:bread"]})
assert plugin.player_xuid_to_thirst["x2"] == 40
print("   非 arc 物品连续吃不 dedup: ok")

print("\n== 6) /arseffect 命令路径 + 感染净化")
plugin.zombie_virus_manager.player_infection["x2"] = 30.0
status, label, bits = cm.apply_by_id(steve2, "arc:antiviral_weak")
print(f"   status={status} label={label} bits={bits} infection={plugin.zombie_virus_manager.player_infection['x2']}")
assert status == "applied" and bits == ["感染-15"]
assert plugin.zombie_virus_manager.player_infection["x2"] == 15.0
status2, _, _ = cm.apply_by_id(steve2, "arc:antiviral_weak")
assert status2 == "deduped", "命令路径 2s 去重应生效"
print("   命令路径 dedup: ok")
status3, _, _ = cm.apply_by_id(steve2, "arc:not_exist")
assert status3 == "unknown"
print("   未知物品: ok")

print("\n== 7) 感染关闭时跳过感染增量")
plugin.zombie_virus_manager.enabled = False
cm._arc_applied_at.clear()
steve3 = {"name": "Bob", "xuid": "x3", "ids": ["arc:purge_serum"]}
status, label, bits = cm.apply_by_id(steve3, "arc:purge_serum")
print(f"   status={status} bits={bits}")
assert bits == []
print("   ok")

print("\n== 8) 面板目录行")
lines = cm.get_catalog_lines(limit=5)
for line in lines:
    print("   " + line)
assert len(lines) == 6

print("\n== 9) 腿伤物品：夹板（骨折降级/骨裂痊愈）+ 止痛药")
db.execute(
    "INSERT OR REPLACE INTO consume_items (item_id, item_name, cure_fracture, show_toast, created_at, updated_at) "
    "VALUES ('arc:bone_splint', '夹板', 1, 1, 'x', 'x')"
)
db.execute(
    "INSERT OR REPLACE INTO consume_items (item_id, item_name, painkiller, show_toast, created_at, updated_at) "
    "VALUES ('arc:painkiller_test', '止痛药', 1, 1, 'x', 'x')"
)
cm.load_items_config()
fm = plugin.fracture_manager
fm.leg["x2"] = "fracture"  # Alex 骨折
splint = cm.find_cfg_by_id("arc:bone_splint")
assert splint and splint["cure_fracture"] == 1
status, label, bits = cm.apply_by_id(steve2, "arc:bone_splint")
print(f"   骨折+夹板: status={status} bits={bits}")
assert status == "applied" and "骨折已固定为骨裂" in bits
assert fm.leg["x2"] == "crack", "夹板应把骨折降级为骨裂"
cm._arc_applied_at.clear()  # 清 2s 防重（测试内连续吃同一物品）
status, label, bits = cm.apply_by_id(steve2, "arc:bone_splint")
assert status == "applied" and "腿伤已治疗" in bits
assert "x2" not in fm.leg, "第二次使用应治愈骨裂"
# 止痛药
pk_item = cm.find_cfg_by_id("arc:painkiller_test")
assert pk_item and pk_item["painkiller"] == 1
status, label, bits = cm.apply_by_id(steve2, "arc:painkiller_test")
assert "止痛生效" not in bits, "无腿伤不应有止痛反馈"
fm.leg["x2"] = "crack"
cm._arc_applied_at.clear()
status, label, bits = cm.apply_by_id(steve2, "arc:painkiller_test")
assert status == "applied" and "止痛生效" in bits, bits
fm.leg["x2"] = "fracture"
cm._arc_applied_at.clear()
status, label, bits = cm.apply_by_id(steve2, "arc:painkiller_test")
assert "止痛生效" not in bits, "骨折用止痛药应无效"
print("   夹板降级/痊愈 + 止痛生效/无效边界 ok")

print("\n== 10) 内置默认目录含止痛药与夹板")
pain_default = cm.find_cfg_by_id("arc:painkiller")
splint_default = cm.find_cfg_by_id("arc:splint")
assert pain_default and pain_default["painkiller"] == 1
assert splint_default and splint_default["cure_fracture"] == 1
print(f"   arc:painkiller / arc:splint 已入目录 ok")

print("\nALL SMOKE TESTS PASSED")
