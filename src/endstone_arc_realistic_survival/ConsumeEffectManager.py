"""统一进食效果管理：单一 consume_items 配置表驱动口渴/营养/感染变动与腿伤治疗。

取代旧的三套进食配置：
- thirst_items（口渴增量 + buffs）
- nutrition_items（四项营养增量）
- pack_effects 内置 arc 物品目录 + 行为包 /arseffect 指令调用

现在插件在 PlayerItemConsumeEvent 一个订阅点按 consume_items 表生效；
行为包不再调用指令，/arseffect 仅保留为管理测试入口。

加载顺序（每次 load_items_config 都执行，幂等）：
1. 内置默认（pack_effects 的 arc 物品 + 原版食物营养）补齐缺失行；
2. 旧表 thirst_items / nutrition_items 的自定义行迁移补齐缺失行（旧表值优先于内置默认）；
3. 从 consume_items 表加载全部行进内存；表内已有行永远是唯一运行时来源。
"""

import datetime
import json
import time
from typing import Any, Callable, Optional

from .effect_compat import apply_mob_effect, resolve_effect_type
from .NutritionManager import NUTRIENT_KEYS, NUTRIENT_LABELS
from .pack_effects import ARC_PACK_EFFECTS, normalize_item_id


# 原版食物营养默认（自 NutritionManager 迁入，仅营养，无口渴/感染）
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

CONSUME_ITEMS_FIELDS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "item_id": "TEXT NOT NULL UNIQUE",
    "item_name": "TEXT",
    "thirst_delta": "INTEGER NOT NULL DEFAULT 0",
    "vitamin_a": "INTEGER NOT NULL DEFAULT 0",
    "vitamin_c": "INTEGER NOT NULL DEFAULT 0",
    "iron": "INTEGER NOT NULL DEFAULT 0",
    "protein": "INTEGER NOT NULL DEFAULT 0",
    "infection_delta": "INTEGER NOT NULL DEFAULT 0",
    "cure_fracture": "INTEGER NOT NULL DEFAULT 0",
    "painkiller": "INTEGER NOT NULL DEFAULT 0",
    "buffs": "TEXT",
    "show_toast": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def _zero_row(item_id: str) -> dict:
    return {
        "item_id": item_id,
        "item_name": item_id,
        "thirst_delta": 0,
        "vitamin_a": 0,
        "vitamin_c": 0,
        "iron": 0,
        "protein": 0,
        "infection_delta": 0,
        "cure_fracture": 0,
        "painkiller": 0,
        "buffs": None,
        "show_toast": 0,
    }


class ConsumeEffectManager:
    """进食效果统一配置：item_id → 口渴/营养/感染增量 + buffs。"""

    def __init__(
        self,
        plugin,
        db_manager,
        log_fn: Callable[[str, str], None],
        get_xuid_fn: Callable[[Any], str],
        collect_item_identities_fn: Callable[[Any], list],
    ):
        self.plugin = plugin
        self.db_manager = db_manager
        self._log = log_fn
        self._get_xuid = get_xuid_fn
        self._collect_item_identities = collect_item_identities_fn

        self.items_map: dict[str, dict] = {}
        # arc: 物品防双触发（旧版行为包脚本仍会调 /arseffect，与 consume 事件撞车）
        self._arc_applied_at: dict[str, float] = {}

    # ---------- 表与配置加载 ----------

    def ensure_tables(self) -> None:
        if self.db_manager.create_table("consume_items", CONSUME_ITEMS_FIELDS):
            self._log("info", "[ARS] consume_items table ready")
        if not self.db_manager.ensure_column("consume_items", "cure_fracture", "INTEGER NOT NULL DEFAULT 0"):
            self._log("warning", "[ARS] failed to add consume_items.cure_fracture")
        if not self.db_manager.ensure_column("consume_items", "painkiller", "INTEGER NOT NULL DEFAULT 0"):
            self._log("warning", "[ARS] failed to add consume_items.painkiller")

    def load_items_config(self) -> None:
        self.items_map = {}
        try:
            self._fill_missing_rows()
        except Exception as e:
            self._log("error", f"[ARS] fill consume_items error: {e}")
        count = 0
        try:
            rows = self.db_manager.query_all(
                "SELECT item_id, item_name, thirst_delta, vitamin_a, vitamin_c, iron, protein, "
                "infection_delta, cure_fracture, painkiller, buffs, show_toast FROM consume_items "
                "WHERE item_id IS NOT NULL AND item_id != ''"
            )
            for row in rows:
                item_id = row.get("item_id")
                if not item_id:
                    continue
                buffs_raw = row.get("buffs")
                buffs_list = None
                if buffs_raw:
                    try:
                        parsed = json.loads(buffs_raw)
                        if isinstance(parsed, list):
                            buffs_list = parsed
                    except Exception:
                        buffs_list = None
                cfg = {
                    "item_id": str(item_id).strip().lower(),
                    "item_name": row.get("item_name") or item_id,
                    "thirst_delta": int(row.get("thirst_delta", 0) or 0),
                    "vitamin_a": int(row.get("vitamin_a", 0) or 0),
                    "vitamin_c": int(row.get("vitamin_c", 0) or 0),
                    "iron": int(row.get("iron", 0) or 0),
                    "protein": int(row.get("protein", 0) or 0),
                    "infection_delta": int(row.get("infection_delta", 0) or 0),
                    "cure_fracture": int(row.get("cure_fracture", 0) or 0),
                    "painkiller": int(row.get("painkiller", 0) or 0),
                    "buffs": buffs_list,
                    "show_toast": int(row.get("show_toast", 0) or 0),
                }
                self._register_item_cfg(item_id, cfg)
                count += 1
        except Exception as e:
            self._log("error", f"[ARS] load consume_items error: {e}")
        self._log(
            "info",
            f"[ARS] consume items: rows={count}, lookup keys={len(self.items_map)} (含命名空间/短名展开)",
        )

    def _builtin_defaults(self) -> dict[str, dict]:
        """内置默认行：原版食物营养 + arc 物品包效果（arc 物品 show_toast=1）。"""
        merged: dict[str, dict] = {}
        for item_id, name, va, vc, fe, pr in DEFAULT_NUTRITION_ITEMS:
            key = str(item_id).strip().lower()
            row = _zero_row(key)
            row["item_name"] = name
            row["vitamin_a"] = int(va)
            row["vitamin_c"] = int(vc)
            row["iron"] = int(fe)
            row["protein"] = int(pr)
            merged[key] = row
        for item_id, eff in ARC_PACK_EFFECTS.items():
            key = str(item_id).strip().lower()
            row = merged.get(key) or _zero_row(key)
            row["item_name"] = eff.get("label") or row.get("item_name") or key
            row["thirst_delta"] = int(eff.get("thirst", 0) or 0)
            for k in NUTRIENT_KEYS:
                row[k] = int(eff.get(k, 0) or 0)
            row["infection_delta"] = int(eff.get("infection", 0) or 0)
            # 特殊效果列（止痛药/夹板等腿伤物品）
            for flag in ("painkiller", "cure_fracture"):
                if flag in eff:
                    row[flag] = int(eff[flag] or 0)
            row["show_toast"] = 1
            merged[key] = row
        return merged

    def _fill_missing_rows(self) -> None:
        """幂等补行：默认目录与旧表条目只在 consume_items 缺行时插入；已有行不覆盖。"""
        now = datetime.datetime.utcnow().isoformat()
        existing = {
            str(r.get("item_id") or "").strip().lower()
            for r in self.db_manager.query_all("SELECT item_id FROM consume_items")
        }
        defaults = self._builtin_defaults()
        candidates: dict[str, dict] = {}

        # 旧表行优先合并到候选（基础值取内置默认，旧表覆盖）
        if self.db_manager.table_exists("thirst_items"):
            for row in self.db_manager.query_all(
                "SELECT item_id, item_name, thirst_delta, buffs FROM thirst_items "
                "WHERE item_id IS NOT NULL AND item_id != ''"
            ):
                key = str(row.get("item_id") or "").strip().lower()
                if not key:
                    continue
                cand = candidates.get(key) or dict(defaults.get(key) or _zero_row(key))
                cand["item_id"] = key
                try:
                    cand["thirst_delta"] = int(row.get("thirst_delta", 0) or 0)
                except Exception:
                    pass
                if row.get("item_name"):
                    cand["item_name"] = row["item_name"]
                buffs_raw = row.get("buffs")
                if buffs_raw:
                    try:
                        parsed = json.loads(buffs_raw)
                        if isinstance(parsed, list):
                            cand["buffs"] = parsed
                    except Exception:
                        pass
                candidates[key] = cand

        if self.db_manager.table_exists("nutrition_items"):
            for row in self.db_manager.query_all(
                "SELECT item_id, item_name, vitamin_a, vitamin_c, iron, protein FROM nutrition_items "
                "WHERE item_id IS NOT NULL AND item_id != ''"
            ):
                key = str(row.get("item_id") or "").strip().lower()
                if not key:
                    continue
                cand = candidates.get(key) or dict(defaults.get(key) or _zero_row(key))
                cand["item_id"] = key
                for k in NUTRIENT_KEYS:
                    try:
                        cand[k] = int(row.get(k, 0) or 0)
                    except Exception:
                        pass
                if row.get("item_name"):
                    cand["item_name"] = row["item_name"]
                candidates[key] = cand

        # 内置默认补齐其余
        for key, row in defaults.items():
            if key not in candidates:
                candidates[key] = row

        inserted = 0
        for key, row in candidates.items():
            if not key or key in existing:
                continue
            data = dict(row)
            buffs_val = data.get("buffs")
            if not isinstance(buffs_val, str):
                # buffs 在内存里是 list（旧表 JSON 解析后），入库前序列化
                data["buffs"] = json.dumps(buffs_val, ensure_ascii=False) if buffs_val else None
            data["created_at"] = now
            data["updated_at"] = now
            if self.db_manager.insert("consume_items", data):
                inserted += 1
        if inserted:
            self._log(
                "info",
                f"[ARS] consume_items 补齐 {inserted} 行（内置默认 + 旧表 thirst_items/nutrition_items 迁移）",
            )

    def _register_item_cfg(self, item_id: str, cfg: dict) -> None:
        """同一物品写入完整命名空间键与短 id（: 后一段），便于匹配 item.type 的多种格式。"""
        raw = str(item_id).strip()
        if not raw:
            return
        upper_full = raw.upper()
        self.items_map[upper_full] = cfg
        if ":" in upper_full:
            short_key = upper_full.split(":", 1)[1]
            self.items_map[short_key] = cfg

    # ---------- 查找 ----------

    def find_cfg_for_item(self, item) -> tuple[Optional[dict], Optional[str]]:
        for cand in self._collect_item_identities(item):
            upper_full = cand.upper()
            if upper_full in self.items_map:
                return self.items_map[upper_full], upper_full
            if ":" in upper_full:
                short_key = upper_full.split(":", 1)[1]
                if short_key in self.items_map:
                    return self.items_map[short_key], short_key
        return None, None

    def find_cfg_by_id(self, item_id: str) -> Optional[dict]:
        key = normalize_item_id(item_id).upper()
        if not key:
            return None
        if key in self.items_map:
            return self.items_map[key]
        if ":" in key:
            short_key = key.split(":", 1)[1]
            return self.items_map.get(short_key)
        return None

    def known_item_ids(self) -> list[str]:
        return sorted({cfg["item_id"] for cfg in self.items_map.values()})

    # ---------- 应用 ----------

    def on_player_consume(self, player, item) -> bool:
        """PlayerItemConsumeEvent 统一入口：命中配置即一次性应用口渴/营养/感染/buffs。"""
        cfg, lookup_key = self.find_cfg_for_item(item)
        if cfg is None:
            identities = self._collect_item_identities(item)
            self.plugin._log_consume_debug(
                f"player={getattr(player, 'name', '?')} identities={identities!r} "
                f"→ 未匹配 consume_items",
            )
            return False

        item_id = cfg["item_id"]

        if self._recently_applied(player, item_id):
            self.plugin._log_consume_debug(
                f"player={getattr(player, 'name', '?')} item={item_id} "
                f"已在 2s 内生效，跳过重复应用（key={lookup_key}）",
            )
            return True
        if item_id.startswith("arc:"):
            self._mark_applied(player, item_id)

        identities = self._collect_item_identities(item)
        self.plugin._log_consume_debug(
            f"player={getattr(player, 'name', '?')} identities={identities!r} "
            f"→ 命中 key={lookup_key} cfg={cfg}",
        )
        bits = self._apply_effect(player, cfg, allow_toast=True)
        self._log(
            "info",
            f"[ARS][consume] player={getattr(player, 'name', '?')} item={cfg.get('item_name') or item_id} "
            f"bits={' '.join(bits) if bits else '无变化'}",
        )
        return True

    def apply_by_id(self, player, item_id: str) -> tuple[str, str, list[str]]:
        """/arseffect 入口。返回 (status, label, bits)；status: applied|deduped|unknown。"""
        cfg = self.find_cfg_by_id(item_id)
        if cfg is None:
            return "unknown", item_id, []
        key = cfg["item_id"]
        if self._recently_applied(player, key):
            return "deduped", cfg.get("item_name") or key, []
        if key.startswith("arc:"):
            self._mark_applied(player, key)
        bits = self._apply_effect(player, cfg, allow_toast=False)
        return "applied", cfg.get("item_name") or key, bits

    def _recently_applied(self, player, item_id: str) -> bool:
        last = self._arc_applied_at.get(f"{self._get_xuid(player)}|{item_id}")
        return last is not None and (time.time() - float(last)) < 2.0

    def _mark_applied(self, player, item_id: str) -> None:
        self._arc_applied_at[f"{self._get_xuid(player)}|{item_id}"] = time.time()

    def _apply_effect(self, player, cfg: dict, allow_toast: bool) -> list[str]:
        """按一行配置应用全部效果；返回反馈片段（用于 toast/命令回显）。"""
        label = cfg.get("item_name") or cfg["item_id"]
        show_toast = bool(int(cfg.get("show_toast", 0) or 0))
        bits: list[str] = []

        thirst = int(cfg.get("thirst_delta", 0) or 0)
        if thirst:
            xuid = self._get_xuid(player)
            old = int(self.plugin.player_xuid_to_thirst.get(xuid, self.plugin.thirst_initial))
            new_val = self.plugin._apply_thirst_delta(
                player, thirst, reason="consume", notify=not show_toast
            )
            self.plugin._sync_creative_snap_thirst(player, new_val)
            gained = int(new_val) - old
            if gained > 0:
                bits.append(f"口渴+{gained}")
            elif old >= self.plugin.thirst_max:
                bits.append("口渴已满")
            else:
                bits.append(f"口渴{thirst:+d}")

        nutri = {k: int(cfg.get(k, 0) or 0) for k in NUTRIENT_KEYS}
        if any(v != 0 for v in nutri.values()):
            nm = getattr(self.plugin, "nutrition_manager", None)
            if nm is not None:
                data = nm.apply_deltas(player, nutri, item_label=label)
                self.plugin._sync_creative_snap_nutrition(player, data)
                bits.extend(
                    f"{NUTRIENT_LABELS.get(k, k)}{v:+d}" for k, v in nutri.items() if v
                )

        infection = int(cfg.get("infection_delta", 0) or 0)
        if infection and self.plugin._is_infection_enabled():
            zvm = getattr(self.plugin, "zombie_virus_manager", None)
            if zvm is not None:
                new_val = zvm.apply_delta(player, float(infection), source_label="")
                self.plugin._sync_creative_snap_infection(player, new_val)
                bits.append(f"感染{infection:+d}")

        buffs = cfg.get("buffs") or []
        if buffs:
            self._apply_buffs(player, buffs)

        # 腿伤处理（夹板）：骨折→降级为骨裂（最长时长）；骨裂→痊愈
        if int(cfg.get("cure_fracture", 0) or 0):
            fmg = getattr(self.plugin, "fracture_manager", None)
            if fmg is not None:
                try:
                    result, _ = fmg.apply_leg_treatment(player)
                except Exception as e:
                    self.plugin._log_consume_debug(f"leg treatment error: {e}")
                    result = "none"
                if result == "cured":
                    bits.append("腿伤已治疗")
                elif result == "downgraded":
                    bits.append("骨折已固定为骨裂")

        # 止痛药：骨裂不再减速（移动掉血保留）；骨折无效
        if int(cfg.get("painkiller", 0) or 0):
            fmg = getattr(self.plugin, "fracture_manager", None)
            if fmg is not None:
                try:
                    ok, _ = fmg.apply_painkiller(player)
                except Exception as e:
                    self.plugin._log_consume_debug(f"painkiller error: {e}")
                    ok = False
                if ok:
                    bits.append("止痛生效")

        if allow_toast and show_toast and bits:
            try:
                player.send_toast(f"服用了{label}", " ".join(bits))
            except Exception:
                pass
        return bits

    def _apply_buffs(self, player, buffs: list) -> None:
        for buff in buffs:
            if not isinstance(buff, dict):
                continue
            eff_name = buff.get("name")
            if not eff_name:
                continue
            try:
                duration_sec = int(buff.get("duration", 30))
            except Exception:
                duration_sec = 30
            try:
                amplifier = int(buff.get("amplifier", 0))
            except Exception:
                amplifier = 0
            try:
                effect_type = resolve_effect_type(str(eff_name))
                if effect_type is None:
                    continue
                apply_mob_effect(player, effect_type, duration_sec * 20, amplifier, ambient=True)
            except Exception as e:
                self._log("error", f"[ARS] apply item buff error: {e}")

    # ---------- 面板展示 ----------

    def get_catalog_lines(self, limit: int = 15) -> list[str]:
        lines = ["=== 进食效果配置（节选）==="]
        try:
            rows = self.db_manager.query_all(
                "SELECT item_name, item_id, thirst_delta, vitamin_a, vitamin_c, iron, protein, "
                "infection_delta FROM consume_items ORDER BY item_name LIMIT ?",
                (limit,),
            )
            for row in rows:
                name = row.get("item_name") or row.get("item_id")
                parts = []
                if int(row.get("thirst_delta", 0) or 0):
                    parts.append(f"口渴{int(row['thirst_delta']):+d}")
                if int(row.get("vitamin_a", 0) or 0):
                    parts.append(f"维A+{int(row['vitamin_a'])}")
                if int(row.get("vitamin_c", 0) or 0):
                    parts.append(f"维C+{int(row['vitamin_c'])}")
                if int(row.get("iron", 0) or 0):
                    parts.append(f"铁+{int(row['iron'])}")
                if int(row.get("protein", 0) or 0):
                    parts.append(f"蛋白+{int(row['protein'])}")
                if int(row.get("infection_delta", 0) or 0):
                    parts.append(f"感染{int(row['infection_delta']):+d}")
                lines.append(f"{name}: {' '.join(parts) or '无效果'}")
        except Exception:
            lines.append("（无法读取进食效果表）")
        return lines
