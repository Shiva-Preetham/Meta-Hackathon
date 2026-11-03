"""Embed stage: local embedding of the key quote plus the item text."""

from __future__ import annotations

from signalhq.domain.states import ItemState
from signalhq.pipeline.stage import CLUSTER, EMBED, StageContext
from signalhq.repositories import ClaimedJob, ItemRepo, NewJob


def cluster_job(item_id: int) -> NewJob:
    return NewJob(CLUSTER, {"item_id": item_id}, dedupe_key=f"cluster:{item_id}")


def embedding_text(text: str, key_quote: str, product_area: str | None = None, anchor_reps: int = 0) -> str:
    """What gets embedded (ADR-004, ADR-005).

    * The key quote is put in front of the text so the embedding leans towards the part of
      the message that states the problem, not greetings or order numbers.
    * The normalised product area is appended ``anchor_reps`` times as a single token
      (e.g. ``areaofflinedownloads``). Themes live inside product areas, so this pulls
      same-area items together and keeps different areas apart, while the words still
      separate themes within an area. "other" carries no information and is not anchored.
    """
    parts = [key_quote, text]
    if product_area and product_area != "other" and anchor_reps > 0:
        parts.append(" ".join(["area" + product_area.replace("_", "")] * anchor_reps))
    return "\n".join(parts)


class EmbedStage:
    job_type = EMBED

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        item_id = int(job.payload["item_id"])
        with ctx.db.read() as s:
            items = ItemRepo(s)
            item = items.get(item_id)
            if item is None:
                return []
            if items.get_embedding(item_id) is not None:
                return [cluster_job(item_id)]
            enrichment = items.get_enrichment(item_id)
            if enrichment is None:
                raise RuntimeError(f"item {item_id} has no enrichment; cannot embed yet")
            text = embedding_text(
                item.text, enrichment.key_quote, enrichment.product_area, ctx.settings.embed_area_anchor
            )

        vector = ctx.embedder.embed([text])[0]

        with ctx.db.write() as s:
            items = ItemRepo(s)
            if items.get_embedding(item_id) is None:
                items.save_embedding(item_id, ctx.embedder.name, vector)
                items.set_state(item_id, ItemState.EMBEDDED)
        return [cluster_job(item_id)]
