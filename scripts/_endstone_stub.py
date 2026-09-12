# -*- coding: utf-8 -*-
"""离线测试共享的 endstone stub：导入本模块后即可 import 插件包（含主插件）。"""
import sys
import types

endstone = types.ModuleType("endstone")
endstone.__path__ = []  # 标记为包


class GameMode:
    SURVIVAL = "SURVIVAL"
    ADVENTURE = "ADVENTURE"
    CREATIVE = "CREATIVE"
    SPECTATOR = "SPECTATOR"


endstone.GameMode = GameMode


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    setattr(endstone, name.split(".", 1)[1], m)
    return m


cmd_mod = _mod("endstone.command")
cmd_mod.Command = type("Command", (), {})
cmd_mod.CommandSender = type("CommandSender", (), {})

evt_mod = _mod("endstone.event")


def event_handler(*a, **k):
    def deco(fn):
        return fn
    return deco


evt_mod.event_handler = event_handler
for cls_name in (
    "PlayerItemConsumeEvent", "PlayerMoveEvent", "PlayerJoinEvent", "PlayerQuitEvent",
    "ActorDamageEvent", "PlayerDeathEvent", "PlayerRespawnEvent", "PlayerGameModeChangeEvent",
):
    setattr(evt_mod, cls_name, type(cls_name, (), {}))

plugin_mod = _mod("endstone.plugin")
plugin_mod.Plugin = type("Plugin", (), {})

form_mod = _mod("endstone.form")


class _Form:
    def __init__(self, *a, **k):
        pass

    def add_button(self, *a, **k):
        pass


form_mod.ActionForm = type("ActionForm", (_Form,), {})
form_mod.ModalForm = type("ModalForm", (_Form,), {})
form_mod.Label = type("Label", (), {"__init__": lambda self, **k: None})
form_mod.TextInput = type("TextInput", (), {"__init__": lambda self, **k: None})

attr_mod = _mod("endstone.attribute")


class Attribute:
    HEALTH = 1
    PLAYER_EXHAUSTION = 2
    ATTACK_DAMAGE = 3


class AttributeModifier:
    ADD = 0
    MULTIPLY_BASE = 1

    def __init__(self, *a, **k):
        pass


attr_mod.Attribute = Attribute
attr_mod.AttributeModifier = AttributeModifier
sys.modules["endstone"] = endstone


def add_src_to_path():
    """把 src 目录加入 sys.path（脚本位于 scripts/ 下）。"""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
