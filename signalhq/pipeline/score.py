"""Scorer: applies the deterministic formula in ``domain.scoring`` to every active theme.

"Now" is data time: the replay clock while a replay runs, otherwise the newest feedback
timestamp. A new score row is written only when a theme's inputs changed, so the score
history stays small and every row marks a real change.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from signalhq.domain.scoring import compute_score
from signalhq.pipeline.stage import SCORE, StageContext
from signalhq.repositories import ClaimedJob, ItemRepo, KVRepo, NewJob, ThemeRepo

REPLAY_CLOCK_KEY = "replay_clock"


def data_now(s: Session) -> datetime | None:
    """max(replay clock, newest item timestamp)."""
    candidates: list[datetime] = []
    replay = KVRepo(s).get(REPLAY_CLOCK_KEY)
    if replay:
        candidates.append(datetime.fromisoformat(replay))
    newest = ItemRepo(s).data_clock()
    if newest:
        candidates.append(newest)
    return max(candidates) if candidates else None


def _comparable(inputs_json: str) -> dict:
    d = json.loads(inputs_json)
    return {k: v for k, v in d.items() if k != "window_end"}


class ScoreStage:
    job_type = SCORE

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        with ctx.db.write() as s:
            window_end = data_now(s)
            if window_end is None:
                return []
            themes = ThemeRepo(s)
            latest = themes.latest_scores()
            computed_at = ctx.now()
            for theme_id, members in themes.member_facts().items():
                inputs = compute_score(members, window_end, ctx.settings.window_days)
                previous = latest.get(theme_id)
                current = json.dumps(inputs.to_dict(), sort_keys=True)
                if previous is not None and _comparable(previous.inputs_json) == _comparable(current):
                    continue
                themes.insert_score(theme_id, inputs, computed_at)
        return []
