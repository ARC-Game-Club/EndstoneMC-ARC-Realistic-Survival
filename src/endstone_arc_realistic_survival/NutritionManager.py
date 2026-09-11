import datetime
import random
import time
from typing import Any, Callable, Optional

from endstone import GameMode
from endstone.attribute import Attribute, AttributeModifier

from .effect_compat import EffectType, apply_mob_effect, remove_mob_effect


NUTRIENT_KEYS = ("vitamin_a", "vitamin_c", "iron", "protein")

NUTRIENT_LABELS = {
    "vitamin_a": "维生素A",
    "vitamin_c": "维生素C",
    "iron": "铁",
    "protein": "蛋白质",
}

DEFICIENCY_NAMES = {
    "vitamin_a": "夜盲症",
    "vitamin_c": "坏血病",
    "iron": "贫血",
    "protein": "肌无力",
}

SEVERITY_LABELS = {
    "healthy": "健康",
    "mild": "轻症",
    "moderate": "中症",
    "severe": "重症",
}

MODIFIER_IDS = (
    "ars:anemia_health",
    "ars:anemia_exhaustion",
    "ars:myasthenia_attack",
)

DEFAULT_NUTRITION_ITEMS = [
    ("minecraft:carrot", "胡萝卜", 25, 2, 1, 1),
    ("minecraft:golden_carrot", "金胡萝卜", 40, 3, 2, 2),
    ("minecraft:potato", "土豆", 2, 5, 1, 3),
    ("minecraft:baked_potato", "烤土豆", 3, 6, 2, 4),
    ("minecraft:beetroot", "甜菜根", 3, 8, 2, 2),
    ("minecraft:beetroot_soup", "甜菜汤", 5, 10, 3, 5),
    ("minecraft:apple", "苹果", 2, 12, 1, 1),
    ("minecraft:melon_slice", "西瓜片", 1, 15, 1, 1),
    ("minecraft:sweet_berries", "甜浆果", 2, 18, 1, 1),
    ("minecraft:glow_berries", "发光浆果", 4, 10, 1, 2),
    ("minecraft:chorus_fruit", "紫颂果", 3, 8, 1, 2),
    ("minecraft:bread", "面包", 1, 3, 2, 6),
    ("minecraft:cooked_beef", "熟牛肉", 2, 2, 18, 20),
    ("minecraft:cooked_porkchop", "熟猪排", 2, 2, 16, 18),
    ("minecraft:cooked_mutton", "熟羊肉", 2, 2, 14, 16),
    ("minecraft:cooked_chicken", "熟鸡肉", 2, 3, 12, 16),
    ("minecraft:cooked_cod", "熟鳕鱼", 3, 4, 10, 14),
    ("minecraft:cooked_salmon", "熟鲑鱼", 3, 5, 12, 15),
    ("minecraft:beef", "生牛肉", 1, 1, 10, 12),
    ("minecraft:porkchop", "生猪排", 1, 1, 9, 11),
    ("minecraft:mutton", "生羊肉", 1, 1, 8, 10),
    ("minecraft:chicken", "生鸡肉", 1, 2, 7, 10),
    ("minecraft:cod", "生鳕鱼", 2, 3, 6, 9),
    ("minecraft:salmon", "生鲑鱼", 2, 4, 7, 10),
    ("minecraft:egg", "鸡蛋", 4, 2, 4, 10),
    ("minecraft:rabbit_stew", "兔肉煲", 5, 8, 14, 16),
    ("minecraft:mushroom_stew", "蘑菇煲", 3, 6, 5, 8),
    ("minecraft:pumpkin_pie", "南瓜派", 8, 5, 3, 5),
    ("minecraft:cookie", "曲奇", 1, 2, 2, 3),
    ("minecraft:golden_apple", "金苹果", 10, 15, 8, 8),
]

WARN_MESSAGES = {
    ("vitamin_a", "mild"): ("营养提示", "你开始感到夜间视物有些模糊…"),
    ("vitamin_a", "moderate"): ("夜盲症", "黑暗中你的视野明显变差了。"),
    ("vitamin_a", "severe"): ("夜盲症", "几乎无法在夜间看清任何东西！"),
    ("vitamin_c", "mild"): ("营养提示", "你感到牙龈有些不适，恢复变慢。"),
    ("vitamin_c", "moderate"): ("坏血病", "身体出现瘀伤，虚弱感加剧。"),
    ("vitamin_c", "severe"): ("坏血病", "坏血病发作，你持续感到虚弱与疼痛。"),
    ("iron", "mild"): ("营养提示", "你感到有些乏力，体力似乎下降了。"),
    ("iron", "moderate"): ("贫血", "贫血让你更容易饥饿，最大生命下降。"),
    ("iron", "severe"): ("贫血", "严重贫血使你极度虚弱！"),
    ("protein", "mild"): ("营养提示", "你的肌肉力量似乎有所减弱。"),
    ("protein", "moderate"): ("肌无力", "肌无力让你攻击与挖掘都变慢了。"),
    ("protein", "severe"): ("肌无力", "严重肌无力，你几乎使不上劲！"),
}

RECOVER_MESSAGES = {
    "vitamin_a": ("营养恢复", "夜盲症状有所缓解。"),
    "vitamin_c": ("营养恢复", "坏血病症状有所缓解。"),
    "iron": ("营养恢复", "贫血症状有所缓解。"),
    "protein": ("营养恢复", "肌无力症状有所缓解。"),
}


class NutritionManager:
    """玩家营养学：四种营养素 0-100，缺素触发对应病症。"""

    def __init__(
        self,
        plugin,
        db_manager,
        setting_manager,
        log_fn: Callable[[str, str], None],
        get_xuid_fn: Callable[[Any], str],
        collect_item_identities_fn: Callable[[Any], list],
    ):
        self.plugin = plugin
        self.db_manager = db_manager
        self.setting_manager = setting_manager
        self._log = log_fn
        self._get_xuid = get_xuid_fn
        self._collect_item_identities = collect_item_identities_fn

        self.nutrition_items_map: dict[str, dict] = {}
        self.player_nutrition: dict[str, dict[str, int]] = {}
        self.player_severity: dict[str, dict[str, str]] = {}
        self.player_last_consume: dict[str, dict] = {}
        self.player_last_warn: dict[str, float] = {}

        self.nutrition_tick_seconds = 300
        self.nutrition_decay_per_tick = 1
        self.nutrition_initial = 100
        self.nutrition_min = 0
        self.nutrition_max = 100
        self.nutrition_warn_cooldown_seconds = 300
        self.threshold_healthy = 60
        self.threshold_mild = 30
        self.threshold_moderate = 10
        self.nutrition_task = None

    def load_settings(self) -> None:
        def _get_int(key: str, default: str, minimum: Optional[int] = None) -> int:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            parsed = int(float(str(val).strip()))
            if minimum is not None:
                return max(minimum, parsed)
            return parsed

        self.nutrition_tick_seconds = _get_int("nutrition_tick_seconds", "300", 30)
        self.nutrition_decay_per_tick = _get_int("nutrition_decay_per_tick", "1", 0)
        self.nutrition_initial = _get_int("nutrition_initial", "100")
        self.nutrition_warn_cooldown_seconds = _get_int("nutrition_warn_cooldown_seconds", "300", 30)
        self.threshold_healthy = _get_int("nutrition_threshold_healthy", "60")
        self.threshold_mild = _get_int("nutrition_threshold_mild", "30")
        self.threshold_moderate = _get_int("nutrition_threshold_moderate", "10")

    def ensure_tables(self) -> None:
        player_fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "vitamin_a": "INTEGER NOT NULL DEFAULT 100",
            "vitamin_c": "INTEGER NOT NULL DEFAULT 100",
            "iron": "INTEGER NOT NULL DEFAULT 100",
            "protein": "INTEGER NOT NULL DEFAULT 100",
            "updated_at": "TEXT NOT NULL",
        }
        if self.db_manager.create_table("player_nutrition", player_fields):
            self._log("info", "[ARS] player_nutrition table ready")

        item_fields = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "item_id": "TEXT NOT NULL UNIQUE",
            "item_name": "TEXT",
            "vitamin_a": "INTEGER NOT NULL DEFAULT 0",
            "vitamin_c": "INTEGER NOT NULL DEFAULT 0",
            "iron": "INTEGER NOT NULL DEFAULT 0",
            "protein": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        if self.db_manager.create_table("nutrition_items", item_fields):
            self._log("info", "[ARS] nutrition_items table ready")
            self._seed_default_items_if_empty()

    def _seed_default_items_if_empty(self) -> None:
        try:
            row = self.db_manager.query_one("SELECT COUNT(*) AS cnt FROM nutrition_items")
            if row and int(row.get("cnt", 0)) > 0:
                return
            now = datetime.datetime.utcnow().isoformat()
            for item_id, name, va, vc, fe, pr in DEFAULT_NUTRITION_ITEMS:
                self.db_manager.insert("nutrition_items", {
                    "item_id": item_id,
                    "item_name": name,
                    "vitamin_a": va,
                    "vitamin_c": vc,
                    "iron": fe,
                    "protein": pr,
                    "created_at": now,
                    "updated_at": now,
                })
            self._log("info", f"[ARS] seeded {len(DEFAULT_NUTRITION_ITEMS)} default nutrition_items rows")
        except Exception as e:
            self._log("error", f"[ARS] seed nutrition_items error: {e}")

    def load_items_config(self) -> None:
        self.nutrition_items_map = {}
        count = 0
        try:
            if not self.db_manager.table_exists("nutrition_items"):
                return
            rows = self.db_manager.query_all(
                "SELECT item_id, item_name, vitamin_a, vitamin_c, iron, protein "
                "FROM nutrition_items WHERE item_id IS NOT NULL AND item_id != ''"
            )
            for row in rows:
                item_id = row.get("item_id")
                if not item_id:
                    continue
                cfg = {
                    "item_name": row.get("item_name") or item_id,
                    "vitamin_a": int(row.get("vitamin_a", 0)),
                    "vitamin_c": int(row.get("vitamin_c", 0)),
                    "iron": int(row.get("iron", 0)),
                    "protein": int(row.get("protein", 0)),
                }
                self._register_item_cfg(item_id, cfg)
                count += 1
        except Exception as e:
            self._log("error", f"[ARS] load nutrition_items error: {e}")
        self._log("info", f"[ARS] nutrition items: database={count} rows, lookup keys={len(self.nutrition_items_map)}")

    def _register_item_cfg(self, item_id: str, cfg: dict) -> None:
        raw = str(item_id).strip()
        if not raw:
            return
        upper_full = raw.upper()
        self.nutrition_items_map[upper_full] = cfg
        if ":" in upper_full:
            short_key = upper_full.split(":", 1)[1]
            self.nutrition_items_map[short_key] = cfg

    def find_cfg_for_item(self, item) -> tuple[Optional[dict], Optional[str], Optional[str]]:
        for cand in self._collect_item_identities(item):
            upper_full = cand.upper()
            if upper_full in self.nutrition_items_map:
                return self.nutrition_items_map[upper_full], cand, upper_full
            if ":" in upper_full:
                short_key = upper_full.split(":", 1)[1]
                if short_key in self.nutrition_items_map:
                    return self.nutrition_items_map[short_key], cand, short_key
        return None, None, None

    def _clamp(self, value: int) -> int:
        return max(self.nutrition_min, min(self.nutrition_max, value))

    def _default_nutrition(self) -> dict[str, int]:
        init = self._clamp(self.nutrition_initial)
        return {k: init for k in NUTRIENT_KEYS}

    def get_severity(self, value: int) -> str:
        if value >= self.threshold_healthy:
            return "healthy"
        if value >= self.threshold_mild:
            return "mild"
        if value >= self.threshold_moderate:
            return "moderate"
        return "severe"

    def load_player(self, player) -> dict[str, int]:
        xuid = self._get_xuid(player)
        row = self.db_manager.query_one(
            "SELECT vitamin_a, vitamin_c, iron, protein FROM player_nutrition WHERE xuid=?",
            (xuid,),
        )
        if row is None:
            data = self._default_nutrition()
            self.db_manager.insert("player_nutrition", {
                "xuid": xuid,
                "player_name": player.name,
                "vitamin_a": data["vitamin_a"],
                "vitamin_c": data["vitamin_c"],
                "iron": data["iron"],
                "protein": data["protein"],
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        else:
            data = {k: self._clamp(int(row[k])) for k in NUTRIENT_KEYS}
        self.player_nutrition[xuid] = data
        self.player_severity[xuid] = {k: self.get_severity(data[k]) for k in NUTRIENT_KEYS}
        self._apply_persistent_symptoms(player)
        return data

    def persist_player(self, player) -> None:
        """仅退出/关服/管理命令调用；运行时数值只在内存。"""
        try:
            xuid = self._get_xuid(player)
            data = self.player_nutrition.get(xuid, self._default_nutrition())
            self.db_manager.upsert("player_nutrition", {
                "xuid": xuid,
                "player_name": player.name,
                "vitamin_a": data["vitamin_a"],
                "vitamin_c": data["vitamin_c"],
                "iron": data["iron"],
                "protein": data["protein"],
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            self._log("error", f"[ARS] persist nutrition error: {e}")

    def apply_deltas(self, player, deltas: dict[str, int], item_label: str = "") -> dict[str, int]:
        xuid = self._get_xuid(player)
        current = dict(self.player_nutrition.get(xuid, self._default_nutrition()))
        old_severity = dict(self.player_severity.get(xuid, {}))

        for key in NUTRIENT_KEYS:
            if key in deltas:
                current[key] = self._clamp(current[key] + int(deltas[key]))

        self.player_nutrition[xuid] = current
        new_severity = {k: self.get_severity(current[k]) for k in NUTRIENT_KEYS}
        self.player_severity[xuid] = new_severity

        if item_label:
            self.player_last_consume[xuid] = {
                "item": item_label,
                "deltas": {k: int(deltas.get(k, 0)) for k in NUTRIENT_KEYS if int(deltas.get(k, 0)) != 0},
                "at": datetime.datetime.utcnow().isoformat(),
            }

        self._notify_severity_changes(player, old_severity, new_severity)
        self._apply_persistent_symptoms(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        return current

    def set_nutrient(self, player, nutrient: str, value: int) -> dict[str, int]:
        key = nutrient.lower().strip()
        if key not in NUTRIENT_KEYS:
            raise ValueError(f"unknown nutrient: {nutrient}")
        xuid = self._get_xuid(player)
        current = dict(self.player_nutrition.get(xuid, self._default_nutrition()))
        old_severity = dict(self.player_severity.get(xuid, {}))
        current[key] = self._clamp(int(value))
        self.player_nutrition[xuid] = current
        new_severity = {k: self.get_severity(current[k]) for k in NUTRIENT_KEYS}
        self.player_severity[xuid] = new_severity
        self._notify_severity_changes(player, old_severity, new_severity)
        self._apply_persistent_symptoms(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        return current

    def _can_warn(self, xuid: str) -> bool:
        last = self.player_last_warn.get(xuid, 0.0)
        return (time.time() - last) >= self.nutrition_warn_cooldown_seconds

    def _mark_warn(self, xuid: str) -> None:
        self.player_last_warn[xuid] = time.time()

    def _notify_severity_changes(
        self,
        player,
        old: dict[str, str],
        new: dict[str, str],
    ) -> None:
        xuid = self._get_xuid(player)
        if not self._can_warn(xuid):
            return
        for key in NUTRIENT_KEYS:
            old_lv = old.get(key, "healthy")
            new_lv = new.get(key, "healthy")
            if old_lv == new_lv:
                continue
            if new_lv == "healthy" and old_lv != "healthy":
                title, content = RECOVER_MESSAGES[key]
                try:
                    player.send_toast(title, content)
                except Exception:
                    player.send_message(f"[{title}] {content}")
                self._mark_warn(xuid)
                return
            if new_lv != "healthy":
                msg = WARN_MESSAGES.get((key, new_lv))
                if msg:
                    title, content = msg
                    try:
                        player.send_toast(title, content)
                    except Exception:
                        player.send_message(f"[{title}] {content}")
                    self._mark_warn(xuid)
                    return

    def _remove_modifier_safe(self, player, modifier_id: str) -> None:
        get_attr = getattr(player, "get_attribute", None)
        if get_attr is None:
            return
        for attr_name in (
            Attribute.HEALTH,
            Attribute.PLAYER_EXHAUSTION,
            Attribute.ATTACK_DAMAGE,
        ):
            try:
                inst = get_attr(attr_name)
                if inst is None:
                    continue
                try:
                    inst.remove_modifier(modifier_id)
                except Exception:
                    for existing in list(getattr(inst, "modifiers", []) or []):
                        if getattr(existing, "name", None) == modifier_id:
                            inst.remove_modifier(existing)
                            break
            except Exception:
                pass

    def clear_symptoms(self, player) -> None:
        for mid in MODIFIER_IDS:
            self._remove_modifier_safe(player, mid)
        for eff in (
            getattr(EffectType, "WEAKNESS", None),
            getattr(EffectType, "POISON", None),
            getattr(EffectType, "MINING_FATIGUE", None),
            getattr(EffectType, "BLINDNESS", None),
        ):
            if eff is None:
                continue
            try:
                remove_mob_effect(player, eff)
            except Exception:
                pass

    def heal_to(self, player, value: int = 80) -> dict[str, int]:
        """治愈缺素病症：四项营养设为指定值并清除症状（不影响感染）。"""
        xuid = self._get_xuid(player)
        target = self._clamp(int(value))
        old_severity = dict(self.player_severity.get(xuid, {}))
        data = {k: target for k in NUTRIENT_KEYS}
        self.player_nutrition[xuid] = data
        new_severity = {k: self.get_severity(target) for k in NUTRIENT_KEYS}
        self.player_severity[xuid] = new_severity
        self.clear_symptoms(player)
        self._apply_persistent_symptoms(player)
        self._notify_severity_changes(player, old_severity, new_severity)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        return data

    def _apply_persistent_symptoms(self, player) -> None:
        xuid = self._get_xuid(player)
        data = self.player_nutrition.get(xuid, self._default_nutrition())
        self.clear_symptoms(player)

        iron_sev = self.get_severity(data["iron"])
        if iron_sev != "healthy":
            health_penalty = {"mild": -2.0, "moderate": -4.0, "severe": -6.0}[iron_sev]
            exhaustion_bonus = {"mild": 0.15, "moderate": 0.3, "severe": 0.5}[iron_sev]
            self._add_modifier(player, Attribute.HEALTH, "ars:anemia_health", health_penalty, AttributeModifier.ADD)
            self._add_modifier(
                player, Attribute.PLAYER_EXHAUSTION, "ars:anemia_exhaustion", exhaustion_bonus, AttributeModifier.ADD
            )

        protein_sev = self.get_severity(data["protein"])
        if protein_sev != "healthy":
            attack_mul = {"mild": -0.2, "moderate": -0.3, "severe": -0.4}[protein_sev]
            self._add_modifier(
                player,
                Attribute.ATTACK_DAMAGE,
                "ars:myasthenia_attack",
                attack_mul,
                AttributeModifier.MULTIPLY_BASE,
            )
            amp = {"mild": 0, "moderate": 0, "severe": 1}[protein_sev]
            try:
                apply_mob_effect(
                    player,
                    EffectType.MINING_FATIGUE,
                    220,
                    amp,
                    ambient=True,
                    particles=False,
                    icon=False,
                )
            except Exception:
                pass

    def _add_modifier(self, player, attribute, modifier_id: str, amount: float, operation) -> None:
        try:
            get_attr = getattr(player, "get_attribute", None)
            if get_attr is None:
                return
            inst = get_attr(attribute)
            if inst is None:
                return
            self._remove_modifier_safe(player, modifier_id)
            mod = AttributeModifier(modifier_id, amount, operation)
            if hasattr(inst, "add_transient_modifier"):
                inst.add_transient_modifier(mod)
            else:
                inst.add_modifier(mod)
        except Exception as e:
            self._log("error", f"[ARS] add modifier {modifier_id} error: {e}")

    def _is_night(self) -> bool:
        try:
            level = self.plugin.server.level
            if level is None:
                return False
            t = int(level.time) % 24000
            return 13000 <= t <= 23000
        except Exception:
            return False

    def _tick_symptoms_for_player(self, player) -> None:
        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
            return
        xuid = self._get_xuid(player)
        data = self.player_nutrition.get(xuid)
        if not data:
            return

        va_sev = self.get_severity(data["vitamin_a"])
        if va_sev != "healthy" and self._is_night():
            chance = {"mild": 0.08, "moderate": 0.18, "severe": 0.35}[va_sev]
            if random.random() < chance:
                low, high = {"mild": (40, 80), "moderate": (60, 100), "severe": (100, 200)}[va_sev]
                try:
                    apply_mob_effect(
                        player,
                        EffectType.BLINDNESS,
                        random.randint(low, high),
                        0,
                        ambient=True,
                        particles=False,
                        icon=False,
                    )
                except Exception:
                    pass

        vc_sev = self.get_severity(data["vitamin_c"])
        if vc_sev != "healthy":
            chance = {"mild": 0.12, "moderate": 0.22, "severe": 0.35}[vc_sev]
            if random.random() < chance:
                try:
                    apply_mob_effect(
                        player,
                        EffectType.WEAKNESS,
                        120,
                        {"mild": 0, "moderate": 0, "severe": 1}[vc_sev],
                        ambient=True,
                        particles=False,
                        icon=True,
                    )
                except Exception:
                    pass
                if vc_sev in ("moderate", "severe"):
                    try:
                        hp = int(player.health)
                        if hp > 2:
                            player.health = hp - 1
                    except Exception:
                        pass
                if vc_sev == "severe" and random.random() < 0.5:
                    try:
                        apply_mob_effect(
                            player,
                            EffectType.POISON,
                            60,
                            0,
                            ambient=True,
                            particles=True,
                            icon=True,
                        )
                    except Exception:
                        pass

        self._apply_persistent_symptoms(player)

    def apply_healthy_bypass(self, player) -> None:
        """创造/旁观：内存设为满营养并清症状，不写库。"""
        xuid = self._get_xuid(player)
        data = self._default_nutrition()
        self.player_nutrition[xuid] = data
        self.player_severity[xuid] = {k: "healthy" for k in NUTRIENT_KEYS}
        self.clear_symptoms(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def restore_nutrition(self, player, data: dict[str, int]) -> None:
        """从快照恢复真实营养并重新挂症状。"""
        xuid = self._get_xuid(player)
        restored = {k: self._clamp(int(data.get(k, self.nutrition_initial))) for k in NUTRIENT_KEYS}
        self.player_nutrition[xuid] = restored
        self.player_severity[xuid] = {k: self.get_severity(restored[k]) for k in NUTRIENT_KEYS}
        self._apply_persistent_symptoms(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def on_player_join(self, player) -> None:
        self.load_player(player)
        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
            # 真实值由主插件快照；此处先挂症状再由主插件切到 bypass
            pass

    def on_player_quit(self, player) -> None:
        self.clear_symptoms(player)
        self.persist_player(player)
        xuid = self._get_xuid(player)
        self.player_nutrition.pop(xuid, None)
        self.player_severity.pop(xuid, None)

    def on_player_respawn(self, player) -> None:
        """重生后按数据库中的营养值重新挂症状（死亡不清营养）。"""
        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
            self.apply_healthy_bypass(player)
            return
        xuid = self._get_xuid(player)
        if xuid not in self.player_nutrition:
            self.load_player(player)
        else:
            self._apply_persistent_symptoms(player)

    def on_player_consume(self, player, item) -> bool:
        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
            return False
        cfg, _, lookup_key = self.find_cfg_for_item(item)
        if cfg is None:
            return False
        deltas = {k: int(cfg.get(k, 0)) for k in NUTRIENT_KEYS}
        if all(v == 0 for v in deltas.values()):
            return False
        label = cfg.get("item_name") or lookup_key or "未知食物"
        self.apply_deltas(player, deltas, item_label=label)
        self._log(
            "info",
            f"[ARS][nutrition] player={player.name} item={label} deltas={deltas}",
        )
        return True

    def tick_decay_for_player(self, player) -> None:
        """统一定时器调用：衰减营养并刷症状。"""
        try:
            xuid = self._get_xuid(player)
            data = self.player_nutrition.get(xuid)
            if data is None:
                self.load_player(player)
                data = self.player_nutrition.get(xuid, self._default_nutrition())
            decay = self.nutrition_decay_per_tick
            if decay > 0:
                new_data = {k: self._clamp(data[k] - decay) for k in NUTRIENT_KEYS}
                old_severity = dict(self.player_severity.get(xuid, {}))
                self.player_nutrition[xuid] = new_data
                new_severity = {k: self.get_severity(new_data[k]) for k in NUTRIENT_KEYS}
                self.player_severity[xuid] = new_severity
                self._notify_severity_changes(player, old_severity, new_severity)
            self._tick_symptoms_for_player(player)
            try:
                self.plugin._push_sidebar_for_player(player)
            except Exception:
                pass
        except Exception as e:
            self._log("error", f"[ARS] nutrition tick error: {e}")

    def start_timer(self) -> None:
        """由主插件 20-tick 统一定时器驱动；此处仅清理旧任务。"""
        self.stop_timer()

    def stop_timer(self) -> None:
        if self.nutrition_task is not None:
            try:
                self.nutrition_task.cancel()
            except Exception:
                pass
            self.nutrition_task = None

    def get_status_lines(self, player) -> list[str]:
        xuid = self._get_xuid(player)
        data = self.player_nutrition.get(xuid, self._default_nutrition())
        lines = ["=== 营养状态 ==="]
        for key in NUTRIENT_KEYS:
            val = data[key]
            sev = self.get_severity(val)
            lines.append(
                f"{NUTRIENT_LABELS[key]}: {val}/100 [{SEVERITY_LABELS[sev]}]"
                + (f" → {DEFICIENCY_NAMES[key]}" if sev != "healthy" else "")
            )
        last = self.player_last_consume.get(xuid)
        if last:
            parts = [f"{NUTRIENT_LABELS[k]}+{v}" for k, v in last.get("deltas", {}).items()]
            lines.append(f"最近进食: {last.get('item', '?')} ({', '.join(parts) or '无营养'})")
        else:
            lines.append("最近进食: 无记录")
        return lines

    def get_food_catalog_lines(self, limit: int = 20) -> list[str]:
        lines = ["=== 食物营养表（节选）==="]
        try:
            rows = self.db_manager.query_all(
                "SELECT item_name, item_id, vitamin_a, vitamin_c, iron, protein "
                "FROM nutrition_items ORDER BY item_name LIMIT ?",
                (limit,),
            )
            for row in rows:
                name = row.get("item_name") or row.get("item_id")
                lines.append(
                    f"{name}: A+{row['vitamin_a']} C+{row['vitamin_c']} "
                    f"Fe+{row['iron']} P+{row['protein']}"
                )
        except Exception:
            lines.append("（无法读取食物表）")
        return lines
