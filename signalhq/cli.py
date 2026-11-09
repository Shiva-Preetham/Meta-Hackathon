"""Command-line entry points (CLAUDE.md §8). Run ``python -m signalhq --help``."""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from pathlib import Path

from signalhq.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "feedback_sample.jsonl"
OFFLINE_BANNER = (
    "OFFLINE DEMO MODE: no MODEL_API_KEY is set, so enrichment and theme names come from a "
    "keyword heuristic (model id offline-heuristic-v1), not Muse Spark."
)


def _settings(args: argparse.Namespace) -> Settings:
    settings = get_settings()
    updates = {}
    if getattr(args, "db", None):
        updates["db_path"] = args.db
    if getattr(args, "offline", False):
        updates["offline"] = True
    return settings.model_copy(update=updates) if updates else settings


# --- commands --------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    """API key, model ids, one strict-JSON Spark call with its cost, and the database."""
    from signalhq.db import Database
    from signalhq.llm import build_gateway
    from signalhq.repositories import LedgerRepo

    settings = _settings(args)
    db = Database(settings.db_path)
    db.create_all()
    with db.read() as s:
        LedgerRepo(s).total_cost()
    print(f"database: ok ({settings.db_path})")
    if settings.is_offline:
        print("MODEL_API_KEY: not set")
        print(OFFLINE_BANNER)
        print("Spark: NOT VERIFIED (no key). Set MODEL_API_KEY and run check again.")
        return 2

    gateway = build_gateway(db, settings)
    print(f"MODEL_API_KEY: set; base URL {settings.api_base_url}")
    try:
        models = gateway.transport.list_models()
    except Exception as exc:  # noqa: BLE001 - report any failure to the human
        print(f"models.list failed: {type(exc).__name__}: {exc}")
        print("Spark looks unreachable. Likely cause: no credit card on the Meta Model API account.")
        return 1
    print(f"models available ({len(models)}): {', '.join(models)}")
    missing = [m for m in (settings.spark_model, settings.voice_model) if m not in models]
    print(f"missing configured models: {missing or 'none'}")

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok", "echo"],
        "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}},
    }
    before = gateway.spent_usd()
    started = time.monotonic()
    try:
        out = gateway.structured(
            purpose="check",
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": f"Set ok to true and echo the word 'signal'. Nonce {time.time_ns()}."},
            ],
            schema=schema,
            schema_name="health_check",
            max_tokens=50,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"strict-JSON Spark call failed: {type(exc).__name__}: {exc}")
        return 1
    elapsed = (time.monotonic() - started) * 1000
    cost = gateway.spent_usd() - before
    print(f"strict-JSON call: {json.dumps(out)} in {elapsed:.0f} ms, cost ${cost:.6f}")
    dropped = getattr(gateway.transport, "dropped_params", set())
    print(f"optional parameters the API rejected and we dropped: {sorted(dropped) or 'none'}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from signalhq.db import Database

    settings = _settings(args)
    Database(settings.db_path).create_all()
    print(f"initialised {settings.db_path}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from signalhq.connectors import get_connector
    from signalhq.db import Database
    from signalhq.ingest import ingest

    settings = _settings(args)
    db = Database(settings.db_path)
    db.create_all()
    connector = get_connector(args.connector, brand=args.brand, limit=args.limit)
    raws = connector.read(Path(args.file))
    if args.limit and args.connector != "twcs":
        # Stop reading as soon as the limit is reached (twcs applies its own limit).
        raws = itertools.islice(raws, args.limit)
    result = ingest(db, settings, raws)
    print(f"ingested {result.created} new items, skipped {result.duplicates} duplicates")
    return 0


def cmd_ingest_audio(args: argparse.Namespace) -> int:
    from signalhq.connectors.audio import recording_id_for, turns_to_feedback
    from signalhq.db import utcnow
    from signalhq.ingest import ingest
    from signalhq.llm.voice import build_request, convert_to_wav, parse_transcript
    from signalhq.runtime import build_runtime

    settings = _settings(args)
    runtime = build_runtime(settings)
    src = Path(args.file)
    if args.transcript_json:
        payload = json.loads(Path(args.transcript_json).read_text())
        wav = src.read_bytes() if src.exists() else b""
    else:
        if settings.is_offline:
            print("ingest-audio needs MODEL_API_KEY (Voice Transcribe), or --transcript-json with a saved response.")
            return 2
        wav_path = src if src.suffix.lower() == ".wav" else convert_to_wav(src, src.with_suffix(".wav"))
        wav = wav_path.read_bytes()
        payload = runtime.gateway.transcribe(wav, build_request(settings.voice_model, settings.voice_mode))
        saved = src.with_suffix(".transcript.json")
        saved.write_text(json.dumps(payload, indent=2))
        print(f"saved raw transcription response to {saved}")
    transcript = parse_transcript(payload)
    recorded_at = utcnow()
    raws = list(
        turns_to_feedback(
            transcript,
            recording_id=recording_id_for(src, wav or src.name.encode()),
            recorded_at=recorded_at,
            account_ref=args.account_ref,
        )
    )
    result = ingest(runtime.db, settings, raws)
    print(f"{len(transcript.turns)} turns, {result.created} new call items, {result.duplicates} duplicates")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from signalhq.queue.worker import BudgetStop, WorkerPool, drain
    from signalhq.runtime import build_runtime

    settings = _settings(args)
    runtime = build_runtime(settings)
    if settings.is_offline:
        print(OFFLINE_BANNER)
    if args.drain:
        try:
            ok = drain(runtime.ctx, concurrency=args.concurrency, timeout_s=args.timeout)
        except BudgetStop as exc:
            print(f"STOPPED: {exc}")
            return 3
        print("queue drained" if ok else "timed out with work still pending")
        return 0 if ok else 1
    pool = WorkerPool(runtime.ctx, concurrency=args.concurrency)
    pool.start()
    print(f"{args.concurrency} workers running; Ctrl+C to stop")
    try:
        while pool.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        pool.stop()
    if pool.budget_stopped:
        print(f"STOPPED: {pool.budget_stopped}")
        return 3
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from signalhq.queue.worker import WorkerPool
    from signalhq.replay import ReplayDriver, load_dataset, reset_processed_data
    from signalhq.runtime import build_runtime

    settings = _settings(args)
    runtime = build_runtime(settings)
    raws = load_dataset(Path(args.file), args.connector, brand=args.brand, limit=args.limit)
    if args.reset:
        reset_processed_data(runtime.db)
    pool = WorkerPool(runtime.ctx, args.workers) if args.workers else None
    if pool:
        pool.start()
    try:
        status = ReplayDriver(runtime.db, settings, raws, args.speed).run()
    finally:
        if pool:
            pool.stop()
    print(f"replay {status.status}: {status.ingested}/{status.total} items, clock {status.clock}")
    return 0


def _serve(runtime, host: str, port: int, workers: int, replay=None) -> int:
    import uvicorn

    from signalhq.api.app import create_app
    from signalhq.queue.worker import WorkerPool

    pool = WorkerPool(runtime.ctx, workers) if workers else None
    app = create_app(runtime, pool, replay)
    if pool:
        pool.start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if pool:
            pool.stop()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from signalhq.runtime import build_runtime

    settings = _settings(args)
    runtime = build_runtime(settings)
    print(f"Signal on http://{args.host}:{args.port}  ({runtime.mode}; API docs at /docs)")
    return _serve(runtime, args.host, args.port, args.workers)


def cmd_demo(args: argparse.Namespace) -> int:
    """One command: init + load the synthetic sample + workers + serve."""
    from signalhq.connectors import JsonlConnector
    from signalhq.ingest import ingest
    from signalhq.queue.worker import drain
    from signalhq.replay import ReplayController
    from signalhq.runtime import build_runtime

    settings = _settings(args)
    runtime = build_runtime(settings)
    print(f"Signal demo: {runtime.mode}")
    if settings.is_offline:
        print(OFFLINE_BANNER)
    print("Data: synthetic sample (data/sample/feedback_sample.jsonl); revenue is simulated.")
    if args.replay:
        controller = ReplayController(runtime.db, settings)
        controller.start(SAMPLE, seconds=args.replay_seconds, reset=True)
        print(f"replaying the sample over about {args.replay_seconds:.0f} s")
    else:
        result = ingest(runtime.db, settings, JsonlConnector().read(SAMPLE))
        print(f"loaded {result.created} new items ({result.duplicates} already present); processing...")
        drain(runtime.ctx, concurrency=args.workers, timeout_s=600)
        print("processing done")
    if args.no_serve:
        if args.replay:
            from signalhq.queue.worker import WorkerPool

            pool = WorkerPool(runtime.ctx, args.workers)
            pool.start()
            while controller.running:
                time.sleep(0.5)
            pool.stop()
            drain(runtime.ctx, concurrency=args.workers, timeout_s=600)
        return 0
    print(f"dashboard: http://{args.host}:{args.port}")
    return _serve(runtime, args.host, args.port, args.workers, controller if args.replay else None)


def cmd_stats(args: argparse.Namespace) -> int:
    from signalhq.db import Database
    from signalhq.repositories import ItemRepo, JobRepo, LedgerRepo, ThemeRepo

    settings = _settings(args)
    db = Database(settings.db_path)
    db.create_all()
    with db.read() as s:
        jobs = JobRepo(s)
        ledger = LedgerRepo(s).summary()
        out = {
            "db": settings.db_path,
            "mode": "offline" if settings.is_offline else "online",
            "items_by_state": ItemRepo(s).counts_by_state(),
            "jobs_by_type": jobs.counts(),
            "dead_jobs": jobs.dead_jobs(5),
            "themes_by_status": ThemeRepo(s).counts_by_status(),
            "spend_usd": ledger["cost_usd"],
            "budget_usd": settings.budget_usd,
            "llm": ledger,
        }
    print(json.dumps(out, indent=2, default=str))
    if ledger["cost_usd"] > settings.budget_usd * 0.8:
        print(f"WARNING: spend is above 80% of the ${settings.budget_usd:.2f} budget")
    return 0


def cmd_requeue_dead(args: argparse.Namespace) -> int:
    from signalhq.db import Database, utcnow
    from signalhq.repositories import JobRepo

    settings = _settings(args)
    with Database(settings.db_path).write() as s:
        n = JobRepo(s).requeue_dead(utcnow())
    print(f"requeued {n} dead jobs")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from signalhq.eval import commands

    settings = _settings(args)
    if args.action == "sample":
        return commands.sample(settings, Path(args.out), size=args.size, seed=args.seed)
    if args.action == "label":
        from signalhq.runtime import build_runtime

        runtime = build_runtime(settings)
        print(f"Open http://{args.host}:{args.port}/label to label items (keyboard shortcuts on the page).")
        return _serve(runtime, args.host, args.port, 0)
    if args.action == "report":
        return commands.report(settings, Path(args.out))
    raise ValueError(args.action)


# --- parser ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m signalhq", description=__doc__)
    parser.add_argument("--db", help="SQLite database path (default: SIGNAL_DB_PATH or signal.db)")
    parser.add_argument("--offline", action="store_true", help="force offline demo mode")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify API key, model ids, one Spark call and the database").set_defaults(func=cmd_check)
    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="ingest a feedback file")
    p.add_argument("file")
    p.add_argument("--connector", choices=["twcs", "csv", "jsonl"], default="jsonl")
    p.add_argument("--brand", default="SpotifyCares")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ingest-audio", help="transcribe a call recording and ingest each speaker turn")
    p.add_argument("file")
    p.add_argument("--account-ref", help="customer id for the call (optional)")
    p.add_argument("--transcript-json", help="use a saved Voice Transcribe response instead of calling the API")
    p.set_defaults(func=cmd_ingest_audio)

    p = sub.add_parser("worker", help="run pipeline workers")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--drain", action="store_true", help="exit when the queue is empty")
    p.add_argument("--timeout", type=float, default=3600)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("replay", help="replay a dataset in time order at N x speed")
    p.add_argument("file")
    p.add_argument("--speed", type=float, default=3600, help="data-seconds per real second")
    p.add_argument("--connector", choices=["twcs", "csv", "jsonl"], default="jsonl")
    p.add_argument("--brand", default="SpotifyCares")
    p.add_argument("--limit", type=int)
    p.add_argument("--reset", action="store_true", help="delete processed data first (keeps cache, ledger, labels)")
    p.add_argument("--workers", type=int, default=4, help="worker threads to run alongside (0 = none)")
    p.set_defaults(func=cmd_replay)

    for name, func, help_text in (("serve", cmd_serve, "API and dashboard"), ("demo", cmd_demo, "one-command demo")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--host", default=get_settings().host)
        p.add_argument("--port", type=int, default=get_settings().port)
        p.add_argument("--workers", type=int, default=4 if name == "demo" else 0)
        p.set_defaults(func=func)
    demo = sub.choices["demo"]
    demo.add_argument("--replay", action="store_true", help="stream the sample instead of loading it at once")
    demo.add_argument("--replay-seconds", type=float, default=90)
    demo.add_argument("--no-serve", action="store_true", help="process and exit (used by tests and CI)")

    p = sub.add_parser("eval", help="evaluation: sample, label, report")
    p.add_argument("action", choices=["sample", "label", "report"])
    p.add_argument("--out", default=None)
    p.add_argument("--size", type=int, default=200)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_eval)

    sub.add_parser("stats", help="queue depth, dead jobs, spend so far").set_defaults(func=cmd_stats)
    sub.add_parser("requeue-dead", help="move dead jobs back to the queue").set_defaults(func=cmd_requeue_dead)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "eval" and args.out is None:
        args.out = {"sample": "eval/to_label.csv", "report": "eval/report.md", "label": ""}[args.action]
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
