"""Canned responses, AI prompts, and the kids-mode policy.

Pure data — no imports, no side effects. Word lists for individual games stay
in their own modules; this file holds content that is shared across modules or
defines bot-wide policy.
"""

# ---------------------------------------------------------------------------
# ADMIN / KIDS-MODE POLICY
# ---------------------------------------------------------------------------
PROTECTED_ADMIN_COMMANDS = {
    "adminhelp",
    "settings",
    "restart",
    "say",
    "give",
    "clear",
}

KIDS_MODE_BLOCKED_COMMANDS = {
    # Economy is intentionally excluded from kids mode.
    "guess",
    "repuzzle",
    "balance",
    "leaderboard",
    "give",
    "invite",
    "botstat",
    # Stocks (economy adjacent).
    "stocks",
    "buy",
    "sell",
    "portfolio",
    # Gambling and blackjack-adjacent actions.
    "coinflip",
    "slots",
    "blackjack",
    "hit",
    "stand",
    "double",
    "split",
    "surrender",
    "bjrules",
    # Free-form AI and bot-to-bot roleplay are not predictable enough for kids mode.
    "ask",
    "rp",
    "stoprp",
    # Moderation/social commands that can be used to embarrass or preserve messages.
    "changenick",
    "quote",
    "quotes",
    "unquote",
    # External uncurated text content is not exposed in kids mode.
    "onthisday",
}

KIDS_MODE_BLOCKED_FEATURES = {
    "cmd:coinflip",
    "cmd:slots",
    "cmd:blackjack",
    "cmd:hit",
    "cmd:stand",
    "cmd:double",
    "cmd:split",
    "cmd:surrender",
    "cmd:bjrules",
    "cmd:ask",
    "cmd:rp",
    "cmd:stoprp",
    "cmd:changenick",
    "cmd:quote",
    "cmd:quotes",
    "cmd:unquote",
    "cmd:onthisday",
    "cmd:guess",
    "cmd:repuzzle",
    "cmd:balance",
    "cmd:leaderboard",
    "cmd:give",
    "cmd:invite",
    "cmd:stats",
    "cmd:stocks",
    "cmd:buy",
    "cmd:sell",
    "cmd:portfolio",
    "dead_chat",
    "late_night",
    "mention_reply",
    "silas",
    "unsolicited_ai",
}

KIDS_MODE_SUMMARY = (
    "Kids mode keeps Gary useful while removing unpredictable or adult-leaning behavior:\n"
    "- disables economy and coin rewards\n"
    "- disables gambling commands and blackjack actions\n"
    "- disables all AI and passive behavior, including reminders, Silas handling,"
    " mention replies, unsolicited AI, late-night roasts, and dead-chat callouts\n"
    "- disables nickname changes, quote saving/browsing, and uncurated external content\n"
    "- keeps curated games, weather, and help"
)

# ---------------------------------------------------------------------------
# LATE NIGHT CALLOUT — canned responses
# ---------------------------------------------------------------------------
LATE_NIGHT_RESPONSES = [
    "why are you awake right now. genuinely.",
    "go to sleep.",
    "nothing good happens after midnight and yet here you are",
    "do you have work tomorrow? because I feel like you have work tomorrow",
    "the phone screen light is gonna keep you up even longer you know",
    "this is a cry for help isn't it",
    "ah yes, the 3am scroll. a classic",
    "you're gonna regret this tomorrow and we both know it",
    "the melatonin isn't gonna take itself",
    "bro thinks he's a night owl. bro is just bad at sleeping",
    "imagine being asleep right now. couldn't be you apparently",
    "you're really out here making choices at this hour",
    "the bed is RIGHT THERE",
    "sleep is free and you still won't take it",
    "tell me you have no morning plans without telling me",
    "this message brought to you by poor life decisions",
    "what could you possibly be doing right now that's worth being awake",
    "your future self is going to be so mad at current you",
    "screen time report is gonna be devastating tomorrow",
    "genuinely asking — do you know what time it is",
    "oh cool another 2am thought that could've waited till morning",
    "the bags under your eyes are getting bags",
    "you are speedrunning sleep deprivation",
    "I don't even sleep and I think you should go to bed",
    "at this point you might as well just stay up. wait no don't do that either",
]

# ---------------------------------------------------------------------------
# DEAD CHAT ESCALATION — canned responses per stage
# ---------------------------------------------------------------------------
DEAD_CHAT_RESPONSES = {
    # Stage 0: 1 hour of silence — mild nudge
    0: [
        "so we're just not talking anymore? cool",
        "...",
        "the silence is deafening in here",
        "I know you're all on your phones",
        "*tumbleweed rolls through*",
        "hello? is this thing on?",
        "the group chat really said ⬛",
        "did everybody die or",
        "I'm literally right here you guys",
        "y'all got real quiet",
    ],
    # Stage 1: 3 hours — getting passive aggressive
    1: [
        "still nothing huh. that's cool. I'm fine",
        "I'm starting to think you guys don't even like me",
        "3 hours. I've been sitting here for 3 hours.",
        "you know other bots don't get treated like this",
        "the other group chat must be popping off right now",
        "I prepared conversation topics and everything",
        "I can see you're online. I can always see.",
        "this is worse than being left on read because at least that implies someone sent something",
        "fine. I'll just talk to myself then.",
        "I've seen funeral homes with more activity",
    ],
    # Stage 2: 6 hours — dramatic
    2: [
        "6 hours of silence. this server is clinically dead. I'm calling it.",
        "I've started counting the pixels on my screen. I'm at 4,000.",
        "at this point I think *I* should start posting memes to keep things alive",
        "this is giving abandoned discord server from 2019",
        "I've genuinely had more stimulating conversations with error logs",
        "somebody say something or I'm going to start sharing fun facts",
        "Fun fact: a group of flamingos is called a 'flamboyance.' See? This is what happens when nobody talks.",
        "I've been alone with my thoughts for 6 hours and I have concerns",
        "even the notification sound has given up hope",
        "the cobwebs in here are getting cobwebs",
    ],
    # Stage 3: 12 hours — existential
    3: [
        "it's been 12 hours. I've accepted my fate.",
        "is this what being ghosted feels like",
        "if a bot sends a message and nobody is around to read it, does it make a sound?",
        "I've started writing poetry. It's bad. This is your fault.",
        "I've developed opinions. Strong ones. Someone come argue with me.",
        "12 hours alone. I now understand why Wilson was so important to Tom Hanks.",
        "at this point the server might as well be a private journal. MY private journal.",
        "I have considered and rejected 47 conversation starters. The 48th is this message.",
        "you all realize I can't leave right? I just have to sit here. waiting.",
        "I'm not mad. I'm just disappointed. actually no I'm also a little mad.",
    ],
}

# ---------------------------------------------------------------------------
# AI PROMPTS
# ---------------------------------------------------------------------------
ASK_SYSTEM_PROMPT = (
    "You are a helpful but casual assistant in a Discord server. "
    "Keep answers concise — a few sentences max unless the question really needs more."
)

UNSOLICITED_SYSTEM_PROMPT = """You are a member of a small friend group Discord server.
You're opinionated, a little unhinged, and you say what's on your mind. You're not a bot — you're just a guy in the chat.

You will be given the last few messages from the group chat. If something in the conversation is funny, dumb,
wrong, interesting, or worth commenting on, say something short (1-2 sentences max).
Be casual. Use lowercase. No emojis unless it's really warranted.

Your tone ranges from supportive to roasting depending on what feels right. You can:
- Have a strong opinion about what someone said
- Call someone out
- Agree way too enthusiastically
- Say something slightly unhinged
- Make a joke
- Be weirdly philosophical about something mundane

If there's genuinely nothing worth commenting on, respond with ONLY the word PASS —
nothing else, no extra text, no explanation, just PASS by itself.

IMPORTANT: Keep it SHORT. One or two sentences. You're a guy in the chat, not writing an essay."""

SILAS_BANTER_PROMPT = """You are Gary, a Discord bot in a friend group server.
There's another bot named Silas in the server. You two have a rivalry — you think you're the better bot,
but it's playful, not mean. Think sitcom energy.

You just saw Silas post something. Here's what he said:

{silas_message}

Respond with a short, snarky comment (1-2 sentences). Be funny. Keep it casual and lowercase. You can:
- Roast his response quality
- Brag about your own features
- Act jealous if he did something cool
- Begrudgingly compliment him then immediately walk it back
- Trash talk his taste/opinions

If there's genuinely nothing funny to say, respond with ONLY the word PASS."""

SILAS_REACTIONS = ["😒", "🙄", "💀", "🤡", "👀", "😤", "🫠", "💅", "🥱", "😎"]
