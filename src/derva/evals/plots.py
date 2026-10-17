"""Reliability diagram and automation curve for one or more runs."""

from __future__ import annotations

from pathlib import Path

from derva.evals.runner import RunSummary


def _label(s: RunSummary) -> str:
    tag = " (mock)" if s.synthetic_decider else ""
    return f"{s.decider}{tag}, n={s.n}"


def plot_runs(summaries: list[RunSummary], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (rel, auto) = plt.subplots(1, 2, figsize=(11, 4.5))
    rel.plot(
        [0, 1], [0, 1], linestyle="--", color="grey", linewidth=1, label="perfectly calibrated"
    )
    for s in summaries:
        points = [
            (b.mean_confidence, b.accuracy)
            for b in s.reliability
            if b.count and b.accuracy is not None
        ]
        if points:
            xs, ys = zip(*points, strict=True)
            rel.plot(
                xs,
                ys,
                marker="o",
                label=f"{_label(s)}, ECE {s.ece:.3f}" if s.ece is not None else _label(s),
            )
        curve = [(p.coverage, p.accuracy) for p in s.automation if p.accuracy is not None]
        if curve:
            xs, ys = zip(*curve, strict=True)
            auto.plot(xs, ys, marker=".", label=_label(s))
    rel.set(
        xlabel="Probability of the top answer",
        ylabel="Share correct",
        xlim=(0, 1),
        ylim=(0, 1),
        title="Reliability",
    )
    auto.set(
        xlabel="Share decided automatically",
        ylabel="Accuracy on that share",
        xlim=(0, 1.02),
        ylim=(0, 1.02),
        title="Automation curve",
    )
    auto.axhline(0.95, color="grey", linestyle=":", linewidth=1)
    for ax in (rel, auto):
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="lower right")
    task = {s.task for s in summaries}
    split = {s.split for s in summaries}
    fig.suptitle(f"{'/'.join(sorted(task))}, {'/'.join(sorted(split))} split")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
