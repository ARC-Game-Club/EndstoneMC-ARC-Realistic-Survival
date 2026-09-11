"""ARC 真实生存物品包（behavior pack）内置默认效果目录。

此目录只是 ConsumeEffectManager 的内置默认值来源：首次加载时会补齐到
consume_items 统一配置表（表内已有行不覆盖）。运行时效果一律以
consume_items 表为准；行为包脚本不再调用 /arseffect 指令。
/heal 仅管理用，不进本目录。
"""

# key: 完整物品 ID（小写）
# thirst: 口渴增量
# vitamin_a / vitamin_c / iron / protein: 营养增量
# infection: 感染增量（负数为净化）
ARC_PACK_EFFECTS: dict[str, dict] = {
    "arc:bottled_water": {
        "label": "瓶装矿泉水",
        "thirst": 42,
    },
    "arc:vitamin_a_pill": {
        "label": "维A软胶囊",
        "vitamin_a": 30,
    },
    "arc:vitamin_c_tablet": {
        "label": "维C泡腾片",
        "vitamin_c": 30,
    },
    "arc:iron_supplement": {
        "label": "补铁剂",
        "iron": 30,
    },
    "arc:protein_shot": {
        "label": "蛋白粉剂",
        "protein": 30,
    },
    "arc:multivitamin": {
        "label": "复合维生素",
        "vitamin_a": 14,
        "vitamin_c": 14,
        "iron": 14,
        "protein": 14,
    },
    "arc:field_ration_med": {
        "label": "野战营养膏",
        "thirst": 10,
        "vitamin_a": 8,
        "vitamin_c": 8,
        "iron": 8,
        "protein": 8,
    },
    "arc:recovery_injection": {
        "label": "急救营养针",
        "vitamin_a": 20,
        "vitamin_c": 20,
        "iron": 20,
        "protein": 20,
    },
    "arc:antiviral_weak": {
        "label": "抗丧尸病毒片",
        "infection": -15,
    },
    "arc:antiviral_strong": {
        "label": "强效丧尸病毒抑制剂",
        "infection": -40,
    },
    "arc:purge_serum": {
        "label": "丧尸病毒净化血清",
        "infection": -85,
    },
}


def normalize_item_id(item_id: str) -> str:
    return str(item_id or "").strip().lower()
