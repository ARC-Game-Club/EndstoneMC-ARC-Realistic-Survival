"""骨裂/骨折系统：高空坠落重摔有概率骨裂，直接摔死则进入骨折。

骨裂（轻，level 1）：坠落伤害 > 阈值（默认 5）时掷概率
    （base + per_damage × (伤害 − 阈值)，封顶 max，百分比）；
    当前移速基础上乘 speed_multiplier（默认 0.75），移动时每秒掉血（默认 1，保底 min_hp 不致死）；
    fracture_heal_seconds（默认 300 秒）后自动痊愈；死亡清除；/heal 或特效物品可治疗。
骨折（重，level 2）：坠落伤害直接致死（伤害 ≥ 现有生命）时进入；
    当前移速基础上乘 severe_speed_multiplier（默认 0.50），不掉血、不自动痊愈；
    死亡不清除（重生继续），仅特效物品（consume_items 配 cure_fracture）或 /heal 治疗。

移速因子由主插件的全局移速管理器（_set_speed_factor 相对叠加）统一连乘：
基速倍率 × 口渴分段 × 骨裂/骨折因子；骨裂与骨折互斥，单状态 level 区分。
状态持久化在 player_fracture 表，掉线重进不丢失。
"""

import datetime
import random
import time
from typing import Any, Callable

from endstone import GameMode


class FractureManager:
    """骨裂/骨折：单状态按 xuid 管理（level 1=骨裂、2=骨折），移速因子由主插件移速重算时叠加。"""

    LEVEL_CRACK = 1  # 骨裂
    LEVEL_BREAK = 2  # 骨折

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

        # xuid -> {"level": int, "fractured_at": float, "heal_at": float}
        self.fractured: dict[str, dict] = {}

        self.enabled = True
        self.fall_damage_min = 5.0
        self.chance_base_percent = 10.0
        self.chance_per_damage_percent = 5.0
        self.chance_max_percent = 80.0
        self.speed_multiplier = 0.75
        self.severe_speed_multiplier = 0.50
        self.heal_seconds = 300                  # 骨裂基础时长（最低坠落档）
        self.crack_per_damage_seconds = 30.0     # 每点坠落伤害延长
        self.crack_duration_max = 900.0          # 骨裂时长上限（也是夹板降级后的时长）
        self.drain_hp_per_second = 1
        self.drain_min_hp = 1

    def load_settings(self) -> None:
        def _get_bool(key: str, default: str) -> bool:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            return str(val).strip().lower() in ("1", "true", "yes", "on")

        def _get_float(key: str, default: str, minimum: float = None, maximum: float = None) -> float:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            parsed = float(val)
            if minimum is not None:
                parsed = max(minimum, parsed)
            if maximum is not None:
                parsed = min(maximum, parsed)
            return parsed

        def _get_int(key: str, default: str, minimum: int = None) -> int:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            parsed = int(float(str(val).strip()))
            if minimum is not None:
                return max(minimum, parsed)
            return parsed

        self.enabled = _get_bool("fracture_enabled", "true")
        self.fall_damage_min = _get_float("fracture_fall_damage_min", "5", 0)
        self.chance_base_percent = _get_float("fracture_chance_base_percent", "10", 0)
        self.chance_per_damage_percent = _get_float("fracture_chance_per_damage_percent", "5", 0)
        self.chance_max_percent = _get_float("fracture_chance_max_percent", "80", 0)
        self.speed_multiplier = _get_float("fracture_speed_multiplier", "0.75", 0.05, 1.0)
        self.severe_speed_multiplier = _get_float("fracture_severe_speed_multiplier", "0.5", 0.05, 1.0)
        self.heal_seconds = _get_int("fracture_heal_seconds", "300", 30)
        self.crack_per_damage_seconds = _get_float("fracture_crack_per_damage_seconds", "30", 0)
        self.crack_duration_max = _get_float("fracture_crack_duration_max", "900", 30)
        self.drain_hp_per_second = _get_int("fracture_drain_hp_per_second", "1", 0)
        self.drain_min_hp = _get_int("fracture_drain_min_hp", "1", 1)

    def ensure_tables(self) -> None:
        fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "level": "INTEGER NOT NULL DEFAULT 1",
            "fractured_at": "REAL NOT NULL",
            "heal_at": "REAL NOT NULL",
            "no_slow": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "TEXT NOT NULL",
        }
        if self.db_manager.create_table("player_fracture", fields):
            self._log("info", "[ARS] player_fracture table ready")
        if not self.db_manager.ensure_column("player_fracture", "level", "INTEGER NOT NULL DEFAULT 1"):
            self._log("warning", "[ARS] failed to add player_fracture.level")
        if not self.db_manager.ensure_column("player_fracture", "no_slow", "INTEGER NOT NULL DEFAULT 0"):
            self._log("warning", "[ARS] failed to add player_fracture.no_slow")

    # ---------- 查询 ----------

    def is_fractured(self, player) -> bool:
        return self._get_xuid(player) in self.fractured

    def get_level(self, player) -> int:
        state = self.fractured.get(self._get_xuid(player))
        return int(state.get("level", 1)) if state else 0

    def speed_factor_for(self, player) -> float:
        """腿伤移速因子：未受伤=1.0；骨折=severe；骨裂=speed_multiplier（止痛后=1.0）。"""
        if not self.enabled:
            return 1.0
        state = self.fractured.get(self._get_xuid(player))
        if state is None:
            return 1.0
        if int(state.get("level", 1)) >= self.LEVEL_BREAK:
            return float(self.severe_speed_multiplier)
        if int(state.get("no_slow", 0) or 0):
            return 1.0  # 止痛药生效：骨裂不减速，移动掉血保留
        return float(self.speed_multiplier)

    def crack_duration(self, damage: float | None = None) -> float:
        """骨裂自愈时长：基础 + 每点坠落伤害延长，封顶上限。"""
        base = float(self.heal_seconds)
        per = float(self.crack_per_damage_seconds)
        over = 0.0
        if damage is not None:
            over = max(0.0, float(damage) - float(self.fall_damage_min))
        return min(float(self.crack_duration_max), base + per * over)

    @staticmethod
    def _is_fall_damage(raw) -> bool:
        """坠落伤害判定。

        Endstone DamageSource.type 是字符串（坠落为 "fall"，"falling_block" 等不匹配）；
        兼容旧版可能存在的枚举 cause（.name = "Fall"/"FALL"）。
        """
        if raw is None:
            return False
        text = str(getattr(raw, "name", raw)).strip().lower()
        return text == "fall" or text.endswith(":fall")

    def resolve_chance_percent(self, damage: float) -> float:
        over = max(0.0, float(damage) - float(self.fall_damage_min))
        chance = float(self.chance_base_percent) + float(self.chance_per_damage_percent) * over
        return max(0.0, min(float(self.chance_max_percent), chance))

    # ---------- 触发与恢复 ----------

    def on_actor_damage(self, event) -> None:
        if not self.enabled:
            return
        try:
            victim = event.actor
            if not hasattr(victim, "game_mode"):
                return
            if victim.game_mode != GameMode.SURVIVAL and victim.game_mode != GameMode.ADVENTURE:
                return
            source = getattr(event, "damage_source", None)
            # DamageSource.type（字符串，如 "fall"）优先，兼容旧版 cause 枚举
            damage_type = getattr(source, "type", None)
            if damage_type is None:
                damage_type = getattr(source, "cause", None)
            if not self._is_fall_damage(damage_type):
                return
            try:
                damage = float(event.damage)
            except Exception:
                return
            if damage <= float(self.fall_damage_min):
                return
            state = self.fractured.get(self._get_xuid(victim))
            if state is not None and int(state.get("level", 1)) >= self.LEVEL_BREAK:
                return  # 已骨折，无需处理
            try:
                health = float(victim.health)
            except Exception:
                health = None
            if health is not None and damage >= health:
                # 坠落直接致死 → 骨折（死亡不清除，需特殊物品治疗）
                self.apply_fracture(victim, self.LEVEL_BREAK, damage)
                return
            if state is not None:
                return  # 已骨裂，不重复掷概率
            chance = self.resolve_chance_percent(damage)
            if random.random() * 100.0 >= chance:
                return
            self.apply_fracture(victim, self.LEVEL_CRACK, damage, chance)
        except Exception as e:
            self._log("error", f"[ARS] fracture damage handler error: {e}")

    def apply_fracture(self, player, level: int = 1, damage: float = None, chance: float = None) -> None:
        if not self.enabled:
            return
        level = self.LEVEL_BREAK if int(level) >= self.LEVEL_BREAK else self.LEVEL_CRACK
        xuid = self._get_xuid(player)
        now = time.time()
        state = {"level": level, "fractured_at": now, "no_slow": 0}
        if level <= self.LEVEL_CRACK:
            # 骨裂时长随坠落高度（伤害）增长，封顶 crack_duration_max
            state["heal_at"] = now + self.crack_duration(damage)
        else:
            # 骨折不自动痊愈，仅特效物品（夹板）或 /heal 治疗
            state["heal_at"] = 0.0
        self.fractured[xuid] = state
        if level <= self.LEVEL_CRACK:
            title = "骨裂"
            content = (
                f"你重重摔伤了腿：移速降低 {int((1.0 - float(self.speed_multiplier)) * 100)}%，"
                f"移动时会持续掉血！约 {int(self.crack_duration(damage))} 秒后可自行恢复。"
            )
        else:
            title = "骨折！"
            content = (
                f"你摔断了腿：移速降低 {int((1.0 - float(self.severe_speed_multiplier)) * 100)}%。"
                f"需要使用夹板固定或寻求医生帮助。"
            )
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass
        # 立即按「基速 × 口渴分段 × 腿伤」重算移速
        try:
            self.plugin._apply_thirst_movement_modifier(player)
        except Exception:
            pass
        self.persist_player(player)
        level_label = "骨折" if level >= self.LEVEL_BREAK else "骨裂"
        self._log(
            "info",
            f"[ARS][fracture] player={getattr(player, 'name', '?')} level={level}({level_label}) "
            f"damage={damage} chance={chance if chance is None else f'{chance:.0f}%'}",
        )

    def clear_fracture(self, player, notify: bool = False) -> bool:
        xuid = self._get_xuid(player)
        if xuid not in self.fractured:
            return False
        self.fractured.pop(xuid, None)
        try:
            self.db_manager.delete("player_fracture", "xuid=?", (xuid,))
        except Exception as e:
            self._log("error", f"[ARS] delete player_fracture error: {e}")
        try:
            self.plugin._apply_thirst_movement_modifier(player)
        except Exception:
            pass
        if notify:
            try:
                player.send_toast("伤势痊愈", "腿伤已经治疗，行动恢复自如。")
            except Exception:
                try:
                    player.send_message("[腿伤] 腿伤已经治疗，行动恢复自如。")
                except Exception:
                    pass
        return True

    def apply_painkiller(self, player) -> tuple[bool, str]:
        """止痛药：骨裂不再减速（移动掉血保留）；骨折无效。"""
        state = self.fractured.get(self._get_xuid(player))
        if state is None:
            return False, "没有腿伤，无需止痛"
        if int(state.get("level", 1)) >= self.LEVEL_BREAK:
            return False, "骨折需要用夹板固定，止痛药无效"
        state["no_slow"] = 1
        self.persist_player(player)
        try:
            player.send_toast("止痛生效", "骨裂不再减速，但移动时仍会掉血。")
        except Exception:
            pass
        self._log(
            "info",
            f"[ARS][fracture] player={getattr(player, 'name', '?')} painkiller → no_slow",
        )
        return True, ""

    def apply_leg_treatment(self, player) -> tuple[str, str]:
        """夹板：骨折降级为骨裂（按最高坠落档时长）；骨裂直接痊愈。

        返回 (result, msg)：result ∈ cured | downgraded | none。
        """
        xuid = self._get_xuid(player)
        state = self.fractured.get(xuid)
        if state is None:
            return "none", "没有腿伤"
        if int(state.get("level", 1)) >= self.LEVEL_BREAK:
            state.update({
                "level": self.LEVEL_CRACK,
                "no_slow": 0,
                "fractured_at": time.time(),
                "heal_at": time.time() + float(self.crack_duration_max),
            })
            self.persist_player(player)
            try:
                self.plugin._apply_thirst_movement_modifier(player)
            except Exception:
                pass
            try:
                player.send_toast(
                    "夹板固定",
                    f"骨折已被夹板固定为骨裂，约 {int(self.crack_duration_max)} 秒后自愈。",
                )
            except Exception:
                pass
            self._log(
                "info",
                f"[ARS][fracture] player={getattr(player, 'name', '?')} splint: 骨折→骨裂（{int(self.crack_duration_max)}s）",
            )
            return "downgraded", ""
        self.clear_fracture(player, notify=True)
        return "cured", ""

    def on_player_respawn(self, player) -> None:
        """重生时若仍处于骨折，提醒需要治疗。"""
        state = self.fractured.get(self._get_xuid(player))
        if state is None or int(state.get("level", 1)) < self.LEVEL_BREAK:
            return
        try:
            player.send_toast(
                "骨折未愈",
                f"你的腿仍处于骨折状态：移速降低 {int((1.0 - float(self.severe_speed_multiplier)) * 100)}%，"
                f"需要使用特殊药品治疗。",
            )
        except Exception:
            pass

    def tick_second(self, player, moved: bool) -> None:
        """统一定时器每秒调用：骨裂自动痊愈检查 + 移动掉血；骨折不掉血、不自动痊愈。"""
        if not self.enabled:
            return
        try:
            if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
                return
        except Exception:
            return
        xuid = self._get_xuid(player)
        state = self.fractured.get(xuid)
        if state is None:
            return
        if int(state.get("level", 1)) >= self.LEVEL_BREAK:
            return
        if time.time() >= float(state.get("heal_at", 0.0)):
            self.clear_fracture(player, notify=True)
            return
        drain = int(self.drain_hp_per_second)
        if not moved or drain <= 0:
            return
        try:
            hp = int(player.health)
            new_hp = max(int(self.drain_min_hp), hp - drain)
            if new_hp < hp:
                player.health = new_hp
        except Exception as e:
            self._log("error", f"[ARS] fracture drain error: {e}")

    # ---------- 玩家生命周期与持久化 ----------

    def load_player(self, player) -> None:
        xuid = self._get_xuid(player)
        row = self.db_manager.query_one(
            "SELECT level, fractured_at, heal_at, no_slow FROM player_fracture WHERE xuid=?",
            (xuid,),
        )
        if row is None:
            self.fractured.pop(xuid, None)
            return
        try:
            state = {
                "level": int(row.get("level", 1) or 1),
                "fractured_at": float(row["fractured_at"]),
                "heal_at": float(row["heal_at"]),
                "no_slow": int(row.get("no_slow", 0) or 0),
            }
        except Exception:
            self.db_manager.delete("player_fracture", "xuid=?", (xuid,))
            self.fractured.pop(xuid, None)
            return
        if state["level"] <= self.LEVEL_CRACK and state["heal_at"] <= time.time():
            # 骨裂：离线期间已到痊愈时间；骨折不自动痊愈，保留
            self.db_manager.delete("player_fracture", "xuid=?", (xuid,))
            self.fractured.pop(xuid, None)
            return
        self.fractured[xuid] = state

    def persist_player(self, player) -> None:
        try:
            xuid = self._get_xuid(player)
            state = self.fractured.get(xuid)
            if state is None:
                self.db_manager.delete("player_fracture", "xuid=?", (xuid,))
                return
            self.db_manager.upsert("player_fracture", {
                "xuid": xuid,
                "player_name": str(getattr(player, "name", "") or ""),
                "level": int(state.get("level", 1)),
                "fractured_at": float(state["fractured_at"]),
                "heal_at": float(state["heal_at"]),
                "no_slow": int(state.get("no_slow", 0) or 0),
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            self._log("error", f"[ARS] persist fracture error: {e}")

    def on_player_quit(self, player) -> None:
        self.persist_player(player)
        self.fractured.pop(self._get_xuid(player), None)

    def reset_on_death(self, player) -> None:
        """死亡：骨裂（轻）清除；骨折（重）保留——需特效物品或 /heal 治疗。"""
        xuid = self._get_xuid(player)
        state = self.fractured.get(xuid)
        if state is None:
            return
        if int(state.get("level", 1)) <= self.LEVEL_CRACK:
            self.fractured.pop(xuid, None)
            self.persist_player(player)
            try:
                self.plugin._apply_thirst_movement_modifier(player)
            except Exception:
                pass
        else:
            # 骨折保留：重生后继续生效
            self.persist_player(player)
