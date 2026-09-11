import datetime
import random
import time
from typing import Any, Callable, Optional

from endstone import GameMode
from endstone.event import ActorDamageEvent


DEFAULT_INFECTION_SOURCES = [
    ("minecraft:zombie", "僵尸", 5),
    ("minecraft:husk", "尸壳", 5),
    ("minecraft:drowned", "溺尸", 5),
    ("minecraft:zombie_villager", "僵尸村民", 5),
    ("minecraft:zombie_villager_v2", "僵尸村民", 5),
    ("minecraft:zombie_pigman", "僵尸猪灵", 4),
    ("minecraft:", "原版命名空间默认", 2),
]


class ZombieVirusManager:
    """丧尸病毒感染：被配置生物攻击增加感染值，超阈值持续恶化，满值变丧尸。"""

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

        self.entity_rules: dict[str, dict] = {}
        self.namespace_rules: dict[str, dict] = {}
        self.player_infection: dict[str, float] = {}
        self.player_last_warn: dict[str, float] = {}
        self.infection_task = None
        # 已进入「感染满值」阶段的玩家（只负责提示/刷丧尸一次；kill 每次检测都执行）
        self._max_infection_active: set[str] = set()

        self.infection_enabled = False
        self.infection_tick_seconds = 12
        self.infection_threshold = 50.0
        self.infection_growth_per_minute = 5.0
        self.infection_decay_per_minute = 2.0
        self.infection_max = 100.0
        self.infection_warn_cooldown_seconds = 120
        self.infection_zombie_entity = "minecraft:zombie"
        self.infection_zombie_entities: list[str] = ["minecraft:zombie"]

    def load_settings(self) -> None:
        def _get_bool(key: str, default: str) -> bool:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            return str(val).strip().lower() in ("1", "true", "yes", "on")

        def _get_float(key: str, default: str, minimum: Optional[float] = None) -> float:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                val = default
            parsed = float(val)
            if minimum is not None:
                return max(minimum, parsed)
            return parsed

        def _get_str(key: str, default: str) -> str:
            val = self.setting_manager.GetSetting(key)
            if val is None or val == "":
                self.setting_manager.SetSetting(key, default)
                return default
            return str(val).strip()

        self.infection_enabled = _get_bool("infection_enabled", "false")
        self.infection_tick_seconds = int(_get_float("infection_tick_seconds", "12", 6))
        self.infection_threshold = _get_float("infection_threshold", "50")
        self.infection_growth_per_minute = _get_float("infection_growth_per_minute", "5", 0)
        self.infection_decay_per_minute = _get_float("infection_decay_per_minute", "2", 0)
        self.infection_max = _get_float("infection_max", "100", 1)
        self.infection_warn_cooldown_seconds = int(_get_float("infection_warn_cooldown_seconds", "120", 30))
        self.infection_zombie_entity = _get_str("infection_zombie_entity", "minecraft:zombie")
        entities_raw = self.setting_manager.GetSetting("infection_zombie_entities")
        if entities_raw is None or str(entities_raw).strip() == "":
            entities_raw = self.infection_zombie_entity
        self.infection_zombie_entities = [
            x.strip() for x in str(entities_raw).split(",") if x.strip()
        ] or [self.infection_zombie_entity]

    def ensure_tables(self) -> None:
        player_fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "infection": "REAL NOT NULL DEFAULT 0",
            "updated_at": "TEXT NOT NULL",
        }
        if self.db_manager.create_table("player_infection", player_fields):
            self._log("info", "[ARS] player_infection table ready")

        source_fields = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "match_pattern": "TEXT NOT NULL UNIQUE",
            "match_type": "TEXT NOT NULL",
            "infection_delta": "INTEGER NOT NULL DEFAULT 5",
            "display_name": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        if self.db_manager.create_table("infection_sources", source_fields):
            self._log("info", "[ARS] infection_sources table ready")
            self._seed_default_sources_if_empty()

    def _seed_default_sources_if_empty(self) -> None:
        try:
            row = self.db_manager.query_one("SELECT COUNT(*) AS cnt FROM infection_sources")
            if row and int(row.get("cnt", 0)) > 0:
                return
            now = datetime.datetime.utcnow().isoformat()
            for pattern, name, delta in DEFAULT_INFECTION_SOURCES:
                match_type = "namespace" if pattern.endswith(":") else "entity"
                self.db_manager.insert("infection_sources", {
                    "match_pattern": pattern,
                    "match_type": match_type,
                    "infection_delta": delta,
                    "display_name": name,
                    "created_at": now,
                    "updated_at": now,
                })
            self._log("info", f"[ARS] seeded {len(DEFAULT_INFECTION_SOURCES)} infection_sources rows")
        except Exception as e:
            self._log("error", f"[ARS] seed infection_sources error: {e}")

    def load_sources_config(self) -> None:
        self.entity_rules = {}
        self.namespace_rules = {}
        count = 0
        try:
            if not self.db_manager.table_exists("infection_sources"):
                return
            rows = self.db_manager.query_all(
                "SELECT match_pattern, match_type, infection_delta, display_name "
                "FROM infection_sources WHERE match_pattern IS NOT NULL AND match_pattern != ''"
            )
            for row in rows:
                pattern = str(row.get("match_pattern", "")).strip().lower()
                if not pattern:
                    continue
                match_type = str(row.get("match_type", "")).strip().lower()
                if not match_type:
                    match_type = "namespace" if pattern.endswith(":") else "entity"
                cfg = {
                    "delta": int(row.get("infection_delta", 0)),
                    "display_name": row.get("display_name") or pattern,
                }
                if match_type == "namespace" or pattern.endswith(":"):
                    ns = pattern if pattern.endswith(":") else pattern + ":"
                    self.namespace_rules[ns] = cfg
                else:
                    self.entity_rules[pattern] = cfg
                    if ":" in pattern:
                        short = pattern.split(":", 1)[1]
                        self.entity_rules[short] = cfg
                count += 1
        except Exception as e:
            self._log("error", f"[ARS] load infection_sources error: {e}")
        self._log(
            "info",
            f"[ARS] infection sources: rows={count}, entity_keys={len(self.entity_rules)}, "
            f"namespace_keys={len(self.namespace_rules)}",
        )

    @staticmethod
    def _normalize_actor_type(actor) -> str:
        if actor is None:
            return ""
        raw = getattr(actor, "type", None)
        if raw is None:
            return ""
        text = str(raw).strip().lower()
        if ":" not in text and text:
            return f"minecraft:{text}"
        return text

    def resolve_infection_delta(self, actor_type: str) -> tuple[int, Optional[str]]:
        if not actor_type:
            return 0, None
        normalized = actor_type.strip().lower()
        if not normalized:
            return 0, None

        if normalized in self.entity_rules:
            cfg = self.entity_rules[normalized]
            return int(cfg["delta"]), cfg.get("display_name")

        if ":" in normalized:
            short = normalized.split(":", 1)[1]
            if short in self.entity_rules:
                cfg = self.entity_rules[short]
                return int(cfg["delta"]), cfg.get("display_name")

        ns_prefix = normalized.split(":", 1)[0] + ":"
        if ns_prefix in self.namespace_rules:
            cfg = self.namespace_rules[ns_prefix]
            return int(cfg["delta"]), cfg.get("display_name")

        return 0, None

    def _clamp(self, value: float) -> float:
        return max(0.0, min(self.infection_max, value))

    def load_player(self, player) -> float:
        xuid = self._get_xuid(player)
        row = self.db_manager.query_one("SELECT infection FROM player_infection WHERE xuid=?", (xuid,))
        if row is None:
            val = 0.0
            self.db_manager.insert("player_infection", {
                "xuid": xuid,
                "player_name": player.name,
                "infection": val,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        else:
            val = float(row.get("infection", 0))
        self.player_infection[xuid] = self._clamp(val)
        return self.player_infection[xuid]

    def persist_player(self, player) -> None:
        """仅退出/关服/死亡/丧尸化等关键点调用；运行时数值只在内存。"""
        try:
            xuid = self._get_xuid(player)
            infection = self._clamp(float(self.player_infection.get(xuid, 0.0)))
            self.db_manager.upsert("player_infection", {
                "xuid": xuid,
                "player_name": player.name,
                "infection": infection,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            self._log("error", f"[ARS] persist infection error: {e}")

    def persist_by_xuid(self, xuid: str, player_name: str = "") -> None:
        """按 xuid 落库感染值，不依赖可能已销毁的 Player 对象。"""
        try:
            xuid_s = str(xuid or "").strip()
            if not xuid_s:
                return
            infection = self._clamp(float(self.player_infection.get(xuid_s, 0.0)))
            self.db_manager.upsert("player_infection", {
                "xuid": xuid_s,
                "player_name": str(player_name or "").strip(),
                "infection": infection,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            self._log("error", f"[ARS] persist infection by xuid error: {e}")

    def set_infection(self, player, value: float) -> float:
        if not self.infection_enabled:
            raise RuntimeError("infection system disabled")
        xuid = self._get_xuid(player)
        old = float(self.player_infection.get(xuid, 0.0))
        new_val = self._clamp(float(value))
        self.player_infection[xuid] = new_val
        self._notify_threshold_cross(player, old, new_val)
        if new_val >= self.infection_max:
            self._on_infection_max(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        return new_val

    def apply_delta(self, player, delta: float, source_label: str = "") -> float:
        if not self.infection_enabled:
            return float(self.player_infection.get(self._get_xuid(player), 0.0))
        xuid = self._get_xuid(player)
        old = float(self.player_infection.get(xuid, 0.0))
        new_val = self._clamp(old + float(delta))
        self.player_infection[xuid] = new_val
        if delta > 0 and source_label:
            try:
                player.send_popup(f"感染 +{int(delta)} ({source_label}) → {int(new_val)}")
            except Exception:
                pass
        self._notify_threshold_cross(player, old, new_val)
        if new_val >= self.infection_max:
            self._on_infection_max(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        return new_val

    def _can_warn(self, xuid: str) -> bool:
        last = self.player_last_warn.get(xuid, 0.0)
        return (time.time() - last) >= self.infection_warn_cooldown_seconds

    def _mark_warn(self, xuid: str) -> None:
        self.player_last_warn[xuid] = time.time()

    def _notify_threshold_cross(self, player, old: float, new: float) -> None:
        xuid = self._get_xuid(player)
        threshold = self.infection_threshold
        crossed_up = old < threshold <= new
        crossed_down = old >= threshold > new
        if not (crossed_up or crossed_down):
            return
        if not self._can_warn(xuid):
            return
        try:
            if crossed_up:
                player.send_toast("感染恶化", "感染值已超过临界，若不治疗将持续恶化！")
            else:
                player.send_toast("感染缓解", "感染值已低于临界，正在缓慢恢复。")
        except Exception:
            if crossed_up:
                player.send_message("[感染] 感染值已超过临界，若不治疗将持续恶化！")
            else:
                player.send_message("[感染] 感染值已低于临界，正在缓慢恢复。")
        self._mark_warn(xuid)

    def _on_infection_max(self, player) -> None:
        """感染满值：每次检测都 kill；不清零。首次提示并刷丧尸，清零只在死亡。"""
        if not self.infection_enabled:
            return
        xuid = self._get_xuid(player)
        self.player_infection[xuid] = self.infection_max

        if xuid not in self._max_infection_active:
            self._max_infection_active.add(xuid)
            try:
                player.send_toast("丧尸化", "感染失控！你变成了丧尸…")
            except Exception:
                try:
                    player.send_message("[感染] 感染失控！你变成了丧尸…")
                except Exception:
                    pass
            self._spawn_zombie_at_player(player)

        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass
        self._console_kill(player)

    def _console_kill(self, player) -> None:
        player_name = str(getattr(player, "name", "") or "").strip()
        if not player_name:
            return
        try:
            safe_name = player_name.replace('"', '\\"')
            self.plugin.server.dispatch_command(
                self.plugin.server.command_sender, f'kill "{safe_name}"'
            )
        except Exception as e:
            self._log("error", f"[ARS] kill via dispatch_command error: {e}")

    def _spawn_zombie_at_player(self, player) -> None:
        loc = None
        dimension = None
        try:
            loc = player.location
            dimension = getattr(loc, "dimension", None)
        except Exception as e:
            self._log("error", f"[ARS] read location on transform error: {e}")
            return
        if loc is None:
            return
        spawn_type = random.choice(self.infection_zombie_entities)
        try:
            target_dim = dimension
            if target_dim is None:
                level = self.plugin.server.level
                if level is not None:
                    target_dim = level.get_dimension("overworld")
            if target_dim is not None:
                target_dim.spawn_actor(loc, spawn_type)
        except Exception as e:
            self._log("error", f"[ARS] spawn zombie after transform error: {e}")

    def tick_growth_decay(self, player, growth_per_tick: float, decay_per_tick: float) -> None:
        """统一定时器调用：按阈值增减感染值（满值不在这里处理）。"""
        if not self.infection_enabled:
            return
        xuid = self._get_xuid(player)
        if xuid not in self.player_infection:
            self.load_player(player)
        current = float(self.player_infection.get(xuid, 0.0))
        if current <= 0:
            return
        if current >= self.infection_max:
            return
        if current >= self.infection_threshold:
            new_val = self._clamp(current + growth_per_tick)
        else:
            new_val = self._clamp(current - decay_per_tick)
        if abs(new_val - current) < 0.001:
            return
        self.player_infection[xuid] = new_val
        self._notify_threshold_cross(player, current, new_val)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def tick_max_infection(self, player) -> None:
        """统一定时器每秒调用：满值则 kill（不清零）。"""
        if not self.infection_enabled:
            return
        xuid = self._get_xuid(player)
        if xuid not in self.player_infection:
            self.load_player(player)
        current = float(self.player_infection.get(xuid, 0.0))
        if current >= self.infection_max:
            self._on_infection_max(player)

    def reset_on_death(self, player) -> None:
        """任意死亡：内存与数据库立刻清零感染，并退出满值阶段。"""
        xuid = self._get_xuid(player)
        self._max_infection_active.discard(xuid)
        self.player_infection[xuid] = 0.0
        self.persist_player(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def reset_on_respawn(self, player) -> None:
        """重生再强制清零落库，双保险。"""
        xuid = self._get_xuid(player)
        self._max_infection_active.discard(xuid)
        self.player_infection[xuid] = 0.0
        self.persist_player(player)
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def apply_healthy_bypass(self, player) -> None:
        """创造/旁观：感染显示为 0，不写库。"""
        xuid = self._get_xuid(player)
        self.player_infection[xuid] = 0.0
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def restore_infection(self, player, value: float) -> None:
        """从快照恢复真实感染值。"""
        xuid = self._get_xuid(player)
        self.player_infection[xuid] = self._clamp(float(value))
        try:
            self.plugin._push_sidebar_for_player(player)
        except Exception:
            pass

    def on_player_join(self, player) -> None:
        self.load_player(player)

    def on_player_quit(self, player) -> None:
        self.persist_player(player)
        xuid = self._get_xuid(player)
        self.player_infection.pop(xuid, None)
        self._max_infection_active.discard(xuid)

    def on_actor_damage(self, event: ActorDamageEvent) -> None:
        if not self.infection_enabled:
            return
        try:
            victim = event.actor
            if not hasattr(victim, "game_mode"):
                return
            if victim.game_mode != GameMode.SURVIVAL and victim.game_mode != GameMode.ADVENTURE:
                return

            source = event.damage_source
            attacker = source.actor if source is not None else None
            if attacker is None and source is not None:
                attacker = source.damaging_actor
            if attacker is None:
                return
            if hasattr(attacker, "game_mode"):
                return

            actor_type = self._normalize_actor_type(attacker)
            delta, label = self.resolve_infection_delta(actor_type)
            if delta <= 0:
                return

            xuid = self._get_xuid(victim)
            if xuid not in self.player_infection:
                self.load_player(victim)

            self.apply_delta(victim, delta, label or actor_type)
            self._log(
                "info",
                f"[ARS][infection] player={victim.name} hit_by={actor_type} delta={delta} "
                f"now={int(self.player_infection.get(xuid, 0))}",
            )
        except Exception as e:
            self._log("error", f"[ARS] infection damage handler error: {e}")

    def start_timer(self) -> None:
        """由主插件 20-tick 统一定时器驱动；此处仅清理旧任务。"""
        self.stop_timer()

    def stop_timer(self) -> None:
        if self.infection_task is not None:
            try:
                self.infection_task.cancel()
            except Exception:
                pass
            self.infection_task = None

    def get_status_lines(self, player) -> list[str]:
        xuid = self._get_xuid(player)
        val = float(self.player_infection.get(xuid, 0.0))
        lines = [
            "=== 丧尸病毒感染 ===",
            f"感染值: {int(val)}/{int(self.infection_max)}",
            f"临界值: {int(self.infection_threshold)}（超过则持续恶化）",
        ]
        if val >= self.infection_threshold:
            lines.append("状态: 恶化中（每分钟 +" + str(int(self.infection_growth_per_minute)) + "）")
        elif val > 0:
            lines.append("状态: 恢复中（每分钟 -" + str(int(self.infection_decay_per_minute)) + "）")
        else:
            lines.append("状态: 未感染")
        return lines

    def get_source_catalog_lines(self, limit: int = 15) -> list[str]:
        lines = ["=== 感染源配置（节选）==="]
        try:
            rows = self.db_manager.query_all(
                "SELECT match_pattern, match_type, infection_delta, display_name "
                "FROM infection_sources ORDER BY match_pattern LIMIT ?",
                (limit,),
            )
            for row in rows:
                name = row.get("display_name") or row.get("match_pattern")
                mtype = row.get("match_type", "entity")
                lines.append(
                    f"{name} [{mtype}] {row.get('match_pattern')} → +{row.get('infection_delta')}/击"
                )
        except Exception:
            lines.append("（无法读取感染源表）")
        return lines
