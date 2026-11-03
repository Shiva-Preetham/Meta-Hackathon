from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from signalhq.domain.scoring import MemberFact, ScoreInputs
from signalhq.models import Account, Enrichment, FeedbackItem, Theme, ThemeMember, ThemeScore
from signalhq.repositories.items import blob_to_vector, vector_to_blob


@dataclass
class ThemeRow:
    id: int
    centroid: np.ndarray
    size: int
    named_at_size: int | None
    name: str | None


class ThemeRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --- clustering writes --------------------------------------------------------------
    def active(self) -> list[ThemeRow]:
        rows = self.s.execute(select(Theme).where(Theme.status == "active").order_by(Theme.id)).scalars()
        return [
            ThemeRow(t.id, blob_to_vector(t.centroid), t.size, t.named_at_size, t.name) for t in rows
        ]

    def create(self, centroid: np.ndarray, now: datetime) -> int:
        theme = Theme(
            centroid=vector_to_blob(centroid), size=0, status="active", created_at=now, updated_at=now
        )
        self.s.add(theme)
        self.s.flush()
        return theme.id

    def theme_of(self, item_id: int) -> int | None:
        return self.s.execute(
            select(ThemeMember.theme_id).where(ThemeMember.item_id == item_id)
        ).scalar_one_or_none()

    def add_member(self, theme_id: int, item_id: int, similarity: float, now: datetime) -> None:
        self.s.add(ThemeMember(theme_id=theme_id, item_id=item_id, similarity=float(similarity), assigned_at=now))

    def set_centroid(self, theme_id: int, centroid: np.ndarray, size: int, now: datetime) -> None:
        self.s.execute(
            update(Theme)
            .where(Theme.id == theme_id)
            .values(centroid=vector_to_blob(centroid), size=size, updated_at=now)
        )

    def member_vectors(self, theme_id: int) -> tuple[list[int], np.ndarray | None]:
        from signalhq.models import Embedding

        rows = self.s.execute(
            select(ThemeMember.item_id, Embedding.vector)
            .join(Embedding, Embedding.item_id == ThemeMember.item_id)
            .where(ThemeMember.theme_id == theme_id)
        ).all()
        if not rows:
            return [], None
        return [r[0] for r in rows], np.vstack([blob_to_vector(r[1]) for r in rows])

    def merge_into(self, src_id: int, dst_id: int, now: datetime) -> None:
        self.s.execute(update(ThemeMember).where(ThemeMember.theme_id == src_id).values(theme_id=dst_id))
        self.s.execute(
            update(Theme)
            .where(Theme.id == src_id)
            .values(status="merged", merged_into=dst_id, size=0, updated_at=now)
        )

    def update_similarities(self, theme_id: int, item_ids: list[int], sims: np.ndarray) -> None:
        for item_id, sim in zip(item_ids, sims, strict=True):
            self.s.execute(
                update(ThemeMember)
                .where(ThemeMember.theme_id == theme_id, ThemeMember.item_id == item_id)
                .values(similarity=float(sim))
            )

    # --- naming -------------------------------------------------------------------------
    def get(self, theme_id: int) -> Theme | None:
        return self.s.get(Theme, theme_id)

    def set_name(self, theme_id: int, name: str, summary: str, named_at_size: int, now: datetime) -> None:
        self.s.execute(
            update(Theme)
            .where(Theme.id == theme_id)
            .values(name=name, summary=summary, named_at_size=named_at_size, updated_at=now)
        )

    def naming_sample(self, theme_id: int, n: int) -> dict[str, Any]:
        """Quotes closest to the centroid plus type and area counts, for the namer."""
        rows = self.s.execute(
            select(Enrichment.key_quote, Enrichment.type, Enrichment.product_area)
            .join(ThemeMember, ThemeMember.item_id == Enrichment.item_id)
            .where(ThemeMember.theme_id == theme_id)
            .order_by(ThemeMember.similarity.desc(), ThemeMember.item_id)
        ).all()
        return {
            "quotes": [r[0] for r in rows[:n]],
            "types": Counter(r[1] for r in rows).most_common(),
            "areas": Counter(r[2] for r in rows).most_common(),
            "size": len(rows),
        }

    # --- scoring ------------------------------------------------------------------------
    def member_facts(self) -> dict[int, list[MemberFact]]:
        rows = self.s.execute(
            select(
                ThemeMember.theme_id,
                FeedbackItem.id,
                FeedbackItem.created_at,
                FeedbackItem.account_id,
                Account.mrr_cents,
                Enrichment.severity,
                Enrichment.churn_signal,
            )
            .join(Theme, Theme.id == ThemeMember.theme_id)
            .join(FeedbackItem, FeedbackItem.id == ThemeMember.item_id)
            .join(Enrichment, Enrichment.item_id == FeedbackItem.id)
            .outerjoin(Account, Account.id == FeedbackItem.account_id)
            .where(Theme.status == "active")
        ).all()
        facts: dict[int, list[MemberFact]] = defaultdict(list)
        for theme_id, item_id, created_at, account_id, mrr, severity, churn in rows:
            facts[theme_id].append(
                MemberFact(item_id, created_at, account_id, int(mrr or 0), severity, bool(churn))
            )
        return dict(facts)

    def insert_score(self, theme_id: int, inputs: ScoreInputs, computed_at: datetime) -> None:
        self.s.add(
            ThemeScore(
                theme_id=theme_id,
                computed_at=computed_at,
                window_end=inputs.window_end,
                mentions_recent=inputs.mentions_recent,
                mentions_prev=inputs.mentions_prev,
                growth=inputs.growth,
                revenue_at_risk_cents=inputs.revenue_at_risk_cents,
                severity_weight=inputs.severity_weight,
                score=inputs.score,
                inputs_json=json.dumps(inputs.to_dict(), sort_keys=True),
            )
        )

    def latest_scores(self) -> dict[int, ThemeScore]:
        latest = select(func.max(ThemeScore.id)).group_by(ThemeScore.theme_id).scalar_subquery()
        rows = self.s.execute(select(ThemeScore).where(ThemeScore.id.in_(latest))).scalars()
        return {r.theme_id: r for r in rows}

    def score_history(self, theme_id: int, limit: int = 200) -> list[ThemeScore]:
        rows = self.s.execute(
            select(ThemeScore)
            .where(ThemeScore.theme_id == theme_id)
            .order_by(ThemeScore.id.desc())
            .limit(limit)
        ).scalars()
        return list(reversed(list(rows)))

    # --- read views ---------------------------------------------------------------------
    def list_active(self) -> list[Theme]:
        return list(self.s.execute(select(Theme).where(Theme.status == "active")).scalars())

    def counts_by_status(self) -> dict[str, int]:
        rows = self.s.execute(select(Theme.status, func.count()).group_by(Theme.status)).all()
        return {status: n for status, n in rows}

    def top_severity(self, theme_ids: list[int]) -> dict[int, str]:
        order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        rows = self.s.execute(
            select(ThemeMember.theme_id, Enrichment.severity)
            .join(Enrichment, Enrichment.item_id == ThemeMember.item_id)
            .where(ThemeMember.theme_id.in_(theme_ids))
        ).all()
        best: dict[int, str] = {}
        for theme_id, severity in rows:
            if theme_id not in best or order[severity] > order[best[theme_id]]:
                best[theme_id] = severity
        return best

    def quotes(self, theme_id: int, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.s.execute(
            select(
                FeedbackItem.id,
                FeedbackItem.source,
                FeedbackItem.created_at,
                FeedbackItem.text,
                Enrichment.key_quote,
                Enrichment.quote_verified,
                Enrichment.severity,
                Enrichment.churn_signal,
                Enrichment.type,
                Account.plan,
                Account.mrr_cents,
                ThemeMember.similarity,
            )
            .join(ThemeMember, ThemeMember.item_id == FeedbackItem.id)
            .join(Enrichment, Enrichment.item_id == FeedbackItem.id)
            .outerjoin(Account, Account.id == FeedbackItem.account_id)
            .where(ThemeMember.theme_id == theme_id)
            # Evidence first from paying, at-risk customers, then the most typical.
            .order_by(
                Enrichment.churn_signal.desc(),
                func.coalesce(Account.mrr_cents, 0).desc(),
                ThemeMember.similarity.desc(),
            )
            .limit(limit)
        ).all()
        return [
            {
                "item_id": r[0],
                "source": r[1],
                "created_at": r[2].isoformat(),
                "text": r[3],
                "quote": r[4],
                "quote_verified": bool(r[5]),
                "severity": r[6],
                "churn_signal": bool(r[7]),
                "type": r[8],
                "plan": r[9] or "unknown",
                "mrr_dollars": (r[10] or 0) / 100,
                "similarity": round(float(r[11]), 4),
            }
            for r in rows
        ]

    def breakdown(self, theme_id: int) -> dict[str, list[tuple[str, int]]]:
        rows = self.s.execute(
            select(Enrichment.type, Enrichment.product_area, FeedbackItem.source, Account.plan)
            .join(ThemeMember, ThemeMember.item_id == Enrichment.item_id)
            .join(FeedbackItem, FeedbackItem.id == Enrichment.item_id)
            .outerjoin(Account, Account.id == FeedbackItem.account_id)
            .where(ThemeMember.theme_id == theme_id)
        ).all()
        return {
            "types": Counter(r[0] for r in rows).most_common(),
            "areas": Counter(r[1] for r in rows).most_common(),
            "sources": Counter(r[2] for r in rows).most_common(),
            "plans": Counter(r[3] or "unknown" for r in rows).most_common(),
        }

    def daily_counts(self, theme_ids: list[int], end: datetime, days: int) -> dict[int, list[int]]:
        """Mentions per data-day for the ``days`` days ending at ``end`` (inclusive)."""
        start = end - timedelta(days=days)
        rows = self.s.execute(
            select(ThemeMember.theme_id, FeedbackItem.created_at)
            .join(FeedbackItem, FeedbackItem.id == ThemeMember.item_id)
            .where(ThemeMember.theme_id.in_(theme_ids), FeedbackItem.created_at > start, FeedbackItem.created_at <= end)
        ).all()
        out = {tid: [0] * days for tid in theme_ids}
        for theme_id, created_at in rows:
            idx = days - 1 - int((end - created_at).total_seconds() // 86400)
            if 0 <= idx < days:
                out[theme_id][idx] += 1
        return out

    def assignments(self) -> dict[int, int]:
        """item_id -> theme_id for all current members."""
        return dict(self.s.execute(select(ThemeMember.item_id, ThemeMember.theme_id)).all())
