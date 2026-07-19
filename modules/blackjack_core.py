"""Blackjack card math, table rules, and basic strategy shared by two cogs.

modules/economy.py runs the real player-facing table game; modules/ai.py's
autonomous Silas gambler only ever sees rendered text and can only answer
"hit" or "stand". Both need the same card arithmetic and strategy knowledge,
so it lives here exactly once.

Rules state is module-global: BLACKJACK_RULES is mutated in place by
apply_blackjack_ruleset so that callers holding a reference (including tests
importing it via modules.economy) always observe the active ruleset.
"""

import shared

BLACKJACK_RULE_PRESETS = {
    "realistic": {
        "decks": 6,
        "reshuffle_at_cards": 78,
        "blackjack_payout": 1.5,  # 3:2
        "dealer_hits_soft_17": False,
        "double_any_two": True,
        "double_after_split": True,
        "allow_split": True,
        "allow_ten_value_split": True,
        "max_hands": 4,
        "resplit_aces": False,
        "draw_to_split_aces": False,
        "late_surrender": True,
        "five_card_charlie": False,
    },
    "arcade": {
        "decks": 6,
        "reshuffle_at_cards": 78,
        "blackjack_payout": 2.0,  # player-favorable: 2:1 blackjack payout
        "dealer_hits_soft_17": False,
        "double_any_two": True,
        "double_after_split": True,
        "allow_split": True,
        "allow_ten_value_split": True,
        "max_hands": 4,
        "resplit_aces": True,
        "draw_to_split_aces": True,
        "late_surrender": True,
        "five_card_charlie": True,
    },
}
BLACKJACK_RULES = {}

BLACKJACK_SUITS = ["♠", "♥", "♦", "♣"]
BLACKJACK_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def get_blackjack_ruleset_name():
    raw = shared.runtime_settings.get("bj_ruleset", "realistic")
    name = str(raw).strip().lower()
    return name if name in BLACKJACK_RULE_PRESETS else "realistic"


def apply_blackjack_ruleset(name: str):
    selected = str(name).strip().lower()
    if selected not in BLACKJACK_RULE_PRESETS:
        selected = "realistic"
    BLACKJACK_RULES.clear()
    BLACKJACK_RULES.update(BLACKJACK_RULE_PRESETS[selected])
    return selected


def card_points(rank):
    rank = rank.upper()
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_total_and_soft(hand):
    total = 0
    aces_as_eleven = 0
    for rank, _ in hand:
        total += card_points(rank)
        if rank == "A":
            aces_as_eleven += 1
    while total > 21 and aces_as_eleven:
        total -= 10
        aces_as_eleven -= 1
    return total, aces_as_eleven > 0


def hand_value(hand):
    total, _ = hand_total_and_soft(hand)
    return total


def is_soft_hand(hand):
    _, soft = hand_total_and_soft(hand)
    return soft


def is_blackjack(hand):
    return len(hand) == 2 and hand_value(hand) == 21


def dealer_up_value(card):
    return card_points(card[0])


def split_rank_key(card):
    rank = card[0]
    if rank in ("10", "J", "Q", "K") and BLACKJACK_RULES["allow_ten_value_split"]:
        return "10"
    return rank


def can_split_cards(hand):
    if len(hand) != 2:
        return False
    return split_rank_key(hand[0]) == split_rank_key(hand[1])


def dealer_should_hit(dealer_hand):
    total, soft = hand_total_and_soft(dealer_hand)
    if total < 17:
        return True
    if total == 17 and soft and BLACKJACK_RULES["dealer_hits_soft_17"]:
        return True
    return False


def is_soft_shown_total(ranks, total):
    """True if a displayed total is soft, given only the card ranks.

    Used when reading another bot's rendered hand (rank strings, no suits):
    soft means at least one ace is effectively counted as 11 in `total`.
    """
    hard_total = 0
    aces = 0
    for rank in ranks:
        if rank.upper() == "A":
            hard_total += 1
            aces += 1
        else:
            hard_total += card_points(rank)
    if aces == 0:
        return False
    return total <= 21 and (hard_total + 10 == total)


def blackjack_recommendation(player_hand, dealer_up_card):
    """Full basic-strategy recommendation (may return double/split/surrender)."""
    total = hand_value(player_hand)
    up = dealer_up_value(dealer_up_card)
    soft = is_soft_hand(player_hand)

    if can_split_cards(player_hand):
        pair = split_rank_key(player_hand[0])
        if pair in ("A", "8"):
            return "split", f"pair of {pair}s"
        if pair in ("10", "5"):
            pass
        elif pair == "9":
            if up in (7, 10, 11):
                return "stand", "9,9 vs 7/10/A"
            return "split", "9,9 vs 2-6/8-9"
        elif pair == "7":
            if 2 <= up <= 7:
                return "split", "7,7 vs 2-7"
        elif pair == "6":
            if 2 <= up <= 6:
                return "split", "6,6 vs 2-6"
        elif pair in ("2", "3"):
            if 2 <= up <= 7:
                return "split", f"{pair},{pair} vs 2-7"
        elif pair == "4":
            if 5 <= up <= 6:
                return "split", "4,4 vs 5-6"

    if soft:
        if total >= 19:
            return "stand", "soft 19+"
        if total == 18:
            if up in (3, 4, 5, 6):
                return "double", "soft 18 vs 3-6"
            if up in (9, 10, 11):
                return "hit", "soft 18 vs 9/10/A"
            return "stand", "soft 18 vs 2/7/8"
        if total == 17 and up in (3, 4, 5, 6):
            return "double", "soft 17 vs 3-6"
        if total in (15, 16) and up in (4, 5, 6):
            return "double", f"soft {total} vs 4-6"
        if total in (13, 14) and up in (5, 6):
            return "double", f"soft {total} vs 5-6"
        return "hit", "soft 17 or less"

    if total >= 17:
        return "stand", "hard 17+"
    if total == 16 and up in (9, 10, 11):
        return "surrender", "hard 16 vs 9/10/A"
    if total == 15 and up == 10:
        return "surrender", "hard 15 vs 10"
    if total in (9, 10, 11):
        if total == 9 and 3 <= up <= 6:
            return "double", "hard 9 vs 3-6"
        if total == 10 and 2 <= up <= 9:
            return "double", "hard 10 vs 2-9"
        if total == 11 and 2 <= up <= 10:
            return "double", "hard 11 vs 2-10"
    if 13 <= total <= 16:
        if 2 <= up <= 6:
            return "stand", "hard 13-16 vs 2-6"
        return "hit", "hard 13-16 vs 7-A"
    if total == 12:
        if 4 <= up <= 6:
            return "stand", "hard 12 vs 4-6"
        return "hit", "hard 12 vs 2-3/7-A"
    return "hit", "hard 11 or less"


def recommend_hit_stand(total, dealer_up, soft):
    """Basic strategy restricted to hit/stand, for interfaces with no other
    actions (Silas's blackjack accepts only those two moves)."""
    if soft:
        if total >= 19:
            return "stand"
        if total == 18:
            return "hit" if dealer_up in (9, 10, 11) else "stand"
        return "hit"

    if total >= 17:
        return "stand"
    if 13 <= total <= 16:
        return "stand" if 2 <= dealer_up <= 6 else "hit"
    if total == 12:
        return "stand" if 4 <= dealer_up <= 6 else "hit"
    return "hit"


apply_blackjack_ruleset(get_blackjack_ruleset_name())
