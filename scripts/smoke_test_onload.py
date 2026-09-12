# -*- coding: utf-8 -*-
"""on_load 全流程冒烟测试：真实调用插件的 on_load，捕获管理器构造签名漂移等启动期错误。

背景：v0.4.0 曾因 NutritionManager 构造调用漏改（类删参但调用点没同步）
在服务器启动时崩溃，而此前的冒烟测试用 object.__new__ 绕过了 on_load。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub  # noqa: F401  安装 endstone stub
_endstone_stub.add_src_to_path()

from endstone_arc_realistic_survival.arc_realistic_survival import ARCRealisticSurvivalPlugin

print("== 0) 主插件模块 import 成功")

tmp = tempfile.mkdtemp()
old_cwd = os.getcwd()
os.chdir(tmp)  # on_load/SettingManager 用 CWD 相对路径（plugins/ARCRealisticSurvival/...）
os.makedirs(os.path.join(tmp, "plugins"), exist_ok=True)  # 真实服务器上该目录必然存在
try:
    plugin = ARCRealisticSurvivalPlugin()  # 真实 __init__（轻量默认值）
    plugin.data_folder = os.path.join(tmp, "data")
    os.makedirs(plugin.data_folder, exist_ok=True)
    plugin.on_load()

    print("== 1) on_load 执行成功，管理器全部就绪")
    assert plugin.db_manager is not None
    assert plugin.setting_manager is not None
    assert plugin.language_manager is not None
    assert plugin.nutrition_manager is not None, "NutritionManager 构造失败"
    assert plugin.zombie_virus_manager is not None
    assert plugin.consume_manager is not None, "ConsumeEffectManager 构造失败"
    assert plugin.fracture_manager is not None, "FractureManager 构造失败"

    print("== 2) 统一进食配置已加载（内置默认 + 迁移）")
    n_rows = plugin.db_manager.query_one("SELECT COUNT(*) AS cnt FROM consume_items")["cnt"]
    assert n_rows > 0, f"consume_items 应有默认行，实际 {n_rows}"
    water = plugin.consume_manager.find_cfg_by_id("arc:bottled_water")
    assert water is not None and water["thirst_delta"] == 42
    splint = plugin.consume_manager.find_cfg_by_id("arc:splint")
    assert splint is not None and splint["cure_fracture"] == 1
    painkiller = plugin.consume_manager.find_cfg_by_id("arc:painkiller")
    assert painkiller is not None and painkiller["painkiller"] == 1
    print(f"   consume_items rows={n_rows}，arc 物品/夹板/止痛药就位")

    print("== 3) 骨裂管理器配置就绪")
    assert plugin.fracture_manager.crack_duration(6) > 0
    assert plugin.fracture_manager.severe_speed_multiplier == 0.5
    print(f"   crack_duration(6)={plugin.fracture_manager.crack_duration(6):.0f}s ok")

    print("== 4) on_disable 保存流程无异常")
    plugin.on_disable()
finally:
    os.chdir(old_cwd)

print("\nALL ONLOAD SMOKE TESTS PASSED")
