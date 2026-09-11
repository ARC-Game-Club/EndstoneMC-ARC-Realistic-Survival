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
    ):
        self.plugin = plugin
        self.db_manager = db_manager
        self.setting_manager = setting_manager
        self._log = log_fn
        self._get_xuid = get_xuid_fn

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
