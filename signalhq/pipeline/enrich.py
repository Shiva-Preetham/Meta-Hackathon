"""Enrich stage: one Muse Spark structured call per item, then deterministic clean-up.

Expensive work (the model call) happens outside any database transaction; the result and
the state change are written together afterwards. Re-running the job for an item that is
already enriched makes no call and returns the same follow-up job.
"""

from __future__ import annotations

import json

from signalhq.db import utcnow
from signalhq.domain.schemas import ENRICH_SCHEMA, ENRICH_SCHEMA_VERSION, EnrichmentOut
from signalhq.domain.states import ItemState
from signalhq.domain.taxonomy import normalize_product_area, verify_quote
from signalhq.llm.prompts import ENRICH_PROMPT_VERSION, enrich_messages
from signalhq.models import Enrichment
from signalhq.pipeline.stage import EMBED, ENRICH, StageContext
from signalhq.repositories import ClaimedJob, ItemRepo, NewJob


def embed_job(item_id: int) -> NewJob:
    return NewJob(EMBED, {"item_id": item_id}, dedupe_key=f"embed:{item_id}")


class EnrichStage:
    job_type = ENRICH

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        item_id = int(job.payload["item_id"])
        with ctx.db.read() as s:
            items = ItemRepo(s)
            item = items.get(item_id)
            if item is None:  # removed by a reset; nothing to do
                return []
            if items.get_enrichment(item_id) is not None:
                return [embed_job(item_id)]
            text, source = item.text, item.source

        raw = ctx.gateway.structured(
            purpose="enrich",
            messages=enrich_messages(text, source),
            schema=ENRICH_SCHEMA,
            schema_name="feedback_enrichment",
            schema_version=ENRICH_SCHEMA_VERSION,
            max_tokens=ctx.settings.enrich_max_output_tokens,
        )
        out = EnrichmentOut.model_validate(raw)
        quote, verified = verify_quote(out.key_quote, text)

        with ctx.db.write() as s:
            items = ItemRepo(s)
            if items.get_enrichment(item_id) is None:
                items.save_enrichment(
                    Enrichment(
                        item_id=item_id,
                        type=out.type,
                        product_area=normalize_product_area(out.product_area),
                        product_area_raw=out.product_area[:128],
                        severity=out.severity,
                        sentiment=out.sentiment,
                        churn_signal=out.churn_signal,
                        key_quote=quote,
                        quote_verified=verified,
                        language=out.language[:16],
                        model=ctx.gateway.model,
                        prompt_version=ENRICH_PROMPT_VERSION,
                        raw_json=json.dumps(raw, sort_keys=True),
                        created_at=utcnow(),
                    )
                )
                items.set_state(item_id, ItemState.ENRICHED)
        return [embed_job(item_id)]
