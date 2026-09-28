# -*- coding: utf-8 -*-
"""脱水倒计时离线时间冒烟测试：离线时长不得计入脱水致死倒计时（只计在线时长）。

背景：dehydrated_since 是墙上时钟，退出时落库、上线原样读回，
elapsed = now - started 会把整个离线时长算进去——口渴 0 的玩家
离线满 1 小时后上线即被秒杀。修复：加载时按 updated_at 顺延起点。
"""
import datetime
import os
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
_endstone_stub.add_src_to_path()

from endstone_arc_realistic_survival.arc_realistic_survival import ARCRealisticSurvivalPlugin

print("== 0) 主插件 import 成功")

tmp = tempfile.mkdtemp()
old_cwd = os.getcwd()
os.chdir(tmp)
os.makedirs(os.path.join(tmp, "plugins"), exist_ok=True)

MINUTE = 60.0
HOUR = 3600.0


class FakePlayer:
    def __init__(self, name, xuid):
        self.name = name
        self.xuid = xuid

    def send_toast(self, *a):
        pass

    def send_message(self, *a):
        pass


try:
    plugin = ARCRealisticSurvivalPlugin()
    plugin.data_folder = os.path.join(tmp, "data")
    os.makedirs(plugin.data_folder, exist_ok=True)
    plugin.on_load()
    db = plugin.db_manager

    killed: list[str] = []
    plugin._kill_from_dehydration = lambda p: killed.append(p.name)

    def seed(xuid, name, thirst, since, offline_seconds):
        """写入一条「下线时刻=now-offline_seconds」的落库行。"""
        saved_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=offline_seconds)
        db.upsert("player_thirst", {
            "xuid": xuid,
            "player_name": name,
            "thirst": thirst,
            "dehydrated_since": since,
            "updated_at": saved_at.isoformat(),
        })

    print("\n== 1) 口渴 0 + 下线前已脱水 10 分钟 + 离线 3 小时 → 不应击杀，且倒计时只含在线 10 分钟")
    steve = FakePlayer("Steve", "x1")
    now = time.time()
    seed("x1", "Steve", 0, now - 3 * HOUR - 10 * MINUTE, 3 * HOUR)
    plugin._load_player_thirst(steve)
    plugin._sync_dehydration_state(steve)
    assert killed == [], f"离线不应计入倒计时，却被击杀: {killed}"
    loaded = plugin.player_xuid_to_dehydrated_since["x1"]
    elapsed = time.time() - loaded
    assert 9 * MINUTE <= elapsed <= 11 * MINUTE + 5, f"在线脱水时长应约 10 分钟，实际 {elapsed / MINUTE:.1f} 分钟"
    print(f"   未击杀；在线脱水累计 {elapsed / MINUTE:.1f} 分钟 ok")

    print("\n== 2) 口渴 0 + 下线前已脱水 70 分钟 + 离线 3 小时 → 在线时长已满 1 小时，上线即击杀")
    alex = FakePlayer("Alex", "x2")
    now = time.time()
    seed("x2", "Alex", 0, now - 3 * HOUR - 70 * MINUTE, 3 * HOUR)
    plugin._load_player_thirst(alex)
    plugin._sync_dehydration_state(alex)
    assert killed == ["Alex"], f"在线脱水已满 1 小时应击杀: {killed}"
    print("   上线即触发脱水死亡 ok（在线 70 分钟 > 60 分钟阈值）")

    print("\n== 3) updated_at 缺失/无法解析 → 不崩溃、起点原样保留")
    bob = FakePlayer("Bob", "x3")
    now = time.time()
    db.upsert("player_thirst", {
        "xuid": "x3", "player_name": "Bob", "thirst": 0,
        "dehydrated_since": now - 10 * MINUTE, "updated_at": "not-a-date",
    })
    plugin._load_player_thirst(bob)
    plugin._sync_dehydration_state(bob)
    loaded3 = plugin.player_xuid_to_dehydrated_since["x3"]
    assert abs(loaded3 - (now - 10 * MINUTE)) < 5, "解析失败应原样保留起点"
    assert killed == ["Alex"], "不应误杀"
    print("   起点 unmodified ok")

    print("\n== 4) 离线时长为负（时钟偏差）→ 不顺延")
    cara = FakePlayer("Cara", "x4")
    now = time.time()
    db.upsert("player_thirst", {
        "xuid": "x4", "player_name": "Cara", "thirst": 0,
        "dehydrated_since": now - 5 * MINUTE,
        "updated_at": (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat(),
    })
    plugin._load_player_thirst(cara)
    loaded4 = plugin.player_xuid_to_dehydrated_since["x4"]
    assert abs(loaded4 - (now - 5 * MINUTE)) < 5, "负离线时长不应顺延"
    print("   ok")

    print("\n== 5) 口渴 >0 时起点不做离线顺延（本就应被清空/无效）")
    dave = FakePlayer("Dave", "x5")
    now = time.time()
    db.upsert("player_thirst", {
        "xuid": "x5", "player_name": "Dave", "thirst": 50,
        "dehydrated_since": now - 2 * HOUR,
        "updated_at": (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).isoformat(),
    })
    plugin._load_player_thirst(dave)
    assert plugin.player_xuid_to_dehydrated_since["x5"] == now - 2 * HOUR
    print("   ok")

    print("\n== 6) 新玩家：无行 → 初始口渴、无脱水计时")
    erin = FakePlayer("Erin", "x6")
    plugin._load_player_thirst(erin)
    assert plugin.player_xuid_to_thirst["x6"] == plugin.thirst_initial
    assert plugin.player_xuid_to_dehydrated_since["x6"] is None
    print("   ok")

    plugin.on_disable()
finally:
    os.chdir(old_cwd)

print("\nALL DEHYDRATION-OFFLINE SMOKE TESTS PASSED")
