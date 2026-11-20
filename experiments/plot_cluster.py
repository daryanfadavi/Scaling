"""Figures for cluster experiment runs (dark theme, for the slides), plus the CSVs behind them so anyone can restyle.

    python experiments/plot_cluster.py                          # busy_day, results in experiments/results/
    python experiments/plot_cluster.py --scenario daily_wave
    python experiments/plot_cluster.py --results-dir experiments/results/archive_overnight_limit500m --scenario random_bursts

Writes to <results-dir>/figures/ (gitignored, like all results):
    <scenario>_timeline.png/.svg      one representative run per controller, two panels on one time axis:
                                      ready pods (request rate as a filled area on a right-hand axis), p95 latency
    <scenario>_timeline_data.csv      the exact rows plotted (long format: one row per controller x 15 s reading),
                                      with a `warmup` column
    <scenario>_tradeoff.png/.svg      one dot per controller: cost (pod-minutes) vs mean p95 latency after warm-up,
                                      mean over repeats, with lines spanning the individual repeats
    <scenario>_tradeoff_data.csv      one row per run with every number in the figure and the printed table
and prints a per-controller table (pod-minutes, p95 after warm-up, SLA violations with and without warm-up).

The raw data is the per-run metrics.csv in <results-dir>/<scenario>/<controller>/repeat_<N>/
(columns documented in docs/telemetry.md); these CSVs are just a convenient cut of it.

Warm-up: every run starts at 1 pod, so the first WARMUP_S seconds measure the cold start, not the controller.
Readings with elapsed_s <= WARMUP_S are excluded from the "after warm-up" latency numbers.
"Representative run" = the repeat whose pod-minutes is closest to that controller's mean.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_cluster import RESULTS_DIR, SLA_MS, collect  # noqa: E402  (same scoring as the tables)

WARMUP_S = 60

CONTROLLERS = ["hpa", "keda", "phpa", "llm"]
LABELS = {"hpa": "HPA", "keda": "KEDA", "phpa": "PHPA", "llm": "LLM"}
COLORS = {"hpa": "#6EA8FE", "keda": "#F2B263", "phpa": "#B79CF2", "llm": "#4FD1A5"}
# Shapes matter: HPA and PHPA are hard to tell apart by colour for red-green colour-blind viewers.
MARKERS = {"hpa": "o", "keda": "s", "phpa": "D", "llm": "^"}

# Dark slide theme
BG, TEXT, TEXT_2, GRID, SHADE = "#0D1117", "#ECEFF3", "#A7B0BD", "#2C3645", "#1B2330"
LOAD = "#3A4558"           # request-rate area: muted, behind the pod lines
FONT_PT = 20               # tick labels and legend
NOTE_PT = 16               # shading labels and footnotes
DPI = 200                  # 2x: sizes below are in inches at 100 px/inch
TIMELINE_IN = (16.64, 5.80)  # -> 3328 x 1160 px
TRADEOFF_IN = (11.92, 5.80)  # -> 2384 x 1160 px


def style() -> None:
    plt.rcParams.update({
        "font.size": FONT_PT, "axes.labelsize": FONT_PT, "xtick.labelsize": FONT_PT, "ytick.labelsize": FONT_PT,
        "legend.fontsize": FONT_PT, "text.color": TEXT, "axes.labelcolor": TEXT_2,
        "xtick.color": TEXT_2, "ytick.color": TEXT_2, "axes.edgecolor": GRID, "axes.linewidth": 1.2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 1.0,
        "legend.frameon": False, "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
        "lines.linewidth": 2.5, "svg.fonttype": "none",
    })


def order(controllers) -> list[str]:
    present = set(controllers)
    return [c for c in CONTROLLERS if c in present] + sorted(present - set(CONTROLLERS))


def legend_handles(controllers: list[str]) -> list[Line2D]:
    return [Line2D([], [], color=COLORS.get(c), lw=3, marker=MARKERS.get(c), markersize=11,
                   markeredgecolor=BG, label=LABELS.get(c, c)) for c in controllers]


def save(fig, out: Path) -> None:
    for ext in ("png", "svg"):
        fig.savefig(out.with_suffix(f".{ext}"), dpi=DPI)  # no bbox="tight": the pixel size must be exact
    plt.close(fig)


# ---- numbers ---------------------------------------------------------------------------------

def run_metrics(results_dir: Path, scenario: str, controller: str, repeat: int) -> pd.DataFrame:
    m = pd.read_csv(results_dir / scenario / controller / f"repeat_{repeat}" / "metrics.csv")
    m["warmup"] = m["elapsed_s"] <= WARMUP_S
    return m


def add_warmup_scores(runs: pd.DataFrame, results_dir: Path, scenario: str) -> pd.DataFrame:
    """Per run: latency and SLA numbers with and without the warm-up readings."""
    extra = []
    for r in runs.itertuples():
        m = run_metrics(results_dir, scenario, r.controller, r.repeat)
        after = m.loc[~m["warmup"], "latency_p95_ms"].dropna()
        p95 = m["latency_p95_ms"]
        extra.append({
            "mean_p95_after_warmup_ms": round(float(after.mean()), 1),
            "max_p95_after_warmup_ms": round(float(after.max()), 1),
            "sla_violations_incl_warmup": int((p95 > SLA_MS).sum()),
            "sla_violations_excl_warmup": int((after > SLA_MS).sum()),
        })
    return pd.concat([runs.reset_index(drop=True), pd.DataFrame(extra)], axis=1)


def representative_runs(runs: pd.DataFrame) -> dict[str, int]:
    """controller -> repeat whose pod-minutes is closest to the controller's mean."""
    picks = {}
    for controller, group in runs.groupby("controller"):
        mean = group["pod_minutes"].mean()
        picks[controller] = int(group.loc[(group["pod_minutes"] - mean).abs().idxmin(), "repeat"])
    return picks


def scenario_phases(scenario: str, duration_s: float) -> list[tuple[float, float, str]]:
    """(start_s, end_s, label) of the scenario's bursts and spike, for shading. Only busy_day."""
    if scenario != "busy_day":
        return []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
        import busy_day  # needs locust (installed in the venv)
    except Exception:
        return []
    phases = [(s * duration_s, e * duration_s, "burst") for s, e, _ in busy_day.BURSTS]
    phases.append((busy_day.SPIKE_START * duration_s, busy_day.SPIKE_END * duration_s, "spike"))
    return phases


# ---- figures ---------------------------------------------------------------------------------

def plot_timeline(data: pd.DataFrame, scenario: str, out: Path) -> None:
    fig, (ax_pods, ax_p95) = plt.subplots(2, 1, figsize=TIMELINE_IN, sharex=True, layout="constrained")
    controllers = order(data["controller"].unique())
    duration = data["elapsed_s"].max()
    phases = scenario_phases(scenario, 900 if scenario == "busy_day" else duration)

    # Top: request rate (right axis, behind) + ready pods. The shading goes on the load axis, underneath
    # the load area, so it never hides the load.
    ax_load = ax_pods.twinx()
    ax_pods.set_zorder(ax_load.get_zorder() + 1)
    ax_pods.patch.set_visible(False)
    for ax in (ax_load, ax_p95):
        ax.axvspan(0, WARMUP_S, color=SHADE, lw=0, zorder=0)
        for start, end, _ in phases:
            ax.axvspan(start, end, color=SHADE, lw=0, zorder=0)
    # The first burst starts right at the end of warm-up: a line marks where warm-up ends.
    for ax in (ax_pods, ax_p95):
        ax.axvline(WARMUP_S, color=TEXT_2, lw=1.2, ls=(0, (2, 3)), zorder=1)
    # Labels: bursts and spike on the top panel; warm-up on the bottom one (so the two never collide).
    for start, end, label in phases:
        ax_pods.text((start + end) / 2, 0.97, label, transform=ax_pods.get_xaxis_transform(),
                     ha="center", va="top", fontsize=NOTE_PT, color=TEXT_2)
    ax_p95.text(WARMUP_S / 2, 0.97, "warm-up", transform=ax_p95.get_xaxis_transform(),
                ha="center", va="top", fontsize=NOTE_PT - 2, color=TEXT_2)
    load = data.groupby("elapsed_s")["request_rate_rps"].mean()  # same load for every controller
    ax_load.fill_between(load.index, load.values, color=LOAD, lw=0, step="post", zorder=1)
    ax_load.set_ylim(0, load.max() * 1.25)
    ax_load.set_ylabel("load (req/s)", color=TEXT_2)
    ax_load.spines["right"].set_visible(True)
    ax_load.grid(False)

    for c in controllers:
        d = data[data["controller"] == c]
        kw = dict(color=COLORS.get(c), marker=MARKERS.get(c), markersize=9, markeredgecolor=BG, zorder=3)
        ax_pods.plot(d["elapsed_s"], d["replicas_ready"], drawstyle="steps-post", markevery=6, **kw)
        ax_p95.plot(d["elapsed_s"], d["latency_p95_ms"], markevery=6, **kw)
    ax_pods.set_ylabel("ready pods")
    ax_pods.set_ylim(0, data["replicas_ready"].max() + 1.5)
    ax_pods.yaxis.get_major_locator().set_params(integer=True)

    ax_p95.axhline(SLA_MS, color=TEXT, lw=2, ls=(0, (6, 4)), zorder=2)
    ax_p95.text(duration, SLA_MS + 15, f"SLA {SLA_MS:.0f} ms", ha="right", va="bottom", fontsize=NOTE_PT,
                color=TEXT)
    ax_p95.set_ylabel("p95 (ms)")
    ax_p95.set_ylim(0, max(700, data["latency_p95_ms"].max()) * 1.25)  # headroom for the warm-up label
    ax_p95.set_xlabel("time since load test start (s)")
    ax_p95.set_xlim(0, duration)

    fig.legend(handles=legend_handles(controllers), loc="outside upper right", ncol=len(controllers))
    save(fig, out)


def plot_tradeoff(runs: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=TRADEOFF_IN, layout="constrained")
    ax.grid(True, axis="both")
    y_col = "mean_p95_after_warmup_ms"
    for c in order(runs["controller"].unique()):
        g = runs[runs["controller"] == c]
        x, y = g["pod_minutes"].mean(), g[y_col].mean()
        color = COLORS.get(c)
        # lines spanning the individual runs (n is small: show the spread, not just the mean)
        ax.plot([g.pod_minutes.min(), g.pod_minutes.max()], [y, y], color=color, lw=2, alpha=0.7, zorder=2)
        ax.plot([x, x], [g[y_col].min(), g[y_col].max()], color=color, lw=2, alpha=0.7, zorder=2)
        ax.scatter(x, y, s=260, color=color, marker=MARKERS.get(c), edgecolor=BG, linewidth=2.5, zorder=4)
        ax.annotate(LABELS.get(c, c), (x, y), xytext=(14, 10), textcoords="offset points", fontsize=FONT_PT,
                    color=TEXT, fontweight="bold")
    ax.set_xlabel("cost: pod-minutes")
    ax.set_ylabel("mean p95 latency (ms)\nafter warm-up")
    lo, hi = runs.pod_minutes.min(), runs.pod_minutes.max()
    pad = (hi - lo) * 0.12 or 5
    ax.set_xlim(lo - pad, hi + pad * 1.6)  # room for the right-most label
    ax.set_ylim(0, runs[y_col].max() * 1.3)
    ax.text(0.015, 0.04, "↙ lower left is better", transform=ax.transAxes, fontsize=NOTE_PT, color=TEXT_2)
    n = runs.groupby("controller").size()
    n_text = f"{n.min()}" if n.min() == n.max() else f"{n.min()}–{n.max()}"
    ax.legend(handles=[Line2D([], [], ls="", marker=MARKERS.get(c), markersize=13, color=COLORS.get(c),
                              markeredgecolor=BG, label=LABELS.get(c, c)) for c in order(runs.controller.unique())],
              loc="upper right", ncol=4, handletextpad=0.2, columnspacing=1.0)
    fig.get_layout_engine().set(h_pad=0.1)
    fig.supxlabel(f"n = {n_text} runs per controller. First {WARMUP_S} s excluded (warm-up).",
                  fontsize=NOTE_PT, color=TEXT_2, x=0.01, ha="left")
    save(fig, out)


# ---- table -----------------------------------------------------------------------------------

def table(runs: pd.DataFrame) -> str:
    head = ["Controller", "Runs", "Pod-min (mean)", "p95 after warm-up: mean (ms)", "max (ms)",
            "SLA viol. incl. warm-up", "SLA viol. excl. warm-up"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for c in order(runs["controller"].unique()):
        g = runs[runs["controller"] == c].sort_values("repeat")
        per_run = lambda col: " + ".join(str(v) for v in g[col]) + f" = {g[col].sum()}"  # noqa: E731
        lines.append("| " + " | ".join([
            LABELS.get(c, c), str(len(g)), f"{g.pod_minutes.mean():.1f}",
            f"{g.mean_p95_after_warmup_ms.mean():.0f}", f"{g.max_p95_after_warmup_ms.max():.0f}",
            per_run("sla_violations_incl_warmup"), per_run("sla_violations_excl_warmup")]) + " |")
    lines.append("")
    lines.append(f"p95 mean = mean over runs of each run's mean p95 after the first {WARMUP_S} s; "
                 "max = highest single 15 s reading after warm-up in any run. SLA counts are per run, summed.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--scenario", default="busy_day")
    args = parser.parse_args(argv)

    runs = pd.DataFrame(collect(args.results_dir, [args.scenario]))
    if runs.empty:
        print(f"No runs for {args.scenario} in {args.results_dir}")
        return 1
    runs = add_warmup_scores(runs, args.results_dir, args.scenario)
    out_dir = args.results_dir / "figures"
    out_dir.mkdir(exist_ok=True)
    style()

    picks = representative_runs(runs)
    timeline = pd.concat([run_metrics(args.results_dir, args.scenario, c, r).assign(controller=c, repeat=r)
                          for c, r in picks.items()], ignore_index=True)
    timeline = timeline[["controller", "repeat"] + [col for col in timeline if col not in ("controller", "repeat")]]
    timeline.to_csv(out_dir / f"{args.scenario}_timeline_data.csv", index=False)
    plot_timeline(timeline, args.scenario, out_dir / f"{args.scenario}_timeline")

    cols = ["scenario", "controller", "repeat", "pod_minutes", "mean_p95_after_warmup_ms", "max_p95_after_warmup_ms",
            "sla_violations_incl_warmup", "sla_violations_excl_warmup", "peak_pods", "mean_pods",
            "scale_events", "flaps", "p95_max_ms", "error_rate_max"]
    runs.sort_values(["controller", "repeat"])[cols].to_csv(out_dir / f"{args.scenario}_tradeoff_data.csv",
                                                            index=False)
    plot_tradeoff(runs, out_dir / f"{args.scenario}_tradeoff")

    print(table(runs))
    after = runs[runs["sla_violations_excl_warmup"] > 0]
    if after.empty:
        print(f"\nCheck: 0 SLA violations after warm-up in all {len(runs)} runs.")
    else:
        print("\nCheck FAILED: SLA violations after warm-up in:")
        print(after[["controller", "repeat", "sla_violations_excl_warmup"]].to_string(index=False))
    print(f"\nRepresentative runs: {picks}")
    for f in sorted(out_dir.glob(f"{args.scenario}_*")):
        print(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
