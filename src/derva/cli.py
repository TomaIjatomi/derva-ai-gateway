"""The ``derva`` command line."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from derva.evals.datasets import Split, Task

app = typer.Typer(help="DERVA: Decide, Evaluate, Route, Verify, Authorize.", no_args_is_help=True)
eval_app = typer.Typer(help="Run and compare decider evaluations.", no_args_is_help=True)
audit_app = typer.Typer(help="Check and anchor the audit log.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
keys_app = typer.Typer(help="Manage caller keys.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
app.add_typer(keys_app, name="keys")


@app.command("mock-server")
def mock_server(host: str = "127.0.0.1", port: int = 8100) -> None:
    """Serve the mock decision API and demo chat model (not real models)."""
    import uvicorn

    uvicorn.run("derva.mock_decider.server:create_app", factory=True, host=host, port=port)


@app.command()
def check(
    text: str,
    decider: Annotated[
        str, typer.Option(help="rules, mock, jev, clef, llm, decision:<model>")
    ] = "rules",
    team: Annotated[list[str], typer.Option(help="Teams the caller belongs to.")] = ["wealth-a"],  # noqa: B006
) -> None:
    """Run one message through the gateway checks and print the outcome."""
    from derva.evals.runner import build_decider, golden_pipeline
    from derva.pipeline import GatewayRequest
    from derva.resolution import Caller

    pipeline = golden_pipeline(build_decider(decider))
    caller = Caller(key_id="cli", owner="cli@example.ie", teams=team)
    result = asyncio.run(
        pipeline.check_request(
            GatewayRequest(caller=caller, messages=[{"role": "user", "content": text}])
        )
    )
    typer.echo(
        json.dumps(
            {
                "outcome": result.outcome.value,
                "rule": result.policy.rule if result.policy else None,
                "reasons": result.policy.reasons if result.policy else [],
                "references": result.references,
                "reply": result.reply,
                "decider": result.decision.decider if result.decision else None,
                "fallbacks": result.decision.fallbacks if result.decision else [],
            },
            indent=2,
        )
    )


@eval_app.command("run")
def eval_run(
    task: Task,
    decider: Annotated[
        str, typer.Option(help="rules, mock, jev, clef, llm, decision:<model>, llm:<model>")
    ] = "rules",
    split: Annotated[Split, typer.Option(help="Report on heldout; tune on tuning.")] = "heldout",
    full: Annotated[
        bool, typer.Option(help="Use the full public dataset, not the committed sample.")
    ] = False,
    limit: Annotated[int | None, typer.Option(help="Score only the first N examples.")] = None,
    concurrency: int = 8,
    cache: Annotated[Path | None, typer.Option(help="SQLite file to cache answers in.")] = None,
    rpm: Annotated[
        float | None,
        typer.Option(
            envvar="DERVA_EVAL_RPM",
            help="Start at most this many decider calls a minute, e.g. 15 for Gemini's free tier.",
        ),
    ] = None,
) -> None:
    """Score a decider on a dataset and write results/runs/<run_id>/."""
    from derva.evals.runner import run

    summary, _ = asyncio.run(
        run(
            task,
            decider,
            split=split,
            full=full,
            limit=limit,
            concurrency=concurrency,
            cache=cache,
            rpm=rpm,
        )
    )
    if summary.synthetic_decider:
        typer.secho(
            "Mock decider: this run checks the harness. Do not report it as a result.", fg="yellow"
        )
    typer.echo(_row(summary.model_dump()))
    typer.echo(f"results/runs/{summary.run_id}/")


@eval_app.command("gate")
def eval_gate(
    run_dir: Path,
    mode: Annotated[str, typer.Option(help="regression or adoption")] = "regression",
    baseline: Annotated[
        Path | None, typer.Option(help="Baseline summary.json or run directory.")
    ] = None,
    criteria: Path = Path("evals/criteria.yaml"),
) -> None:
    """Check a run against the criteria; exit 1 on any failure."""
    from derva.evals.gate import evaluate, load_criteria
    from derva.evals.runner import load_summary

    if mode not in ("regression", "adoption"):
        raise typer.BadParameter("mode must be regression or adoption")
    summary = load_summary(run_dir)
    base = load_summary(baseline) if baseline else None
    checks = evaluate(summary, mode, base, load_criteria(criteria))  # type: ignore[arg-type]
    for c in checks:
        typer.echo(c.describe())
    if not checks:
        typer.echo(f"No {mode} criteria for {summary.task}.")
    if any(not c.passed for c in checks):
        raise typer.Exit(1)


_COLUMNS = [
    ("task", "task"),
    ("split", "split"),
    ("decider", "decider"),
    ("n", "n"),
    ("accuracy", "acc"),
    ("macro_f1", "F1"),
    ("ece", "ECE"),
    ("brier", "Brier"),
    ("coverage_at_95", "auto@95"),
    ("latency_p95_ms", "p95 ms"),
    ("cost_per_1k_usd", "$/1k"),
]


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.0f}"
    return str(value)


def _row(record: dict[str, object]) -> str:
    cells = [_fmt(record.get(k)) for k, _ in _COLUMNS]
    if record.get("synthetic_decider"):
        cells[2] += " (mock)"
    return "| " + " | ".join(cells) + " |"


@eval_app.command("report")
def eval_report(
    log: Path = Path("results/log.jsonl"),
    task: Annotated[str | None, typer.Option()] = None,
    include_mock: Annotated[bool, typer.Option(help="Include mock-decider runs.")] = False,
) -> None:
    """Print the results log as a Markdown table, newest run per decider and split."""
    rows: dict[tuple[str, str, str], dict[str, object]] = {}
    for line in log.read_text().splitlines():
        record = json.loads(line)
        if task and record["task"] != task:
            continue
        if record.get("synthetic_decider") and not include_mock:
            continue
        rows[(record["task"], record["split"], record["decider"])] = record
    typer.echo("| " + " | ".join(h for _, h in _COLUMNS) + " |")
    typer.echo("|" + "---|" * len(_COLUMNS))
    for key in sorted(rows):
        typer.echo(_row(rows[key]))


@eval_app.command("plot")
def eval_plot(runs: list[Path], out: Path = Path("results/plots/latest.png")) -> None:
    """Draw the reliability diagram and automation curve for one or more runs."""
    from derva.evals.plots import plot_runs
    from derva.evals.runner import load_summary

    typer.echo(plot_runs([load_summary(r) for r in runs], out))


@audit_app.command("migrate")
def audit_migrate(
    url: Annotated[
        str, typer.Option(envvar="DERVA_OWNER_URL", help="Schema owner's database URL.")
    ],
    grant_to: Annotated[str, typer.Option(help="Login role that writes events.")] = "derva_app",
) -> None:
    """Apply sql/audit_schema.sql as the schema owner and grant the writer role."""
    import re

    from sqlalchemy import create_engine, text

    from derva.config import HOME

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", grant_to):
        raise typer.BadParameter("role name must be a plain identifier")
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:
        conn.exec_driver_sql((HOME / "sql" / "audit_schema.sql").read_text())
        conn.execute(text(f"GRANT derva_audit_writer TO {grant_to}"))
    typer.echo(f"Schema applied; {grant_to} can insert and read audit events only.")


@audit_app.command("verify")
def audit_verify(
    url: Annotated[str, typer.Option(envvar="DERVA_AUDIT_URL")] = "sqlite:///derva_audit.db",
    anchor: Annotated[
        str | None, typer.Option(help="Anchor file, or gs://bucket/prefix for the newest anchor.")
    ] = None,
) -> None:
    """Recompute every hash and link; with an anchor, check nothing was cut off the end."""
    from derva.anchor import read_anchor
    from derva.audit import AuditStore

    store = AuditStore(url, create_schema=False)
    result = store.verify(read_anchor(anchor) if anchor else None)
    typer.echo(result.model_dump_json(indent=2))
    if not result.ok:
        raise typer.Exit(1)


@audit_app.command("anchor")
def audit_anchor(
    target: Annotated[str, typer.Argument(help="A file path, or gs://bucket/prefix.")],
    url: Annotated[str, typer.Option(envvar="DERVA_AUDIT_URL")] = "sqlite:///derva_audit.db",
) -> None:
    """Copy the chain head to a file or a retention-locked bucket."""
    from derva.anchor import write_anchor
    from derva.audit import AuditStore

    head = AuditStore(url, create_schema=False).head()
    typer.echo(f"{write_anchor(head, target)} {json.dumps(head)}")


@keys_app.command("new")
def keys_new(
    alias: Annotated[str, typer.Option(help="A name for the key, e.g. wealth-agent-prod.")],
    owner: Annotated[str, typer.Option(help="Who is accountable for the key.")],
    team: Annotated[list[str], typer.Option(help="Teams whose clients the key may act for.")],
) -> None:
    """Mint a caller key. Prints the key once and the entry to add to DERVA_CALLERS."""
    from derva.gateway.auth import CallerKey, key_hash, new_key

    key = new_key()
    entry = CallerKey(key_sha256=key_hash(key), key_alias=alias, owner=owner, teams=team)
    typer.echo(f"Key (shown once, give it to the caller): {key}")
    typer.echo("Add this entry to the DERVA_CALLERS list:")
    typer.echo(entry.model_dump_json())


if __name__ == "__main__":
    app()
