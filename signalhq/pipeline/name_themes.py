"""Theme namer: Muse Spark names and summarises a theme from its most typical quotes.

It only calls the model when the theme is new, was merged, or has grown by
``rename_growth_ratio`` since it was last named, so a theme that gains one member does not
cost another call.
"""

from __future__ import annotations

from signalhq.domain.schemas import THEME_SCHEMA, THEME_SCHEMA_VERSION, ThemeNameOut
from signalhq.llm.prompts import theme_messages
from signalhq.pipeline.cluster import needs_naming
from signalhq.pipeline.stage import NAME_THEME, StageContext
from signalhq.repositories import ClaimedJob, NewJob, ThemeRepo


class NameThemeStage:
    job_type = NAME_THEME

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        theme_id = int(job.payload["theme_id"])
        force = bool(job.payload.get("force", False))
        with ctx.db.read() as s:
            themes = ThemeRepo(s)
            theme = themes.get(theme_id)
            if theme is None or theme.status != "active":
                return []
            sample = themes.naming_sample(theme_id, ctx.settings.name_sample_size)
            if theme.name and not force and not needs_naming(
                sample["size"], theme.named_at_size, ctx.settings.rename_growth_ratio
            ):
                return []
        if not sample["quotes"]:
            return []

        raw = ctx.gateway.structured(
            purpose="name_theme",
            messages=theme_messages(sample["quotes"], sample["types"], sample["areas"]),
            schema=THEME_SCHEMA,
            schema_name="theme_name",
            schema_version=THEME_SCHEMA_VERSION,
            max_tokens=ctx.settings.name_max_output_tokens,
        )
        out = ThemeNameOut.model_validate(raw)
        with ctx.db.write() as s:
            ThemeRepo(s).set_name(theme_id, out.name.strip()[:200], out.summary.strip(), sample["size"], ctx.now())
        return []
