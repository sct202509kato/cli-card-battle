from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
import random
from typing import List, Optional, Tuple


# =========
#  Models
# =========


class Role(Enum):
    ATTACKER = auto()
    HEALER = auto()
    SUPPORTER = auto()
    TANK = auto()


class Action(Enum):
    ATTACK = auto()
    HEAL = auto()
    DEFEND = auto()
    SUPPORT_ATTACK = auto()
    AOE_ATTACK = auto()  # ★追加：全体攻撃（アタッカー専用）


@dataclass
class Effects:
    defending: bool = False
    atk_buff: int = 0
    atk_debuff: int = 0


@dataclass
class Cooldowns:
    aoe_attack: int = 0  # ★追加：全体攻撃のCT（残りターン）


@dataclass
class Stats:
    max_hp: int = 100
    hp: int = 100
    atk: int = 5
    vit: int = 5
    luk: int = 5  # MVPでは未使用
    spd: int = 5


@dataclass
class Character:
    name: str
    role: Role
    stats: Stats
    effects: Effects = field(default_factory=Effects)
    cds: Cooldowns = field(default_factory=Cooldowns)

    def alive(self) -> bool:
        return self.stats.hp > 0


@dataclass
class Party:
    name: str
    members: List[Character]


# =========
#  Utils
# =========


def roll_die() -> int:
    return random.randint(1, 6)


def roll_dice(n: int) -> int:
    return sum(roll_die() for _ in range(n))


def clamp_hp(c: Character) -> None:
    c.stats.hp = max(0, min(c.stats.hp, c.stats.max_hp))


def living(p: Party) -> List[Character]:
    return [c for c in p.members if c.alive()]


def party_defeated(p: Party) -> bool:
    return all(not c.alive() for c in p.members)


def find_most_damaged_ally(p: Party) -> Optional[Character]:
    alive = living(p)
    if not alive:
        return None
    return min(alive, key=lambda c: c.stats.hp / c.stats.max_hp)


def pick_enemy(p: Party) -> Optional[Character]:
    alive = living(p)
    if not alive:
        return None
    return min(alive, key=lambda c: c.stats.hp)


def reset_turn_flags(p1: Party, p2: Party) -> None:
    for c in p1.members + p2.members:
        c.effects.defending = False
        c.effects.atk_buff = 0
        # c.effects.atk_debuff = 0  # ★消す：次ターンまで残す


def tick_cooldowns(p1: Party, p2: Party) -> None:
    """
    ★追加：ターン開始時にクールタイムを減らす（0未満にしない）
    """
    for c in p1.members + p2.members:
        if c.cds.aoe_attack > 0:
            c.cds.aoe_attack -= 1


def build_turn_order(p1: Party, p2: Party) -> List[Character]:
    chars = living(p1) + living(p2)
    random.shuffle(chars)
    chars.sort(key=lambda c: c.stats.spd, reverse=True)
    return chars


# =========
#  Phase (damage scaling)
# =========


def phase_multiplier(turn: int) -> float:
    if turn >= 15:
        return 1.4
    if turn >= 10:
        return 1.2
    return 1.0


# =========
#  Action selection (simple AI)
# =========


def choose_action(
    actor: Character, allies: Party, enemies: Party
) -> Tuple[Action, Optional[Character]]:
    # 安全のため：敵も味方も「生存者リスト」を最初に作る
    ally_list = living(allies)
    enemy_list = living(enemies)

    # もし敵が誰もいないなら、何もしない（battle側で勝敗判定されるはず）
    if not enemy_list:
        return (Action.DEFEND, None)

    # ===== ATTACKER =====
    if actor.role == Role.ATTACKER:
        enemy_alive = len(enemy_list)
        low_hp_exists = any(e.stats.hp <= 50 for e in enemy_list)

        if enemy_alive >= 3 and actor.cds.aoe_attack == 0 and low_hp_exists:
            return (Action.AOE_ATTACK, None)

        return (Action.ATTACK, pick_enemy(enemies))

    # ===== HEALER =====
    if actor.role == Role.HEALER:
        target = find_most_damaged_ally(allies)
        if target and (target.stats.hp / target.stats.max_hp) < 0.70:
            return (Action.HEAL, target)
        return (Action.DEFEND, None)

    # ===== SUPPORTER =====
    if actor.role == Role.SUPPORTER:
        # アタッカー優先で狙う（いなければHP低い敵）
        attackers = [e for e in enemy_list if e.role == Role.ATTACKER]
        target = attackers[0] if attackers else pick_enemy(enemies)

        # 念のためターゲットが取れない場合は防御に逃がす
        if target is None:
            return (Action.DEFEND, None)

        return (Action.SUPPORT_ATTACK, target)

    # ===== TANK =====
    if actor.role == Role.TANK:
        return (Action.DEFEND, None)

    # 万一 role が想定外でも必ず返す
    return (Action.DEFEND, None)


# =========
#  Resolution
# =========


def effective_atk_value(attacker: Character) -> int:
    val = attacker.stats.atk + attacker.effects.atk_buff + attacker.effects.atk_debuff

    # ★デバフは「次の攻撃1回」で消費される
    if attacker.effects.atk_debuff != 0:
        attacker.effects.atk_debuff = 0

    return max(1, val)


def apply_damage(target: Character, raw: int) -> int:
    dmg = raw
    if target.effects.defending:
        dmg = int(raw * 0.6)
    target.stats.hp -= dmg
    clamp_hp(target)
    return dmg


def resolve_attack(attacker: Character, target: Character, turn: int) -> str:
    if not (attacker.alive() and target.alive()):
        return ""

    dice = roll_dice(2)  # 単体攻撃は2個
    atk_val = effective_atk_value(attacker)
    raw = int(dice * atk_val * phase_multiplier(turn))

    dmg = apply_damage(target, raw)
    def_tag = " (DEF)" if target.effects.defending else ""
    return f"{attacker.name} attacks {target.name}{def_tag}: dice={dice}, mult={phase_multiplier(turn)} raw={raw} -> dmg={dmg} | {target.name} HP={target.stats.hp}"


def resolve_aoe_attack(attacker: Character, enemies: Party, turn: int) -> str:
    """
    ★追加：全体攻撃（アタッカー専用）
      - ダイス1個 × ATK（単体より弱い）
      - クールタイム 3
    """
    if not attacker.alive():
        return ""

    targets = living(enemies)
    if not targets:
        return ""

    dice = roll_dice(1)  # 全体は1個
    atk_val = effective_atk_value(attacker)
    raw_base = int(dice * atk_val * phase_multiplier(turn) * 0.8)

    logs = []
    for t in targets:
        dmg = apply_damage(t, raw_base)
        def_tag = " (DEF)" if t.effects.defending else ""
        logs.append(f"{t.name}{def_tag} -{dmg} (HP {t.stats.hp})")

    attacker.cds.aoe_attack = 3  # CTセット

    return (
        f"{attacker.name} uses WHIRLWIND (AOE)! dice={dice}, mult={phase_multiplier(turn)} raw={raw_base} "
        f"| " + ", ".join(logs) + f" | CT=3"
    )


def resolve_support_attack(supporter: Character, target: Character, turn: int) -> str:
    if not (supporter.alive() and target.alive()):
        return ""

    dice = roll_dice(1)
    atk_val = effective_atk_value(supporter)
    raw = int(dice * atk_val * phase_multiplier(turn))

    dmg = apply_damage(target, raw)

    # 命中時デバフ：ATK -2（MVPはターン開始リセットで消える）
    target.effects.atk_debuff = -2

    def_tag = " (DEF)" if target.effects.defending else ""
    return (
        f"{supporter.name} support-attacks {target.name}{def_tag}: dice={dice}, mult={phase_multiplier(turn)} raw={raw} -> dmg={dmg} | "
        f"{target.name} HP={target.stats.hp} | {target.name} ATK debuff -2"
    )


def resolve_heal(healer: Character, target: Character) -> str:
    if not (healer.alive() and target.alive()):
        return ""

    dice = roll_dice(1)
    amount = dice * healer.stats.vit

    before = target.stats.hp
    target.stats.hp += amount
    clamp_hp(target)
    actual = target.stats.hp - before

    return f"{healer.name} heals {target.name}: dice={dice}, heal={amount} -> +{actual} | {target.name} HP={target.stats.hp}"


def resolve_defend(actor: Character) -> str:
    if not actor.alive():
        return ""
    actor.effects.defending = True
    return f"{actor.name} defends (incoming dmg x0.6)"


# =========
#  Battle loop
# =========


def print_party(p: Party) -> None:
    print(f"[{p.name}]")
    for c in p.members:
        s = c.stats
        print(
            f"  - {c.name:10s} {c.role.name:9s} HP={s.hp}/{s.max_hp} ATK={s.atk} VIT={s.vit} SPD={s.spd}"
        )


def print_status(p1: Party, p2: Party) -> None:
    def line(p: Party) -> str:
        parts = [f"{c.name}:{c.stats.hp}" for c in p.members]
        return f"{p.name} | " + " ".join(parts)

    print(line(p1))
    print(line(p2))


def battle(p1: Party, p2: Party, seed: Optional[int] = 0, turn_limit: int = 50) -> str:
    if seed is not None:
        random.seed(seed)

    turn = 1
    print(f"=== Battle Start: {p1.name} vs {p2.name} ===")
    print_party(p1)
    print_party(p2)
    print()

    while turn <= turn_limit:
        print(f"\n--- Turn {turn} ---")
        tick_cooldowns(p1, p2)
        reset_turn_flags(p1, p2)
        order = build_turn_order(p1, p2)

        for actor in order:
            if not actor.alive():
                continue

            allies = p1 if actor in p1.members else p2
            enemies = p2 if actor in p1.members else p1

            action, target = choose_action(actor, allies, enemies)

            log = ""
            if action == Action.ATTACK and target:
                log = resolve_attack(actor, target, turn)
            elif action == Action.AOE_ATTACK:
                log = resolve_aoe_attack(actor, enemies, turn)
            elif action == Action.SUPPORT_ATTACK and target:
                log = resolve_support_attack(actor, target, turn)
            elif action == Action.HEAL and target:
                log = resolve_heal(actor, target)
            elif action == Action.DEFEND:
                log = resolve_defend(actor)

            if log:
                # CT表示を少し見やすく（アタッカーのみ）
                if actor.role == Role.ATTACKER:
                    log += f" | {actor.name} AOE_CT={actor.cds.aoe_attack}"
                print(log)

            if party_defeated(p1):
                print(f"\n=== Winner: {p2.name} ===")
                return p2.name
            if party_defeated(p2):
                print(f"\n=== Winner: {p1.name} ===")
                return p1.name

        print_status(p1, p2)
        turn += 1

    print("\n=== Draw (turn limit reached) ===")
    return "DRAW"


# =========
#  Sample setup
# =========


def make_sample_party(name: str) -> Party:
    members = [
        Character(f"{name}_Att", Role.ATTACKER, Stats(atk=6, vit=4, spd=6)),
        Character(f"{name}_Heal", Role.HEALER, Stats(atk=3, vit=6, spd=5)),
        Character(f"{name}_Sup", Role.SUPPORTER, Stats(atk=4, vit=4, spd=6)),
        Character(f"{name}_Tank", Role.TANK, Stats(atk=4, vit=5, spd=4)),
    ]
    return Party(name=name, members=members)


if __name__ == "__main__":
    p1 = make_sample_party("A")
    p2 = make_sample_party("B")
    battle(p1, p2, seed=1, turn_limit=50)
