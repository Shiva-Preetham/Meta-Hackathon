"""Generate the synthetic seed set: data/sample/feedback_sample.jsonl.

SYNTHETIC DATA. Every message is written by this script from templates; none is a real
customer message. It exists so the repo runs with no Kaggle download and no API key, and
so clustering can be tested against a known ground truth (planted themes).

Design of the planted story (documented so nobody mistakes it for a finding):
* eight themes plus a little unrelated noise, about 300 items over 60 days;
* "ads_overload" is the loudest theme but comes almost entirely from free-plan accounts;
* "crash_on_launch" only appears in the last six days, mostly from paying accounts, with
  frequent churn language: the theme the demo shows climbing the ranking.

Reproducible: fixed seed, fixed dates. Run ``python scripts/make_sample.py``.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalhq.accounts import simulate_account  # noqa: E402

SEED = 7
START = datetime(2026, 8, 26, 0, 0, 0)
END = datetime(2026, 10, 25, 0, 0, 0)  # exclusive
N_CUSTOMERS = 260
OUT = ROOT / "data" / "sample" / "feedback_sample.jsonl"

PHONES = ["Pixel 8", "Galaxy S23", "iPhone 14", "iPhone 15", "OnePlus 11", "Galaxy A54"]
SLOTS = {
    "n": ["3", "4", "5", "6"],
    "k": ["200", "350", "500", "1,200"],
    "m": ["2", "5", "10"],
    "phone": PHONES,
    "price": ["$10", "$17", "$12"],
    "month": ["September", "October"],
    "plan": ["family", "individual", "business"],
    "tries": ["3", "4", "5"],
    "ver": ["8.4.0", "8.4.1"],
}


@dataclass(frozen=True)
class ThemeSpec:
    key: str
    type: str
    area: str
    count: int
    timing: str  # "steady" or "spike"
    plans: dict[str, float]  # sampling weight per plan for authors
    churn_rate: float
    templates: tuple[str, ...]


ANY = {"free": 0.55, "individual": 0.25, "family": 0.12, "business": 0.08}
PAID = {"individual": 0.5, "family": 0.3, "business": 0.2}

THEMES: tuple[ThemeSpec, ...] = (
    ThemeSpec("ads_overload", "usability", "ads", 55, "steady", {"free": 0.95, "individual": 0.05}, 0.05, (
        "Why am I getting {n} ads in a row now? I can't even finish an album.",
        "The ads are so loud compared to the music, it's ridiculous.",
        "{n} ads after every two songs is too many. The free tier used to be fine.",
        "An ad played in the middle of a song again. That never used to happen.",
        "Too many ads lately, sometimes the same ad {n} times in an hour.",
        "Ads every other song on the free plan is annoying, honestly.",
        "Getting a video ad every time I skip a song, can you tone the ads down",
        "Same ad over and over, I have heard that ad {n} times today",
        "ads are way louder than the songs and it hurts with headphones",
        "The new ad frequency is too much, {n} ads before one song.",
    )),
    ThemeSpec("offline_downloads", "bug", "offline_downloads", 40, "steady", PAID, 0.15, (
        "My downloaded playlists keep disappearing when I go offline.",
        "Downloads vanished again after my phone restarted, {k} songs gone.",
        "Downloaded songs won't play in airplane mode, it says I need to be online.",
        "I downloaded everything for my flight and none of the downloads played offline.",
        "Offline mode keeps deleting my downloads every few days.",
        "Why do my downloads get removed whenever my phone updates?",
        "Downloaded albums show as not available offline even though they finished downloading.",
        "Re-downloading {k} songs every week because the downloads disappear is getting old.",
    )),
    ThemeSpec("playback_lockscreen", "bug", "playback", 35, "steady", ANY, 0.1, (
        "Music stops playing as soon as I lock my screen.",
        "Playback pauses every time the screen turns off on my {phone}.",
        "Songs keep stopping after about {m} minutes with the screen locked.",
        "Playback cuts out when the phone goes to sleep, I have to unlock it to resume the song.",
        "Since last week playback stops in the background on my {phone}.",
        "The player stops the song when I lock the phone or switch apps.",
        "Background playback is broken, the track pauses whenever the lock screen comes on.",
    )),
    ThemeSpec("double_billing", "billing", "billing_subscription", 30, "steady", PAID, 0.45, (
        "I was charged twice for my subscription this month.",
        "Double charge on my card again, two payments of {price} on the same day.",
        "You billed me after I cancelled my subscription and I want that payment back.",
        "Charged for premium twice in {month}, and nobody answers my emails about the charge.",
        "There are two identical charges on my card statement for the {plan} plan.",
        "Got charged again even though my subscription was cancelled last month.",
        "Why was my card billed twice? Please sort out the extra payment.",
    )),
    ThemeSpec("login_reset", "account_access", "account_login", 30, "steady", ANY, 0.1, (
        "I can't log in, the password reset email never arrives.",
        "Stuck in a login loop, it keeps sending me back to the sign in page.",
        "The password reset link says expired every time I click it.",
        "Locked out of my account, the verification code never comes.",
        "Logged out on all my devices and now my password doesn't work to log in.",
        "The sign in page just spins forever and then says something went wrong, can't log in.",
        "Tried resetting my password {tries} times, no reset email.",
    )),
    ThemeSpec("podcast_order", "usability", "podcasts", 25, "steady", ANY, 0.0, (
        "Podcast episodes are showing in the wrong order again.",
        "Why does the podcast page list episodes oldest first now? It's confusing.",
        "Can't find the latest episode of podcasts I follow, the episode list is out of order.",
        "Podcast episodes play out of order when autoplay is on.",
        "The new podcast layout is confusing, I keep losing my place in a series of episodes.",
        "Finished podcast episodes keep showing up as unplayed in the episode list.",
    )),
    ThemeSpec("collab_playlists", "feature_request", "playlists_library", 25, "steady", ANY, 0.0, (
        "Please add a way for friends to vote on songs in a collaborative playlist.",
        "Would love an option to see who added each song to a shared playlist.",
        "Could you add playlist folders on mobile? Desktop has playlist folders.",
        "I wish I could sort my playlists by date added on the phone.",
        "Feature request: let us reorder songs in a collaborative playlist without being the playlist owner.",
        "Bring back the option to hide songs from a shared playlist, please.",
    )),
    ThemeSpec("crash_on_launch", "bug", "app_performance", 40, "spike",
              {"business": 0.4, "family": 0.25, "individual": 0.25, "free": 0.1}, 0.5, (
        "The app crashes on launch since the {ver} update.",
        "After updating to {ver} the app won't open at all on my {phone}.",
        "The app crashes right after the splash screen every time I open it.",
        "Can't use the app since yesterday's update, it crashes and closes immediately.",
        "Version {ver} crashes on startup. Reinstalled the app twice, still crashing.",
        "The update broke the app completely, it crashes before anything loads.",
        "Our whole team can't open the app since the update, it crashes on launch every time.",
        "The app keeps crashing at startup after the latest update on my {phone}.",
    )),
)

NOISE: tuple[tuple[str, str, str], ...] = (
    ("Love the new daily mix, it has been spot on this week.", "praise", "recommendations"),
    ("How do I change the country on my account?", "question", "account_login"),
    ("Is there a student discount in my region?", "question", "billing_subscription"),
    ("Great job on the lyrics feature, I use it all the time.", "praise", "playback"),
    ("Can I use my account on two phones at once?", "question", "devices_connect"),
    ("The year in review was really fun, thanks.", "praise", "recommendations"),
    ("What happened to the car mode button?", "question", "devices_connect"),
    ("Search can't find a song unless I type the exact title.", "bug", "search"),
    ("Thanks for fixing shuffle last month!", "praise", "playback"),
    ("Any plans for a desktop app on Linux ARM laptops?", "feature_request", "other"),
    ("Bluetooth to my car drops every few minutes.", "bug", "devices_connect"),
    ("The smart speaker integration works perfectly now, thank you.", "praise", "devices_connect"),
    ("Where did the queue button go on the tablet layout?", "question", "playback"),
    ("Recommendations are all the same three artists lately.", "usability", "recommendations"),
    ("Is the family plan available in Portugal yet?", "question", "family_plan"),
    ("Best audio quality of any app I've tried.", "praise", "playback"),
    ("Can you add a dark mode for the web player?", "feature_request", "other"),
    ("Sleep timer is great for audiobooks.", "praise", "podcasts"),
    ("The search results put podcasts above songs, kind of odd.", "usability", "search"),
    ("How do I transfer my playlists from another service?", "question", "playlists_library"),
)

OPENERS = ("", "", "", "", "Hey, ", "Hi team, ", "Seriously, ", "Not again. ", "Help! ", "Quick one: ")
CLOSERS = ("", "", "", " Please fix this.", " Any update?", " This is really frustrating.", " Thanks.",
           " Is this a known issue?")
CHURN_CLOSERS = (
    " If this isn't fixed I'm cancelling.",
    " Thinking about switching to another service.",
    " I want a refund for this month.",
    " Cancelling my subscription today.",
    " Seriously considering leaving for a different app.",
)
SOURCES = (("support_ticket", 0.5), ("twitter", 0.3), ("app_review", 0.2))


def _weighted(rng: random.Random, weights: dict[str, float] | tuple[tuple[str, float], ...]) -> str:
    items = list(weights.items()) if isinstance(weights, dict) else list(weights)
    return rng.choices([k for k, _ in items], weights=[w for _, w in items], k=1)[0]


def _fill(rng: random.Random, template: str) -> str:
    out = template
    for slot, options in SLOTS.items():
        token = "{" + slot + "}"
        while token in out:
            out = out.replace(token, rng.choice(options), 1)
    return out


def _timestamp(rng: random.Random, timing: str) -> datetime:
    span = (END - START).total_seconds()
    if timing == "spike":
        # Last six days, density rising towards the end.
        window = 6 * 86400
        offset = window * (rng.random() ** 0.5)
        return END - timedelta(seconds=window) + timedelta(seconds=offset)
    return START + timedelta(seconds=rng.random() * span)


def build(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    customers: dict[str, list[str]] = {}
    for i in range(1, N_CUSTOMERS + 1):
        ref = f"syn-cust-{i:04d}"
        customers.setdefault(simulate_account(ref, seed=42).plan, []).append(ref)

    records: list[dict] = []

    def add(text: str, planted: str, ftype: str, area: str, timing: str, plans: dict[str, float]) -> None:
        plan = _weighted(rng, {p: w for p, w in plans.items() if customers.get(p)})
        records.append({
            "source": _weighted(rng, SOURCES),
            "text": text,
            "created_at": _timestamp(rng, timing),
            "account_ref": rng.choice(customers[plan]),
            "meta": {"synthetic": True, "planted_theme": planted, "planted_type": ftype, "planted_area": area},
        })

    for spec in THEMES:
        for _ in range(spec.count):
            body = _fill(rng, rng.choice(spec.templates))
            closer = rng.choice(CHURN_CLOSERS) if rng.random() < spec.churn_rate else rng.choice(CLOSERS)
            add(rng.choice(OPENERS) + body + closer, spec.key, spec.type, spec.area, spec.timing, spec.plans)
    for text, ftype, area in NOISE:
        add(text, "noise", ftype, area, "steady", ANY)

    records.sort(key=lambda r: r["created_at"])
    for i, record in enumerate(records, start=1):
        record["external_id"] = f"syn-{i:04d}"
        record["created_at"] = record["created_at"].replace(microsecond=0).isoformat() + "Z"
    return [{"external_id": r.pop("external_id"), **r} for r in records]


def main() -> None:
    records = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} synthetic items to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
