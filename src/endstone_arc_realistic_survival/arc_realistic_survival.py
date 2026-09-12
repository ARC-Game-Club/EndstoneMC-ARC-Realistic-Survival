import datetime
import os
import json
import math
import time

from endstone import GameMode
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerItemConsumeEvent, PlayerMoveEvent, PlayerJoinEvent, PlayerQuitEvent, ActorDamageEvent, PlayerDeathEvent, PlayerRespawnEvent, PlayerGameModeChangeEvent
from endstone.plugin import Plugin
from endstone.form import ActionForm, ModalForm, Label, TextInput

from .ConsumeEffectManager import ConsumeEffectManager
from .DatabaseManager import DatabaseManager
from .FractureManager import FractureManager
from .LanguageManager import LanguageManager
from .NutritionManager import NUTRIENT_KEYS, NutritionManager
from .SettingManager import SettingManager
from .ZombieVirusManager import ZombieVirusManager
from .effect_compat import apply_mob_effect, resolve_effect_type
from .pack_effects import normalize_item_id


class ARCRealisticSurvivalPlugin(Plugin):
    prefix = "ARCRealisticSurvivalPlugin"
    api_version = "0.10"
    load = "POSTWORLD"
    # 硬依赖：玩家属性/buff 管理器，负责移速等属性的统一因子管理
    depend = ["arc_attribute_core"]

    commands = {
        "ars": {
            "description": "打开真实生存个人状态；OP 可从中进入配置管理。",
            "usages": [
                "/ars",
                "/ars nutrition",
                "/ars infection",
                "/ars reload",
                "/ars infectset <player: player> <value: float>",
                "/ars nutriset <player: player> <nutrient: str> <value: int>",
            ],
            "permissions": ["arc_realistic_survival.command.common"],
        },
        "heal": {
            "description": "治愈缺素病症并将营养设为 80，同时治疗骨裂/骨折（不影响丧尸感染；仅 OP/控制台；物品模组不用）",
            "usages": ["/heal <player: player>"],
            "permissions": ["arc_realistic_survival.command.admin"],
        },
        "purify": {
            "description": "净化玩家感染值；物品模组可对自身调用，改他人需 OP",
            "usages": ["/purify <player: player> <amount: float>"],
            "permissions": ["arc_realistic_survival.command.item"],
        },
        "thirstadd": {
            "description": "增减口渴值；物品模组对接（自身可用，改他人需 OP）",
            "usages": ["/thirstadd <player: player> <delta: float>"],
            "permissions": ["arc_realistic_survival.command.item"],
        },
        "nutriadd": {
            "description": "增减营养素；nutrient=vitamin_a|vitamin_c|iron|protein|all",
            "usages": ["/nutriadd <player: player> <nutrient: str> <delta: int>"],
            "permissions": ["arc_realistic_survival.command.item"],
        },
        "arseffect": {
            "description": "按统一进食效果配置（consume_items 表）施加口渴/营养/感染；管理测试入口",
            "usages": ["/arseffect <player: player> <item_id: str>"],
            "permissions": ["arc_realistic_survival.command.item"],
        },
    }

    permissions = {
        "arc_realistic_survival.command.common": {
            "description": "全员：/ars 个人状态、/ars nutrition、/ars infection（对齐弧光核心 common）",
            "default": True,
        },
        "arc_realistic_survival.command.config": {
            "description": "OP：配置面板、reload、nutriset、infectset（主面板内仅 OP 显示配置管理）",
            "default": "op",
        },
        "arc_realistic_survival.command.admin": {
            "description": "允许使用 /heal 及对任意玩家使用物品对接指令（OP/控制台）",
            "default": "op",
        },
        "arc_realistic_survival.command.item": {
            "description": "允许对自身使用 /thirstadd、/nutriadd、/purify、/arseffect（物品模组）",
            "default": True,
        },
    }

    def __init__(self):
        super().__init__()
        self.CHUNK_SIZE = 16  # 仍用于内部区块计算（如后续扩展）
        # 生存-口渴系统相关
        self.player_xuid_to_thirst = {}
        self.thirst_tick_seconds = 10
        self.thirst_decay_per_tick = 1
        self.thirst_moving_multiplier = 2.0
        self.thirst_initial = 100
        self.thirst_min = 0
        self.thirst_max = 100
        self.thirst_consume_debug = False
        self.thirst_task = None
        # 统一 20-tick 定时器：口渴/感染/营养共用
        self._last_thirst_run = 0.0
        self._last_infection_run = 0.0
        self._last_nutrition_run = 0.0
        # 口渴移速分段：>80 +20%；[30,80] 无加成；<30 -20%；<15 -50%
        self.thirst_speed_bonus_threshold = 80
        self.thirst_speed_bonus = 0.20
        self.thirst_speed_slow_threshold = 30
        self.thirst_speed_slow = -0.20
        self.thirst_speed_severe_threshold = 15
        self.thirst_speed_severe = -0.50
        self.thirst_fatal_seconds = 3600
        self.walk_speed_base_multiplier = 1.0
        self.player_xuid_to_dehydrated_since = {}
        # 统一进食效果配置（consume_items 表：口渴/营养/感染/buffs 一行配齐）
        self.consume_manager = None
        # 骨裂系统（坠落重摔概率触发：减速 + 移动掉血）
        self.fracture_manager = None
        # 玩家属性/buff 管理器（arc_attribute_core）缓存：安装后移速因子交由其统一连乘管理
        self._attr_core_cache = None
        # 创造/旁观时冻结真实生存数值；切回生存时恢复
        self._creative_snapshots = {}
        self.nutrition_manager = None
        self.zombie_virus_manager = None
        self.economy_plugin = None
        self._sidebar_page_id = "ars_health"
        self._sidebar_registered = False

    def _safe_log(self, level: str, message: str):
        """
        安全的日志记录方法，在logger未初始化时使用print
        :param level: 日志级别 (info, warning, error)
        :param message: 日志消息
        """
        if hasattr(self, 'logger') and self.logger is not None:
            if level.lower() == 'info':
                self.logger.info(message)
            elif level.lower() == 'warning':
                self.logger.warning(message)
            elif level.lower() == 'error':
                self.logger.error(message)
            else:
                self.logger.info(message)
        else:
            # 如果logger未初始化，使用print
            print(f"[{level.upper()}] {message}")

    def _log_consume_debug(self, message: str) -> None:
        """进食/饮水诊断日志：仅 thirst_consume_debug=true 时输出。"""
        if not self.thirst_consume_debug:
            return
        self._safe_log('info', message)
        try:
            print(f"[ARCRealisticSurvival][consume] {message}", flush=True)
        except Exception:
            pass

    def _resolve_survival_db_path(self) -> str:
        """与 settings.yml 同目录优先：plugins/ARCRealisticSurvival/ars_survival.db；兼容旧路径 ARCRealisticSurvival/。"""
        preferred = os.path.join("plugins", "ARCRealisticSurvival", "ars_survival.db")
        legacy = os.path.join("ARCRealisticSurvival", "ars_survival.db")
        if os.path.isfile(preferred):
            return preferred
        if os.path.isfile(legacy):
            return legacy
        return preferred

    def on_load(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_load is called!")
        
        # 初始化语言管理器
        self.language_manager = LanguageManager("CN")
        
        # 初始化设置管理器
        self.setting_manager = SettingManager()
        
        # 初始化默认配置
        self._init_default_settings()
        
        # 初始化数据库管理器（仅生存相关）
        db_path = self._resolve_survival_db_path()
        self.db_manager = DatabaseManager(db_path)
        self._safe_log(
            'info',
            f"[ARCRealisticSurvival] ars_survival.db -> {os.path.abspath(db_path)}",
        )
        
        # 创建表（仅生存相关）
        self._create_survival_tables()
        # 初始化营养学管理器
        self.nutrition_manager = NutritionManager(
            self,
            self.db_manager,
            self.setting_manager,
            self._safe_log,
            self._get_player_xuid,
            self._collect_item_identity_strings,
        )
        self.nutrition_manager.ensure_tables()
        self.nutrition_manager.load_settings()
        # 初始化丧尸病毒管理器
        self.zombie_virus_manager = ZombieVirusManager(
            self,
            self.db_manager,
            self.setting_manager,
            self._safe_log,
            self._get_player_xuid,
        )
        self.zombie_virus_manager.ensure_tables()
        self.zombie_virus_manager.load_settings()
        self.zombie_virus_manager.load_sources_config()
        # 初始化统一进食效果管理器（口渴/营养/感染单表配置）
        self.consume_manager = ConsumeEffectManager(
            self,
            self.db_manager,
            self._safe_log,
            self._get_player_xuid,
            self._collect_item_identity_strings,
        )
        self.consume_manager.ensure_tables()
        self.consume_manager.load_items_config()
        # 初始化骨裂管理器（坠落重摔概率触发）
        self.fracture_manager = FractureManager(
            self,
            self.db_manager,
            self.setting_manager,
            self._safe_log,
            self._get_player_xuid,
        )
        self.fracture_manager.ensure_tables()
        self.fracture_manager.load_settings()
        # 加载生存-口渴系统配置
        self._load_thirst_settings()

    def on_enable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_enable is called!")
        self.register_events(self)
        self._safe_log(
            'info',
            "[ARCRealisticSurvival] 事件已注册（含 PlayerItemConsumeEvent）；"
            "进食诊断日志默认关闭，设 thirst_consume_debug=true 后开启",
        )

        # 初始化经济插件 - 检查 arc_core 优先，然后 umoney
        self._init_economy_plugin()
        # 向弧光核心注册真实生存侧边栏页面
        self._register_sidebar_page()
        # 统一 20-tick 定时器（口渴/感染/营养）
        self._start_survival_timer()
        # 热重载时给已在线玩家补推侧边栏
        try:
            for online in list(self.server.online_players or []):
                self._push_sidebar_for_player(online)
        except Exception:
            pass

    def on_disable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_disable is called!")
        try:
            self._unregister_sidebar_page()
        except Exception:
            pass

        # 停止统一定时任务
        if self.thirst_task is not None:
            try:
                self.thirst_task.cancel()
            except Exception:
                pass
            self.thirst_task = None
        if self.nutrition_manager is not None:
            self.nutrition_manager.stop_timer()
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.stop_timer()
        # 先把在线玩家数值写入数据库，再关连接
        try:
            for player in self.server.online_players:
                self._persist_player_thirst(player)
                if self.nutrition_manager is not None:
                    self.nutrition_manager.clear_symptoms(player)
                    self.nutrition_manager.persist_player(player)
                if self.zombie_virus_manager is not None:
                    self.zombie_virus_manager.persist_player(player)
                if self.fracture_manager is not None:
                    self.fracture_manager.persist_player(player)
        except Exception:
            pass
        if hasattr(self, 'db_manager'):
            self.db_manager.close()

    def _get_arc_core(self):
        """优先复用已探测的 arc_core，否则再向插件管理器查询。"""
        try:
            plugin = getattr(self, "economy_plugin", None)
            if plugin is not None and getattr(plugin, "name", None) == "arc_core":
                return plugin
            if plugin is not None and callable(getattr(plugin, "api_sidebar_register_page", None)):
                return plugin
        except Exception:
            pass
        try:
            return self.server.plugin_manager.get_plugin("arc_core")
        except Exception:
            return None

    def _is_infection_enabled(self) -> bool:
        zvm = self.zombie_virus_manager
        return bool(zvm is not None and getattr(zvm, "infection_enabled", False))

    def _register_sidebar_page(self) -> None:
        """向弧光核心注册真实生存健康侧边栏页面。"""
        # 先卸再挂，保证 infection_enabled 开关切换后行模板同步更新
        if self._sidebar_registered:
            self._unregister_sidebar_page()
        self._sidebar_registered = False
        arc = self._get_arc_core()
        if arc is None:
            self._safe_log(
                "warning",
                "[ARCRealisticSurvival] arc_core 未加载，跳过侧边栏页面注册",
            )
            return
        register = getattr(arc, "api_sidebar_register_page", None)
        if not callable(register):
            self._safe_log(
                "warning",
                "[ARCRealisticSurvival] 当前 arc_core 不支持侧边栏 API，请升级到 v0.9.0+",
            )
            return
        try:
            lines = [
                "§8----------",
                "§7口渴：§f{thirst}§8/100",
                "§7维A：§f{vitamin_a} §8{sev_a}",
                "§7维C：§f{vitamin_c} §8{sev_c}",
                "§7铁：§f{iron} §8{sev_iron}",
                "§7蛋白：§f{protein} §8{sev_protein}",
            ]
            if self._is_infection_enabled():
                lines.extend(
                    [
                        "§7感染：§f{infection}§8/{infection_max}",
                        "§7状态：§6{infection_status}",
                    ]
                )
            lines.append("§8----------")
            ok = register(
                self._sidebar_page_id,
                "§6真实生存",
                lines,
                owner="arc_realistic_survival",
                priority=10,
                hide_line_if_missing=True,
            )
            self._sidebar_registered = bool(ok)
            if self._sidebar_registered:
                self._safe_log(
                    "info",
                    "[ARCRealisticSurvival] 已向弧光核心注册侧边栏页面 ars_health"
                    + ("（含感染）" if self._is_infection_enabled() else "（无感染）"),
                )
            else:
                self._safe_log(
                    "warning",
                    "[ARCRealisticSurvival] 侧边栏页面注册返回失败",
                )
        except Exception as e:
            self._safe_log("error", f"[ARCRealisticSurvival] 侧边栏页面注册异常: {e}")

    def _unregister_sidebar_page(self) -> None:
        if not self._sidebar_registered:
            return
        arc = self._get_arc_core()
        unregister = getattr(arc, "api_sidebar_unregister_page", None) if arc else None
        if callable(unregister):
            try:
                unregister(self._sidebar_page_id)
            except Exception:
                pass
        self._sidebar_registered = False

    def _severity_short_label(self, severity: str) -> str:
        mapping = {
            "healthy": "健康",
            "mild": "轻症",
            "moderate": "中症",
            "severe": "重症",
        }
        return mapping.get(str(severity or "healthy"), "?")

    def _build_sidebar_values(self, player) -> dict:
        """汇总口渴 / 营养 / 感染，供侧边栏键值模板使用。"""
        xuid = self._get_player_xuid(player)
        thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))

        vitamin_a = vitamin_c = iron = protein = 100
        sev_a = sev_c = sev_iron = sev_protein = self._severity_short_label("healthy")
        if self.nutrition_manager is not None:
            nm = self.nutrition_manager
            data = nm.player_nutrition.get(xuid) or nm._default_nutrition()
            vitamin_a = int(data.get("vitamin_a", nm.nutrition_initial))
            vitamin_c = int(data.get("vitamin_c", nm.nutrition_initial))
            iron = int(data.get("iron", nm.nutrition_initial))
            protein = int(data.get("protein", nm.nutrition_initial))
            sev = nm.player_severity.get(xuid) or {}
            sev_a = self._severity_short_label(sev.get("vitamin_a") or nm.get_severity(vitamin_a))
            sev_c = self._severity_short_label(sev.get("vitamin_c") or nm.get_severity(vitamin_c))
            sev_iron = self._severity_short_label(sev.get("iron") or nm.get_severity(iron))
            sev_protein = self._severity_short_label(
                sev.get("protein") or nm.get_severity(protein)
            )

        infection = 0
        infection_max = 100
        infection_status = "未感染"
        infection_on = self._is_infection_enabled()
        if infection_on and self.zombie_virus_manager is not None:
            zm = self.zombie_virus_manager
            infection = int(float(zm.player_infection.get(xuid, 0.0)))
            infection_max = int(getattr(zm, "infection_max", 100) or 100)
            threshold = float(getattr(zm, "infection_threshold", 50) or 50)
            if infection <= 0:
                infection_status = "未感染"
            elif infection >= threshold:
                infection_status = "恶化中"
            else:
                infection_status = "恢复中"

        values = {
            "thirst": thirst,
            "vitamin_a": vitamin_a,
            "vitamin_c": vitamin_c,
            "iron": iron,
            "protein": protein,
            "sev_a": sev_a,
            "sev_c": sev_c,
            "sev_iron": sev_iron,
            "sev_protein": sev_protein,
        }
        # 关闭感染时不写入相关键，配合 hide_line_if_missing 隐藏侧边栏行
        if infection_on:
            values["infection"] = infection
            values["infection_max"] = infection_max
            values["infection_status"] = infection_status
        return values

    def _push_sidebar_for_player(self, player) -> None:
        """把当前生存状态推送到弧光核心侧边栏页面。"""
        if player is None:
            return
        if not self._sidebar_registered:
            # 晚加载的 arc_core：尝试再注册一次
            self._register_sidebar_page()
            if not self._sidebar_registered:
                return
        arc = self._get_arc_core()
        set_values = getattr(arc, "api_sidebar_set_values", None) if arc else None
        if not callable(set_values):
            return
        try:
            xuid = str(self._get_player_xuid(player) or "").strip()
            if not xuid:
                return
            values = self._build_sidebar_values(player)
            set_values(self._sidebar_page_id, values, xuid=xuid)
        except Exception as e:
            self._safe_log("error", f"[ARCRealisticSurvival] push sidebar error: {e}")

    def _init_default_settings(self) -> None:
        """初始化默认配置"""
        # 交易税率 (默认5%)
        tax_rate = self.setting_manager.GetSetting("trade_tax_rate")
        if tax_rate is None:
            self.setting_manager.SetSetting("trade_tax_rate", "0.05")
            self._safe_log('info', "[ARCRealisticSurvival] Set default trade tax rate: 5%")
        
        # 最大商店数量限制 (默认50)
        max_shops = self.setting_manager.GetSetting("max_shops_per_player")
        if max_shops is None:
            self.setting_manager.SetSetting("max_shops_per_player", "50")
            self._safe_log('info', "[ARCRealisticSurvival] Set default max shops per player: 50")
        
        # 是否启用交易税 (默认启用)
        tax_enabled = self.setting_manager.GetSetting("trade_tax_enabled")
        if tax_enabled is None:
            self.setting_manager.SetSetting("trade_tax_enabled", "true")
            self._safe_log('info', "[ARCRealisticSurvival] Trade tax enabled by default")

    def _init_economy_plugin(self) -> None:
        """初始化经济插件 - 检查 arc_core 优先，然后 umoney"""
        try:
            self.economy_plugin = self.server.plugin_manager.get_plugin('arc_core')
            if self.economy_plugin is not None:
                self._safe_log('info', "[ARCRealisticSurvival] Using ARC Core economy system for money rewards.")
            else:
                self.economy_plugin = self.server.plugin_manager.get_plugin('umoney')
                if self.economy_plugin is not None:
                    self._safe_log('info', "[ARCRealisticSurvival] Using UMoney economy system for money rewards.")
                else:
                    self._safe_log('warning', "[ARCRealisticSurvival] No supported economy plugin found (arc_core or umoney). Money rewards will not be available.")
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] Failed to load economy plugin: {e}. Money rewards will not be available.")

    def _get_player_money(self, player_name: str) -> int:
        return 0

    def _change_player_money(self, player_name: str, amount: int) -> bool:
        return False

    def _is_admin_sender(self, sender) -> bool:
        """OP 或服务器控制台才可用的高级命令校验。"""
        if getattr(sender, "is_op", False):
            return True
        # 控制台通常不是玩家实体
        if not hasattr(sender, "game_mode") and not hasattr(sender, "xuid"):
            return True
        try:
            if sender.has_permission("arc_realistic_survival.command.admin"):
                return True
        except Exception:
            pass
        return False

    def _is_same_player(self, sender, target) -> bool:
        if sender is None or target is None:
            return False
        try:
            if sender is target:
                return True
        except Exception:
            pass
        try:
            sx = self._get_player_xuid(sender)
            tx = self._get_player_xuid(target)
            if sx and tx and sx == tx:
                return True
        except Exception:
            pass
        try:
            return str(getattr(sender, "name", "")).lower() == str(getattr(target, "name", "")).lower()
        except Exception:
            return False

    def _can_item_affect(self, sender, target) -> bool:
        """物品对接指令：控制台/OP 可改任意人；普通玩家仅可改自己。"""
        if target is None:
            return False
        if self._is_admin_sender(sender):
            return True
        if not hasattr(sender, "game_mode") and not hasattr(sender, "xuid"):
            return True
        try:
            if not sender.has_permission("arc_realistic_survival.command.item"):
                return False
        except Exception:
            pass
        return self._is_same_player(sender, target)

    def _resolve_online_player(self, name: str):
        try:
            return self.server.get_player(name)
        except Exception:
            return None

    def _sync_creative_snap_nutrition(self, target, data: dict) -> None:
        xuid = self._get_player_xuid(target)
        snap = self._creative_snapshots.get(xuid)
        if snap is not None:
            snap["nutrition"] = dict(data)

    def _sync_creative_snap_infection(self, target, value: float) -> None:
        xuid = self._get_player_xuid(target)
        snap = self._creative_snapshots.get(xuid)
        if snap is not None:
            snap["infection"] = float(value)

    def _sync_creative_snap_thirst(self, target, value: int) -> None:
        xuid = self._get_player_xuid(target)
        snap = self._creative_snapshots.get(xuid)
        if snap is not None:
            snap["thirst"] = int(value)

    def _cmd_apply_thirst_delta(self, sender, target, delta: float, quiet: bool = False) -> bool:
        try:
            new_val = self._apply_thirst_delta(
                target, int(round(delta)), reason="command", notify=not quiet
            )
            self._sync_creative_snap_thirst(target, new_val)
            if not quiet:
                sender.send_message(
                    f"[ARS] {target.name} 口渴 {int(delta):+d} → {int(new_val)}"
                )
            return True
        except Exception as e:
            sender.send_message(f"[ARS] 口渴调整失败: {e}")
            return False

    def _cmd_apply_nutri_delta(self, sender, target, nutrient: str, delta: int, quiet: bool = False) -> bool:
        if self.nutrition_manager is None:
            sender.send_message("[ARS] 营养系统未初始化")
            return False
        key = str(nutrient).lower().strip()
        try:
            if key == "all":
                deltas = {k: int(delta) for k in NUTRIENT_KEYS}
            elif key in NUTRIENT_KEYS:
                deltas = {key: int(delta)}
            else:
                sender.send_message("[ARS] 营养素须为: vitamin_a, vitamin_c, iron, protein, all")
                return False
            data = self.nutrition_manager.apply_deltas(target, deltas, item_label="command")
            self._sync_creative_snap_nutrition(target, data)
            if not quiet:
                shown = ",".join(f"{k}{v:+d}" for k, v in deltas.items())
                sender.send_message(f"[ARS] {target.name} 营养 {shown}")
            return True
        except Exception as e:
            sender.send_message(f"[ARS] 营养调整失败: {e}")
            return False

    def _cmd_apply_purify(self, sender, target, amount: float, quiet: bool = False) -> bool:
        if not self._is_infection_enabled():
            if not quiet:
                sender.send_message("[ARS] 感染系统已关闭（infection_enabled=false）")
            return False
        if self.zombie_virus_manager is None:
            sender.send_message("[ARS] 感染系统未初始化")
            return False
        if amount <= 0:
            sender.send_message("[ARS] 净化量必须大于 0")
            return False
        try:
            xuid = self._get_player_xuid(target)
            old = float(self.zombie_virus_manager.player_infection.get(xuid, 0.0))
            new_val = self.zombie_virus_manager.apply_delta(
                target, -float(amount), source_label="净化"
            )
            self._sync_creative_snap_infection(target, new_val)
            removed = max(0.0, old - float(new_val))
            if not quiet:
                sender.send_message(
                    f"[ARS] 已净化 {target.name} 感染 -{removed:.0f}：{int(old)} → {int(new_val)}"
                )
                try:
                    target.send_toast("净化", f"感染值降低了 {int(removed)} 点。")
                except Exception:
                    pass
            return True
        except Exception as e:
            sender.send_message(f"[ARS] 净化失败: {e}")
            return False

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        match command.name:
            case "heal":
                if not self._is_admin_sender(sender):
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                    return True
                if len(args) < 1:
                    sender.send_message("[ARS] 用法: /heal <玩家>")
                    return True
                target = self._resolve_online_player(args[0])
                if target is None:
                    sender.send_message(f"[ARS] 找不到玩家: {args[0]}")
                    return True
                if self.nutrition_manager is None:
                    sender.send_message("[ARS] 营养系统未初始化")
                    return True
                try:
                    data = self.nutrition_manager.heal_to(target, 80)
                    self._sync_creative_snap_nutrition(target, data)
                    fracture_cleared = False
                    if self.fracture_manager is not None:
                        fracture_cleared = self.fracture_manager.clear_fracture(target, notify=False)
                    extra = "，腿伤已治疗" if fracture_cleared else ""
                    sender.send_message(
                        f"[ARS] 已治愈 {target.name}：营养已设为 80，缺素病症已清除{extra}（感染未改动）"
                    )
                    try:
                        target.send_toast("治疗", "你的身体状况已恢复。")
                    except Exception:
                        pass
                except Exception as e:
                    sender.send_message(f"[ARS] 治愈失败: {e}")
                return True

            case "purify":
                if len(args) < 2:
                    sender.send_message("[ARS] 用法: /purify <玩家> <净化量>")
                    return True
                target = self._resolve_online_player(args[0])
                if target is None:
                    sender.send_message(f"[ARS] 找不到玩家: {args[0]}")
                    return True
                if not self._can_item_affect(sender, target):
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                    return True
                try:
                    amount = float(args[1])
                except ValueError:
                    sender.send_message("[ARS] 净化量必须是数字")
                    return True
                self._cmd_apply_purify(sender, target, amount)
                return True

            case "thirstadd":
                if len(args) < 2:
                    sender.send_message("[ARS] 用法: /thirstadd <玩家> <增量>")
                    return True
                target = self._resolve_online_player(args[0])
                if target is None:
                    sender.send_message(f"[ARS] 找不到玩家: {args[0]}")
                    return True
                if not self._can_item_affect(sender, target):
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                    return True
                try:
                    delta = float(args[1])
                except ValueError:
                    sender.send_message("[ARS] 增量必须是数字")
                    return True
                self._cmd_apply_thirst_delta(sender, target, delta)
                return True

            case "nutriadd":
                if len(args) < 3:
                    sender.send_message(
                        "[ARS] 用法: /nutriadd <玩家> <vitamin_a|vitamin_c|iron|protein|all> <增量>"
                    )
                    return True
                target = self._resolve_online_player(args[0])
                if target is None:
                    sender.send_message(f"[ARS] 找不到玩家: {args[0]}")
                    return True
                if not self._can_item_affect(sender, target):
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                    return True
                try:
                    delta = int(args[2])
                except ValueError:
                    sender.send_message("[ARS] 增量必须是整数")
                    return True
                self._cmd_apply_nutri_delta(sender, target, args[1], delta)
                return True

            case "arseffect":
                if len(args) < 2:
                    sender.send_message("[ARS] 用法: /arseffect <玩家> <物品ID>")
                    sender.send_message("[ARS] 示例: /arseffect Steve arc:bottled_water")
                    return True
                target = self._resolve_online_player(args[0])
                if target is None:
                    sender.send_message(f"[ARS] 找不到玩家: {args[0]}")
                    return True
                if not self._can_item_affect(sender, target):
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                    return True
                if self.consume_manager is None:
                    sender.send_message("[ARS] 进食效果系统未初始化")
                    return True
                item_id = normalize_item_id(" ".join(args[1:]))
                status, label, bits = self.consume_manager.apply_by_id(target, item_id)
                if status == "unknown":
                    known = ", ".join(self.consume_manager.known_item_ids())
                    sender.send_message(f"[ARS] 未知物品ID: {item_id}\n可用: {known}")
                    return True
                if status == "deduped":
                    self._log_consume_debug(
                        f"player={target.name} item={item_id} 已在 2s 内生效，跳过重复应用"
                    )
                    return True
                detail = " ".join(bits) if bits else "无变化"
                sender.send_message(f"[ARS] {label} → {target.name}: {detail}")
                try:
                    target.send_toast(f"服用了{label}", detail if bits else "已生效")
                except Exception:
                    pass
                return True

            case "ars":
                if args and len(args) >= 1:
                    sub = str(args[0]).lower()
                    if sub == "reload":
                        if hasattr(sender, 'is_op') and not sender.is_op:
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        self._reload_survival_settings()
                        sender.send_message("[ARS] 配置与物品效果已重载")
                        return True
                    if sub == "nutrition":
                        if not hasattr(sender, 'send_form'):
                            sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                            return True
                        self._show_nutrition_panel(sender)
                        return True
                    if sub == "infection":
                        if not hasattr(sender, 'send_form'):
                            sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                            return True
                        if not self._is_infection_enabled():
                            sender.send_message("[ARS] 感染系统已关闭（infection_enabled=false）")
                            return True
                        self._show_infection_panel(sender)
                        return True
                    if sub == "infectset":
                        if not getattr(sender, 'is_op', False):
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        if not self._is_infection_enabled():
                            sender.send_message("[ARS] 感染系统已关闭（infection_enabled=false）")
                            return True
                        if len(args) < 3:
                            sender.send_message("[ARS] 用法: /ars infectset <玩家> <0-100>")
                            return True
                        target = self.server.get_player(args[1])
                        if target is None:
                            sender.send_message(f"[ARS] 找不到玩家: {args[1]}")
                            return True
                        try:
                            value = float(args[2])
                        except ValueError:
                            sender.send_message("[ARS] 数值必须是 0-100")
                            return True
                        if self.zombie_virus_manager is None:
                            sender.send_message("[ARS] 感染系统未初始化")
                            return True
                        try:
                            val = self.zombie_virus_manager.set_infection(target, value)
                            sender.send_message(f"[ARS] 已设置 {target.name} 感染值={int(val)}")
                        except Exception as e:
                            sender.send_message(f"[ARS] 设置失败: {e}")
                        return True
                    if sub == "nutriset":
                        if not getattr(sender, 'is_op', False):
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        if len(args) < 4:
                            sender.send_message("[ARS] 用法: /ars nutriset <玩家> <vitamin_a|vitamin_c|iron|protein> <0-100>")
                            return True
                        target = self.server.get_player(args[1])
                        if target is None:
                            sender.send_message(f"[ARS] 找不到玩家: {args[1]}")
                            return True
                        nutrient = args[2].lower()
                        if nutrient not in NUTRIENT_KEYS:
                            sender.send_message("[ARS] 营养素必须是: vitamin_a, vitamin_c, iron, protein")
                            return True
                        try:
                            value = int(args[3])
                        except ValueError:
                            sender.send_message("[ARS] 数值必须是 0-100 的整数")
                            return True
                        if self.nutrition_manager is None:
                            sender.send_message("[ARS] 营养系统未初始化")
                            return True
                        try:
                            data = self.nutrition_manager.set_nutrient(target, nutrient, value)
                            sender.send_message(
                                f"[ARS] 已设置 {target.name} 的 {nutrient}={data[nutrient]}"
                            )
                        except Exception as e:
                            sender.send_message(f"[ARS] 设置失败: {e}")
                        return True
                # 全员打开个人状态；配置管理仅 OP 在面板内可见（对齐弧光核心 /arc）
                if not hasattr(sender, 'send_form'):
                    sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                    return True
                self._show_ars_home_panel(sender)
                return True
        return True

    def _reload_survival_settings(self) -> None:
        # 重新加载配置与物品效果，并重启定时器
        try:
            self._load_thirst_settings()
            if self.consume_manager is not None:
                self.consume_manager.load_items_config()
            if self.nutrition_manager is not None:
                self.nutrition_manager.load_settings()
            if self.zombie_virus_manager is not None:
                self.zombie_virus_manager.load_settings()
                self.zombie_virus_manager.load_sources_config()
            if self.fracture_manager is not None:
                self.fracture_manager.load_settings()
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            if self.nutrition_manager is not None:
                self.nutrition_manager.stop_timer()
            if self.zombie_virus_manager is not None:
                self.zombie_virus_manager.stop_timer()
            self._start_survival_timer()
            # 感染开关可能变化，重挂侧边栏行模板并刷新在线玩家
            self._register_sidebar_page()
            try:
                for p in self.server.online_players:
                    if self._is_survival_like(p):
                        self._apply_thirst_movement_modifier(p)
                    self._push_sidebar_for_player(p)
            except Exception:
                pass
        except Exception as e:
            self._safe_log('error', f"[ARS] reload settings error: {e}")

    def _show_ars_home_panel(self, player) -> None:
        """个人状态主页（全员）；仅 OP 显示「配置管理」按钮。"""
        try:
            xuid = self._get_player_xuid(player)
            thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            lines = [
                "=== 个人状态 ===",
                f"口渴: {thirst}/100",
            ]
            if self.nutrition_manager is not None:
                lines.append("")
                lines.extend(self.nutrition_manager.get_status_lines(player))
            if self._is_infection_enabled() and self.zombie_virus_manager is not None:
                lines.append("")
                lines.extend(self.zombie_virus_manager.get_status_lines(player))

            form = ActionForm(
                title="真实生存",
                content="\n".join(lines),
                on_close=lambda s: None,
            )
            form.add_button("营养详情", on_click=self._show_nutrition_panel)
            if self._is_infection_enabled():
                form.add_button("感染详情", on_click=self._show_infection_panel)
            if getattr(player, "is_op", False):
                form.add_button("配置管理", on_click=self._show_survival_config_panel)
            form.add_button("刷新", on_click=self._show_ars_home_panel)
            player.send_form(form)
        except Exception as e:
            self._safe_log('error', f"[ARS] show home panel error: {e}")
            try:
                player.send_message(f"[ARS] 无法打开个人状态: {e}")
            except Exception:
                pass

    def _show_survival_config_panel(self, player) -> None:
        if not getattr(player, "is_op", False):
            try:
                player.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
            except Exception:
                pass
            return
        try:
            nm = self.nutrition_manager
            zvm = self.zombie_virus_manager
            fm = self.fracture_manager
            title = "ARC Realistic Survival 配置"
            content_lines = [
                "修改后提交即写入配置并热重载",
                "口渴/营养/感染: 见各字段说明",
            ]
            header = Label(text="\n".join(content_lines))
            input_tick = TextInput(
                label="thirst_tick_seconds",
                placeholder="口渴衰减间隔（秒）",
                default_value=str(self.thirst_tick_seconds)
            )
            input_decay = TextInput(
                label="thirst_decay_per_tick",
                placeholder="每次衰减口渴值(>=0)",
                default_value=str(self.thirst_decay_per_tick)
            )
            input_move = TextInput(
                label="thirst_moving_multiplier",
                placeholder="移动时衰减倍数(>=1.0)",
                default_value=str(self.thirst_moving_multiplier)
            )
            input_initial = TextInput(
                label="thirst_initial",
                placeholder="初始口渴值(0-100)",
                default_value=str(self.thirst_initial)
            )
            input_nutrition_tick = TextInput(
                label="nutrition_tick_seconds",
                placeholder="营养衰减间隔（秒，默认300）",
                default_value=str(nm.nutrition_tick_seconds if nm else 300)
            )
            input_nutrition_decay = TextInput(
                label="nutrition_decay_per_tick",
                placeholder="每次衰减营养值(>=0)",
                default_value=str(nm.nutrition_decay_per_tick if nm else 1)
            )
            input_nutrition_initial = TextInput(
                label="nutrition_initial",
                placeholder="初始营养值(0-100)",
                default_value=str(nm.nutrition_initial if nm else 100)
            )
            input_nutrition_cooldown = TextInput(
                label="nutrition_warn_cooldown_seconds",
                placeholder="症状提示冷却（秒）",
                default_value=str(nm.nutrition_warn_cooldown_seconds if nm else 300)
            )
            input_infection_enabled = TextInput(
                label="infection_enabled",
                placeholder="感染开关 true/false（默认 false）",
                default_value=("true" if (zvm and zvm.infection_enabled) else "false")
            )
            input_infection_tick = TextInput(
                label="infection_tick_seconds",
                placeholder="感染 tick 间隔（秒）",
                default_value=str(zvm.infection_tick_seconds if zvm else 12)
            )
            input_infection_threshold = TextInput(
                label="infection_threshold",
                placeholder="恶化临界值（默认50）",
                default_value=str(int(zvm.infection_threshold if zvm else 50))
            )
            input_infection_growth = TextInput(
                label="infection_growth_per_minute",
                placeholder="超临界每分钟增长",
                default_value=str(int(zvm.infection_growth_per_minute if zvm else 5))
            )
            input_infection_decay = TextInput(
                label="infection_decay_per_minute",
                placeholder="低于临界每分钟下降",
                default_value=str(int(zvm.infection_decay_per_minute if zvm else 2))
            )
            input_fracture_enabled = TextInput(
                label="fracture_enabled",
                placeholder="骨裂开关 true/false（默认 true）",
                default_value=("true" if (fm and fm.enabled) else "false")
            )
            input_fracture_min = TextInput(
                label="fracture_fall_damage_min",
                placeholder="坠落伤害大于该值才可能骨裂（默认 5）",
                default_value=str(fm.fall_damage_min if fm else 5)
            )
            input_fracture_base = TextInput(
                label="fracture_chance_base_percent",
                placeholder="刚过阈值时的骨裂率 %（默认 10）",
                default_value=str(fm.chance_base_percent if fm else 10)
            )
            input_fracture_per = TextInput(
                label="fracture_chance_per_damage_percent",
                placeholder="每高 1 点伤害增加的骨裂率 %（默认 5）",
                default_value=str(fm.chance_per_damage_percent if fm else 5)
            )
            input_fracture_max = TextInput(
                label="fracture_chance_max_percent",
                placeholder="骨裂率上限 %（默认 80）",
                default_value=str(fm.chance_max_percent if fm else 80)
            )
            input_fracture_speed = TextInput(
                label="fracture_speed_multiplier",
                placeholder="骨裂后移速倍率，0.75 = 当前基础上 -25%",
                default_value=str(fm.speed_multiplier if fm else 0.75)
            )
            input_fracture_heal = TextInput(
                label="fracture_heal_seconds",
                placeholder="骨裂基础时长（最低坠落档，默认 300）",
                default_value=str(fm.heal_seconds if fm else 300)
            )
            input_fracture_drain = TextInput(
                label="fracture_drain_hp_per_second",
                placeholder="移动时每秒掉血（默认 1，0=不掉血）",
                default_value=str(fm.drain_hp_per_second if fm else 1)
            )
            input_fracture_severe_speed = TextInput(
                label="fracture_severe_speed_multiplier",
                placeholder="骨折后移速倍率，0.5 = 当前基础上 -50%",
                default_value=str(fm.severe_speed_multiplier if fm else 0.5)
            )
            input_crack_per_damage = TextInput(
                label="fracture_crack_per_damage_seconds",
                placeholder="骨裂时长：每点坠落伤害延长秒数（默认 30）",
                default_value=str(fm.crack_per_damage_seconds if fm else 30)
            )
            input_crack_duration_max = TextInput(
                label="fracture_crack_duration_max",
                placeholder="骨裂时长上限/夹板降级后的时长（默认 900）",
                default_value=str(fm.crack_duration_max if fm else 900)
            )

            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    new_tick = int(float(data[1]))
                    new_decay = int(float(data[2]))
                    new_move = float(data[3])
                    new_initial = int(float(data[4]))
                    new_n_tick = int(float(data[5]))
                    new_n_decay = int(float(data[6]))
                    new_n_initial = int(float(data[7]))
                    new_n_cooldown = int(float(data[8]))
                    new_i_enabled_raw = str(data[9]).strip().lower()
                    new_i_tick = int(float(data[10]))
                    new_i_threshold = float(data[11])
                    new_i_growth = float(data[12])
                    new_i_decay = float(data[13])
                    new_f_enabled_raw = str(data[14]).strip().lower()
                    new_f_min = float(data[15])
                    new_f_base = float(data[16])
                    new_f_per = float(data[17])
                    new_f_max = float(data[18])
                    new_f_speed = float(data[19])
                    new_f_heal = int(float(data[20]))
                    new_f_drain = int(float(data[21]))
                    new_f_severe_speed = float(data[22])
                    new_crack_per_damage = float(data[23])
                    new_crack_max = float(data[24])

                    if new_tick < 1:
                        raise ValueError("thirst tick seconds < 1")
                    if new_decay < 0:
                        raise ValueError("thirst decay < 0")
                    if new_move < 1.0:
                        raise ValueError("moving multiplier < 1.0")
                    if new_initial < 0 or new_initial > 100:
                        raise ValueError("thirst initial out of [0,100]")
                    if new_n_tick < 30:
                        raise ValueError("nutrition tick seconds < 30")
                    if new_n_decay < 0:
                        raise ValueError("nutrition decay < 0")
                    if new_n_initial < 0 or new_n_initial > 100:
                        raise ValueError("nutrition initial out of [0,100]")
                    if new_n_cooldown < 30:
                        raise ValueError("nutrition cooldown < 30")
                    if new_i_enabled_raw not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                        raise ValueError("infection_enabled must be true/false")
                    new_i_enabled = new_i_enabled_raw in ("1", "true", "yes", "on")
                    if new_i_tick < 6:
                        raise ValueError("infection tick seconds < 6")
                    if new_i_threshold < 1 or new_i_threshold > 99:
                        raise ValueError("infection threshold out of (0,100)")
                    if new_i_growth < 0 or new_i_decay < 0:
                        raise ValueError("infection growth/decay < 0")
                    if new_f_enabled_raw not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                        raise ValueError("fracture_enabled must be true/false")
                    new_f_enabled = new_f_enabled_raw in ("1", "true", "yes", "on")
                    if new_f_min < 0:
                        raise ValueError("fracture fall damage min < 0")
                    if new_f_base < 0 or new_f_per < 0 or new_f_max < 0:
                        raise ValueError("fracture chance percents < 0")
                    if not (0.05 <= new_f_speed <= 1.0):
                        raise ValueError("fracture speed multiplier out of [0.05,1.0]")
                    if not (0.05 <= new_f_severe_speed <= 1.0):
                        raise ValueError("fracture severe speed multiplier out of [0.05,1.0]")
                    if new_crack_per_damage < 0:
                        raise ValueError("fracture crack per damage < 0")
                    if new_crack_max < 30:
                        raise ValueError("fracture crack duration max < 30")
                    if new_f_heal < 30:
                        raise ValueError("fracture heal seconds < 30")
                    if new_f_drain < 0:
                        raise ValueError("fracture drain hp < 0")

                    self.setting_manager.SetSetting("thirst_tick_seconds", str(new_tick))
                    self.setting_manager.SetSetting("thirst_decay_per_tick", str(new_decay))
                    self.setting_manager.SetSetting("thirst_moving_multiplier", str(new_move))
                    self.setting_manager.SetSetting("thirst_initial", str(new_initial))
                    self.setting_manager.SetSetting("nutrition_tick_seconds", str(new_n_tick))
                    self.setting_manager.SetSetting("nutrition_decay_per_tick", str(new_n_decay))
                    self.setting_manager.SetSetting("nutrition_initial", str(new_n_initial))
                    self.setting_manager.SetSetting("nutrition_warn_cooldown_seconds", str(new_n_cooldown))
                    self.setting_manager.SetSetting("infection_enabled", "true" if new_i_enabled else "false")
                    self.setting_manager.SetSetting("infection_tick_seconds", str(new_i_tick))
                    self.setting_manager.SetSetting("infection_threshold", str(new_i_threshold))
                    self.setting_manager.SetSetting("infection_growth_per_minute", str(new_i_growth))
                    self.setting_manager.SetSetting("infection_decay_per_minute", str(new_i_decay))
                    self.setting_manager.SetSetting("fracture_enabled", "true" if new_f_enabled else "false")
                    self.setting_manager.SetSetting("fracture_fall_damage_min", str(new_f_min))
                    self.setting_manager.SetSetting("fracture_chance_base_percent", str(new_f_base))
                    self.setting_manager.SetSetting("fracture_chance_per_damage_percent", str(new_f_per))
                    self.setting_manager.SetSetting("fracture_chance_max_percent", str(new_f_max))
                    self.setting_manager.SetSetting("fracture_speed_multiplier", str(new_f_speed))
                    self.setting_manager.SetSetting("fracture_heal_seconds", str(new_f_heal))
                    self.setting_manager.SetSetting("fracture_drain_hp_per_second", str(new_f_drain))
                    self.setting_manager.SetSetting("fracture_severe_speed_multiplier", str(new_f_severe_speed))
                    self.setting_manager.SetSetting("fracture_crack_per_damage_seconds", str(new_crack_per_damage))
                    self.setting_manager.SetSetting("fracture_crack_duration_max", str(new_crack_max))

                    self._reload_survival_settings()
                    sender.send_message("[ARS] 配置已保存并重载")
                except Exception as e:
                    sender.send_message(f"[ARS] 配置提交失败: {e}")

            panel = ModalForm(
                title=title,
                controls=[
                    header, input_tick, input_decay, input_move, input_initial,
                    input_nutrition_tick, input_nutrition_decay, input_nutrition_initial, input_nutrition_cooldown,
                    input_infection_enabled, input_infection_tick, input_infection_threshold,
                    input_infection_growth, input_infection_decay,
                    input_fracture_enabled, input_fracture_min, input_fracture_base,
                    input_fracture_per, input_fracture_max, input_fracture_speed,
                    input_fracture_heal, input_fracture_drain, input_fracture_severe_speed,
                    input_crack_per_damage, input_crack_duration_max,
                ],
                on_close=lambda s: None,
                on_submit=on_submit,
            )
            player.send_form(panel)
        except Exception as e:
            self._safe_log('error', f"[ARS] show config panel error: {e}")

    def _show_nutrition_panel(self, player) -> None:
        try:
            if self.nutrition_manager is None:
                player.send_message("[ARS] 营养系统未初始化")
                return
            status_lines = self.nutrition_manager.get_status_lines(player)
            catalog_lines = (
                self.consume_manager.get_catalog_lines(limit=15)
                if self.consume_manager is not None
                else []
            )
            body = "\n".join(status_lines + [""] + catalog_lines)
            form = ActionForm(
                title="营养学",
                content=body,
                on_close=lambda s: None,
            )
            form.add_button("刷新", on_click=self._show_nutrition_panel)
            form.add_button("返回", on_click=self._show_ars_home_panel)
            player.send_form(form)
        except Exception as e:
            self._safe_log('error', f"[ARS] show nutrition panel error: {e}")
            player.send_message(f"[ARS] 无法打开营养面板: {e}")

    def _show_infection_panel(self, player) -> None:
        try:
            if self.zombie_virus_manager is None:
                player.send_message("[ARS] 感染系统未初始化")
                return
            if not self._is_infection_enabled():
                player.send_message("[ARS] 感染系统已关闭（infection_enabled=false）")
                return
            status_lines = self.zombie_virus_manager.get_status_lines(player)
            catalog_lines = self.zombie_virus_manager.get_source_catalog_lines(limit=15)
            body = "\n".join(status_lines + [""] + catalog_lines)
            form = ActionForm(
                title="丧尸病毒感染",
                content=body,
                on_close=lambda s: None,
            )
            form.add_button("刷新", on_click=self._show_infection_panel)
            form.add_button("返回", on_click=self._show_ars_home_panel)
            player.send_form(form)
        except Exception as e:
            self._safe_log('error', f"[ARS] show infection panel error: {e}")
            player.send_message(f"[ARS] 无法打开感染面板: {e}")
    
    # 数据库（仅生存）

    # 生存-口渴系统：数据库与配置
    def _create_survival_tables(self) -> None:
        thirst_fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "thirst": "INTEGER NOT NULL",
            "updated_at": "TEXT NOT NULL"
        }
        if self.db_manager.create_table("player_thirst", thirst_fields):
            self._safe_log('info', "[ARCRealisticSurvival] player_thirst table ready")
        else:
            self._safe_log('error', "[ARCRealisticSurvival] Failed to create player_thirst table")
        if not self.db_manager.ensure_column("player_thirst", "dehydrated_since", "REAL"):
            self._safe_log('warning', "[ARCRealisticSurvival] failed to add player_thirst.dehydrated_since")

    def _load_thirst_settings(self) -> None:
        def _as_int(raw, default: int) -> int:
            try:
                return int(float(str(raw).strip()))
            except Exception:
                return int(default)

        def _as_float(raw, default: float) -> float:
            try:
                return float(str(raw).strip())
            except Exception:
                return float(default)

        try:
            val = self.setting_manager.GetSetting("thirst_tick_seconds")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_tick_seconds", "10")
                self.thirst_tick_seconds = 10
            else:
                self.thirst_tick_seconds = max(1, _as_int(val, 10))

            val = self.setting_manager.GetSetting("thirst_decay_per_tick")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_decay_per_tick", "1")
                self.thirst_decay_per_tick = 1
            else:
                self.thirst_decay_per_tick = max(0, _as_int(val, 1))

            val = self.setting_manager.GetSetting("thirst_moving_multiplier")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_moving_multiplier", "2.0")
                self.thirst_moving_multiplier = 2.0
            else:
                self.thirst_moving_multiplier = max(1.0, _as_float(val, 2.0))

            val = self.setting_manager.GetSetting("thirst_initial")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_initial", "100")
                self.thirst_initial = 100
            else:
                self.thirst_initial = _as_int(val, 100)

            val = self.setting_manager.GetSetting("thirst_consume_debug")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_consume_debug", "false")
                self.thirst_consume_debug = False
            else:
                self.thirst_consume_debug = str(val).strip().lower() in ("1", "true", "yes", "on")

            val = self.setting_manager.GetSetting("thirst_speed_bonus_threshold")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_bonus_threshold", "80")
                self.thirst_speed_bonus_threshold = 80
            else:
                self.thirst_speed_bonus_threshold = max(0, _as_int(val, 80))

            val = self.setting_manager.GetSetting("thirst_speed_bonus")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_bonus", "0.20")
                self.thirst_speed_bonus = 0.20
            else:
                self.thirst_speed_bonus = _as_float(val, 0.20)

            val = self.setting_manager.GetSetting("thirst_speed_slow_threshold")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_slow_threshold", "30")
                self.thirst_speed_slow_threshold = 30
            else:
                self.thirst_speed_slow_threshold = max(0, _as_int(val, 30))

            val = self.setting_manager.GetSetting("thirst_speed_slow")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_slow", "-0.20")
                self.thirst_speed_slow = -0.20
            else:
                self.thirst_speed_slow = _as_float(val, -0.20)

            val = self.setting_manager.GetSetting("thirst_speed_severe_threshold")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_severe_threshold", "15")
                self.thirst_speed_severe_threshold = 15
            else:
                self.thirst_speed_severe_threshold = max(0, _as_int(val, 15))

            val = self.setting_manager.GetSetting("thirst_speed_severe")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_severe", "-0.50")
                self.thirst_speed_severe = -0.50
            else:
                self.thirst_speed_severe = _as_float(val, -0.50)

            val = self.setting_manager.GetSetting("thirst_fatal_seconds")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_fatal_seconds", "3600")
                self.thirst_fatal_seconds = 3600
            else:
                self.thirst_fatal_seconds = max(1, _as_int(val, 3600))

            val = self.setting_manager.GetSetting("walk_speed_base_multiplier")
            if val is None or val == "":
                self.setting_manager.SetSetting("walk_speed_base_multiplier", "1.0")
                self.walk_speed_base_multiplier = 1.0
            else:
                self.walk_speed_base_multiplier = max(0.1, _as_float(val, 1.0))
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst settings error: {e}")

    def _thirst_speed_amount(self, thirst: int) -> float:
        """口渴分段移速加成：>80 +20%；[30,80] 无；<30 -20%；<15 -50%。"""
        t = max(self.thirst_min, min(self.thirst_max, int(thirst)))
        if t < int(self.thirst_speed_severe_threshold):
            return float(self.thirst_speed_severe)
        if t < int(self.thirst_speed_slow_threshold):
            return float(self.thirst_speed_slow)
        if t > int(self.thirst_speed_bonus_threshold):
            return float(self.thirst_speed_bonus)
        return 0.0

    def _thirst_speed_factor(self, thirst: int) -> float:
        """口渴对应的相对因子：1.0 + 分段加成。"""
        factor = 1.0 + self._thirst_speed_amount(thirst)
        return max(0.01, float(factor))

    def _get_attr_core(self):
        """获取硬依赖 arc_attribute_core（玩家属性/buff 管理器）；找到后缓存。"""
        core = getattr(self, "_attr_core_cache", None)
        if core is not None:
            return core
        try:
            core = self.server.plugin_manager.get_plugin("arc_attribute_core")
        except Exception:
            return None
        if core is not None and callable(getattr(core, "api_add_factor", None)):
            self._attr_core_cache = core
            self._safe_log('info', "[ARCRealisticSurvival] 已接入 arc_attribute_core，移速因子交由属性核心统一管理")
            return core
        return None

    def _ars_speed_amounts(self, player) -> dict[str, float]:
        """ARS 各来源的 walk_speed 调整量（乘法因子 - 1）。"""
        xuid = self._get_player_xuid(player)
        thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        fracture_factor = (
            self.fracture_manager.speed_factor_for(player)
            if self.fracture_manager is not None
            else 1.0
        )
        return {
            "ars:base": float(self.walk_speed_base_multiplier) - 1.0,
            "ars:thirst": float(self._thirst_speed_factor(thirst)) - 1.0,
            "ars:leg": float(fracture_factor) - 1.0,
        }

    def _sync_movement_factors(self, player) -> None:
        """把 ARS 移速因子（基速/口渴/腿伤）同步到属性核心统一连乘落地。

        arc_attribute_core 为硬依赖（Plugin.depend 保证加载顺序）；
        核心缺失时告警一次并跳过（此时移速调整不可用，其余功能不受影响）。
        """
        core = self._get_attr_core()
        if core is None:
            if not getattr(self, "_attr_core_missing_warned", False):
                self._attr_core_missing_warned = True
                self._safe_log(
                    'error',
                    "[ARCRealisticSurvival] 未找到 arc_attribute_core（硬依赖），移速调整不可用",
                )
            return
        try:
            for source, amount in self._ars_speed_amounts(player).items():
                if abs(amount) < 1e-9:
                    try:
                        core.api_remove_factor(player, "walk_speed", source)
                    except Exception:
                        pass
                else:
                    core.api_add_factor(player, "walk_speed", source=source, amount=amount)
        except Exception as e:
            self._safe_log('error', f"[ARS] sync movement via attribute core error: {e}")

    def _apply_thirst_movement_modifier(self, player) -> None:
        """重算并应用移速：把 ARS 三路因子同步到属性核心统一连乘落地。"""
        self._sync_movement_factors(player)

    def _clear_thirst_movement_modifier(self, player) -> None:
        """移除本插件全部移速因子（创造旁路/退出），保留其他插件的改动。"""
        core = self._get_attr_core()
        if core is None:
            return
        try:
            for source in ("ars:base", "ars:thirst", "ars:leg"):
                try:
                    core.api_remove_factor(player, "walk_speed", source)
                except Exception:
                    pass
        except Exception:
            pass

    def _collect_item_identity_strings(self, item) -> list[str]:
        """从 ItemStack 收集可能用于匹配的字符串（去重、保序）。"""
        candidates = []
        seen = set()

        def add_one(val) -> None:
            if val is None:
                return
            s = str(val).strip()
            if not s or s in seen:
                return
            seen.add(s)
            candidates.append(s)

        add_one(getattr(item, "type", None))
        add_one(getattr(item, "name", None))
        try:
            t = getattr(item, "type", None)
            if t is not None and hasattr(t, "name"):
                add_one(getattr(t, "name", None))
            if t is not None:
                add_one(t)
        except Exception:
            pass
        add_one(item)
        return candidates

    # 生存-口渴系统：内部工具
    def _is_survival_like(self, player) -> bool:
        try:
            return player.game_mode == GameMode.SURVIVAL or player.game_mode == GameMode.ADVENTURE
        except Exception:
            return True

    def _enter_non_survival_mode(self, player) -> None:
        """创造/旁观：快照真实数值 → 运行时设为正常值 → 停止后续变动处理。"""
        xuid = self._get_player_xuid(player)
        if xuid not in self._creative_snapshots:
            snap = {
                "thirst": int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial)),
                "dehydrated_since": self.player_xuid_to_dehydrated_since.get(xuid),
                "nutrition": None,
                "infection": None,
            }
            if self.nutrition_manager is not None:
                nm = self.nutrition_manager
                snap["nutrition"] = dict(
                    nm.player_nutrition.get(xuid) or nm._default_nutrition()
                )
            if self.zombie_virus_manager is not None:
                snap["infection"] = float(
                    self.zombie_virus_manager.player_infection.get(xuid, 0.0)
                )
            self._creative_snapshots[xuid] = snap

        self.player_xuid_to_thirst[xuid] = self.thirst_initial
        self.player_xuid_to_dehydrated_since[xuid] = None
        self._clear_thirst_movement_modifier(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.apply_healthy_bypass(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.apply_healthy_bypass(player)
        self._push_sidebar_for_player(player)

    def _leave_non_survival_mode(self, player) -> None:
        """切回生存/冒险：从快照恢复真实数值并重新挂效果。"""
        xuid = self._get_player_xuid(player)
        snap = self._creative_snapshots.pop(xuid, None)
        if snap is not None:
            self.player_xuid_to_thirst[xuid] = int(snap.get("thirst", self.thirst_initial))
            self.player_xuid_to_dehydrated_since[xuid] = snap.get("dehydrated_since")
            if self.nutrition_manager is not None and snap.get("nutrition") is not None:
                self.nutrition_manager.restore_nutrition(player, snap["nutrition"])
            elif self.nutrition_manager is not None:
                self.nutrition_manager._apply_persistent_symptoms(player)
            if self.zombie_virus_manager is not None and snap.get("infection") is not None:
                self.zombie_virus_manager.restore_infection(player, snap["infection"])
        else:
            if self.nutrition_manager is not None:
                self.nutrition_manager._apply_persistent_symptoms(player)
        self._apply_thirst_movement_modifier(player)
        self._sync_dehydration_state(player)
        self._persist_player_thirst(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.persist_player(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.persist_player(player)
        self._push_sidebar_for_player(player)

    def _restore_snapshot_before_persist(self, player) -> None:
        """退出时若仍在创造旁观，把快照写回内存再落库，避免把「正常值」存进数据库。"""
        xuid = self._get_player_xuid(player)
        snap = self._creative_snapshots.pop(xuid, None)
        if snap is None:
            return
        self.player_xuid_to_thirst[xuid] = int(snap.get("thirst", self.thirst_initial))
        self.player_xuid_to_dehydrated_since[xuid] = snap.get("dehydrated_since")
        if self.nutrition_manager is not None and snap.get("nutrition") is not None:
            x = self._get_player_xuid(player)
            data = {k: int(snap["nutrition"].get(k, self.nutrition_manager.nutrition_initial)) for k in NUTRIENT_KEYS}
            self.nutrition_manager.player_nutrition[x] = data
        if self.zombie_virus_manager is not None and snap.get("infection") is not None:
            self.zombie_virus_manager.player_infection[xuid] = float(snap["infection"])

    def _clamp_thirst(self, value: int) -> int:
        """钳制在 [thirst_min, thirst_max]；脱水计时在 0 时启动，不再继续扣成负数。"""
        return max(self.thirst_min, min(self.thirst_max, int(value)))

    def _get_player_xuid(self, player) -> str:
        try:
            return getattr(player, 'xuid', None) or getattr(player, 'uuid', None) or player.name
        except Exception:
            return player.name

    def _resolve_online_player(self, xuid: str = "", name: str = ""):
        xuid_s = str(xuid or "").strip()
        if xuid_s:
            for p in self.server.online_players or []:
                if str(getattr(p, "xuid", "")) == xuid_s:
                    return p
        name_s = str(name or "").strip()
        if name_s:
            try:
                return self.server.get_player(name_s)
            except Exception:
                return None
        return None

    def _parse_dehydrated_since(self, raw) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except Exception:
            return None

    def _load_player_thirst(self, player) -> int:
        xuid = self._get_player_xuid(player)
        row = self.db_manager.query_one(
            "SELECT thirst, dehydrated_since FROM player_thirst WHERE xuid=?",
            (xuid,),
        )
        if row is None:
            row = self.db_manager.query_one("SELECT thirst FROM player_thirst WHERE xuid=?", (xuid,))
        if row is None:
            self.player_xuid_to_thirst[xuid] = self.thirst_initial
            self.player_xuid_to_dehydrated_since[xuid] = None
            self.db_manager.insert("player_thirst", {
                "xuid": xuid,
                "player_name": player.name,
                "thirst": self.thirst_initial,
                "dehydrated_since": None,
                "updated_at": datetime.datetime.utcnow().isoformat()
            })
        else:
            raw_thirst = int(row["thirst"])
            clamped = self._clamp_thirst(raw_thirst)
            self.player_xuid_to_thirst[xuid] = clamped
            if clamped != raw_thirst:
                self._safe_log(
                    'warning',
                    f"[ARCRealisticSurvival] 玩家 {player.name} 口渴值 {raw_thirst} 已修正为 {clamped}",
                )
            self.player_xuid_to_dehydrated_since[xuid] = self._parse_dehydrated_since(
                row.get("dehydrated_since")
            )
        return self.player_xuid_to_thirst[xuid]

    def _persist_player_thirst(self, player) -> None:
        """仅退出/关服/死亡等关键点调用；运行时数值只在内存。"""
        try:
            xuid = self._get_player_xuid(player)
            thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            since = self.player_xuid_to_dehydrated_since.get(xuid)
            self.db_manager.upsert("player_thirst", {
                "xuid": xuid,
                "player_name": player.name,
                "thirst": thirst,
                "dehydrated_since": float(since) if since is not None else None,
                "updated_at": datetime.datetime.utcnow().isoformat()
            })
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] persist thirst error: {e}")

    def _apply_thirst_delta(self, player, delta: int, reason: str = "", notify: bool = True) -> int:
        if not self._is_survival_like(player):
            return int(self.player_xuid_to_thirst.get(self._get_player_xuid(player), self.thirst_initial))
        xuid = self._get_player_xuid(player)
        current = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        new_val = self._clamp_thirst(current + delta)
        self.player_xuid_to_thirst[xuid] = new_val
        # 仅当口渴度整数值确实变化时才提示；物品效果链路 notify=False，由上层统一 toast
        if new_val != current:
            if notify:
                msg = self.language_manager.GetText("THIRST_VALUE") or "当前口渴值: {value}"
                player.send_popup(msg.replace("{value}", str(new_val)))
            self._push_sidebar_for_player(player)
        self._apply_thirst_movement_modifier(player)
        self._sync_dehydration_state(player)
        return new_val

    def _reset_player_thirst(self, player) -> None:
        if not self._is_survival_like(player):
            return
        xuid = self._get_player_xuid(player)
        self.player_xuid_to_thirst[xuid] = self.thirst_initial
        self.player_xuid_to_dehydrated_since[xuid] = None
        self._persist_player_thirst(player)
        self._apply_thirst_movement_modifier(player)
        self._push_sidebar_for_player(player)

    def _notify_dehydrated(self, player) -> None:
        title = self.language_manager.GetText("THIRST_DEHYDRATED_TITLE") or "严重脱水"
        content = (
            self.language_manager.GetText("THIRST_DEHYDRATED_WARNING")
            or "口渴值已耗尽，超过一小时将致命！"
        )
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass

    def _kill_from_dehydration(self, player) -> None:
        title = self.language_manager.GetText("THIRST_DEHYDRATED_DEATH_TITLE") or "严重脱水"
        content = self.language_manager.GetText("THIRST_DEHYDRATED_DEATH") or "你因严重脱水而死。"
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass
        effect = resolve_effect_type("instant_damage")
        apply_mob_effect(player, effect, 1, 255, particles=True, icon=True)

    def _sync_dehydration_state(self, player) -> None:
        """口渴 <= 0 时记录起始时间；持续超过 thirst_fatal_seconds 则瞬间伤害 255。"""
        if not self._is_survival_like(player):
            return
        xuid = self._get_player_xuid(player)
        thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        if thirst <= 0:
            started = self.player_xuid_to_dehydrated_since.get(xuid)
            if started is None:
                self.player_xuid_to_dehydrated_since[xuid] = time.time()
                self._notify_dehydrated(player)
                return
            try:
                elapsed = time.time() - float(started)
            except Exception:
                self.player_xuid_to_dehydrated_since[xuid] = time.time()
                return
            if elapsed >= float(self.thirst_fatal_seconds):
                self._kill_from_dehydration(player)
        elif self.player_xuid_to_dehydrated_since.get(xuid) is not None:
            self.player_xuid_to_dehydrated_since[xuid] = None

    def _start_survival_timer(self) -> None:
        """统一 20-tick（1 秒）定时器：口渴衰减、感染增减/满值 kill、营养衰减。"""
        try:
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            now = time.time()
            self._last_thirst_run = now
            self._last_infection_run = now
            self._last_nutrition_run = now

            def tick():
                try:
                    now_ts = time.time()
                    do_thirst = (now_ts - self._last_thirst_run) >= max(1, int(self.thirst_tick_seconds))
                    do_infection = (
                        self._is_infection_enabled()
                        and (now_ts - self._last_infection_run) >= max(1, int(
                            getattr(self.zombie_virus_manager, "infection_tick_seconds", 12)
                        ))
                    )
                    do_nutrition = (
                        self.nutrition_manager is not None
                        and (now_ts - self._last_nutrition_run) >= max(1, int(
                            getattr(self.nutrition_manager, "nutrition_tick_seconds", 30)
                        ))
                    )
                    if do_thirst:
                        self._last_thirst_run = now_ts
                    if do_infection:
                        self._last_infection_run = now_ts
                    if do_nutrition:
                        self._last_nutrition_run = now_ts

                    infection_period = max(
                        1.0,
                        float(getattr(self.zombie_virus_manager, "infection_tick_seconds", 12) or 12),
                    )
                    growth = float(
                        getattr(self.zombie_virus_manager, "infection_growth_per_minute", 0) or 0
                    ) * (infection_period / 60.0)
                    decay = float(
                        getattr(self.zombie_virus_manager, "infection_decay_per_minute", 0) or 0
                    ) * (infection_period / 60.0)

                    for player in self.server.online_players:
                        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
                            continue
                        xuid = self._get_player_xuid(player)
                        if do_thirst:
                            current_thirst = int(
                                self.player_xuid_to_thirst.get(xuid, self.thirst_initial)
                            )
                            if current_thirst <= 0:
                                self._sync_dehydration_state(player)
                            else:
                                base_decay = self.thirst_decay_per_tick
                                moving_flag = getattr(player, '_arc_moving_flag', False)
                                tick_decay = base_decay if not moving_flag else int(
                                    math.ceil(base_decay * self.thirst_moving_multiplier)
                                )
                                if tick_decay > 0:
                                    # 仅在口渴数值变化时重算移速（同段位因子不变则 _set_speed_factor 直接跳过）
                                    self._apply_thirst_delta(player, -tick_decay, reason="timer")
                                else:
                                    self._sync_dehydration_state(player)
                            if hasattr(player, '_arc_moving_flag'):
                                try:
                                    delattr(player, '_arc_moving_flag')
                                except Exception:
                                    pass
                        # 口渴为 0 时每秒检查脱水致死（不重挂移速）
                        if int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial)) <= 0:
                            self._sync_dehydration_state(player)

                        if self._is_infection_enabled() and self.zombie_virus_manager is not None:
                            if do_infection:
                                self.zombie_virus_manager.tick_growth_decay(player, growth, decay)
                            # 满值每秒检测：每次都 kill，死亡才清零
                            self.zombie_virus_manager.tick_max_infection(player)

                        # 骨裂：每秒自动痊愈检查 + 移动掉血（读取并清空本秒移动标记）
                        if self.fracture_manager is not None:
                            moved_second = getattr(player, '_arc_moved_second', False)
                            if hasattr(player, '_arc_moved_second'):
                                try:
                                    delattr(player, '_arc_moved_second')
                                except Exception:
                                    pass
                            self.fracture_manager.tick_second(player, moved_second)

                        if do_nutrition and self.nutrition_manager is not None:
                            self.nutrition_manager.tick_decay_for_player(player)
                except Exception as e:
                    self._safe_log('error', f"[ARCRealisticSurvival] survival timer error: {e}")

            scheduler = self.server.scheduler
            self.thirst_task = scheduler.run_task(self, tick, 20, 20)
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] start survival timer error: {e}")

    # 生存-口渴系统：事件
    @event_handler()
    def on_player_join(self, event: PlayerJoinEvent):
        player = event.player
        self._load_player_thirst(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_join(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_player_join(player)
        if self.fracture_manager is not None:
            self.fracture_manager.load_player(player)
        if self._is_survival_like(player):
            # 进服重新同步移速因子（属性核心按 0.10 基线绝对重算，防上次残留）
            self._apply_thirst_movement_modifier(player)
            self._sync_dehydration_state(player)
        else:
            self._enter_non_survival_mode(player)
        self._push_sidebar_for_player(player)

    @event_handler()
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        self._restore_snapshot_before_persist(player)
        self._persist_player_thirst(player)
        self._clear_thirst_movement_modifier(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_quit(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_player_quit(player)
        if self.fracture_manager is not None:
            self.fracture_manager.on_player_quit(player)

    @event_handler()
    def on_player_game_mode_change(self, event: PlayerGameModeChangeEvent):
        player = event.player
        new_mode = event.new_game_mode
        now_survival = new_mode == GameMode.SURVIVAL or new_mode == GameMode.ADVENTURE
        xuid = self._get_player_xuid(player)
        if now_survival:
            if xuid in self._creative_snapshots:
                self._leave_non_survival_mode(player)
            else:
                self._apply_thirst_movement_modifier(player)
                self._sync_dehydration_state(player)
                if self.nutrition_manager is not None:
                    self.nutrition_manager._apply_persistent_symptoms(player)
        else:
            self._enter_non_survival_mode(player)

    @event_handler()
    def on_actor_damage(self, event: ActorDamageEvent):
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_actor_damage(event)
        if self.fracture_manager is not None:
            self.fracture_manager.on_actor_damage(event)

    @event_handler()
    def on_player_death(self, event: PlayerDeathEvent):
        player = event.player
        xuid = self._get_player_xuid(player)
        # 任意死亡立刻清零感染（内存+数据库）；创造快照里的感染也清掉，防止切回生存又恢复
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.reset_on_death(player)
        # 死亡清除骨裂（骨折保留，需特效物品治疗）
        if self.fracture_manager is not None:
            self.fracture_manager.reset_on_death(player)
        snap = self._creative_snapshots.get(xuid)
        if snap is not None:
            snap["infection"] = 0.0
        if not self._is_survival_like(player):
            return
        self._reset_player_thirst(player)
        if snap is not None:
            snap["thirst"] = self.thirst_initial
            snap["dehydrated_since"] = None

    @event_handler()
    def on_player_respawn(self, event: PlayerRespawnEvent):
        player = event.player
        if self._is_survival_like(player):
            if self.zombie_virus_manager is not None:
                self.zombie_virus_manager.reset_on_respawn(player)
            self._apply_thirst_movement_modifier(player)
            if self.nutrition_manager is not None:
                self.nutrition_manager.on_player_respawn(player)
            if self.fracture_manager is not None:
                self.fracture_manager.on_player_respawn(player)
        else:
            self._enter_non_survival_mode(player)

    @event_handler()
    def on_player_move(self, event: PlayerMoveEvent):
        try:
            player = event.player
            if not self._is_survival_like(player):
                return
            # _arc_moving_flag：口渴按衰减周期判断是否移动；_arc_moved_second：骨裂每秒判断
            setattr(player, '_arc_moving_flag', True)
            setattr(player, '_arc_moved_second', True)
        except Exception:
            pass

    @event_handler()
    def on_player_item_consume(self, event: PlayerItemConsumeEvent):
        """统一进食入口：口渴/营养/感染/buffs 全部由 consume_items 配置一次性应用。"""
        try:
            player = event.player
            if not self._is_survival_like(player):
                return
            item = event.item
            if item is None:
                self._log_consume_debug(
                    f"player={player.name} item=None，跳过（事件未携带物品栈）",
                )
                return
            if self.consume_manager is None:
                return
            self.consume_manager.on_player_consume(player, item)
        except Exception as e:
            self._safe_log('error', f"[ARS] consume event error: {e}")
