"""End-to-end: the runner writes CSV/JSON/PNG and the plotter builds the leaderboard."""

import json

import pandas as pd

from experiments import plot, run


def test_run_and_plot_end_to_end(tmp_path):
    for replicas in (2, 6):
        code = run.main(
            [
                "--controller",
                "static",
                "--replicas",
                str(replicas),
                "--scenario",
                "spike",
                "--results-dir",
                str(tmp_path),
            ]
        )
        assert code == 0

    run_dir = tmp_path / "spike" / "static6"
    df = pd.read_csv(run_dir / "seed0_rep0.csv")
    assert len(df) == 80
    for column in [
        "load",
        "capacity",
        "replicas_ready",
        "replicas_pending",
        "cpu_pct",
        "latency_ms",
        "backlog",
        "target",
        "reason",
    ]:
        assert column in df.columns

    summary = json.loads((run_dir / "seed0_rep0.json").read_text())
    for key in [
        "sla_violation_ticks",
        "mean_latency_ms",
        "p95_latency_ms",
        "replica_minutes",
        "scaling_actions",
        "invalid_decisions",
        "llm_calls",
        "llm_latency_total_s",
    ]:
        assert key in summary["metrics"]
    assert (run_dir / "seed0_rep0.png").exists()

    assert plot.main(["--results-dir", str(tmp_path)]) == 0
    assert (tmp_path / "plots" / "spike_timeseries.png").exists()
    assert (tmp_path / "plots" / "leaderboard.png").exists()
    leaderboard = (tmp_path / "leaderboard.md").read_text()
    assert "static2" in leaderboard and "static6" in leaderboard


def test_repeats_aggregate_with_std(tmp_path):
    run.main(
        [
            "--controller",
            "static",
            "--scenario",
            "ramp",
            "--seed",
            "1",
            "--results-dir",
            str(tmp_path),
            "--no-plot",
        ]
    )
    run.main(
        [
            "--controller",
            "static",
            "--scenario",
            "ramp",
            "--seed",
            "2",
            "--results-dir",
            str(tmp_path),
            "--no-plot",
        ]
    )
    agg = plot.aggregate(plot.load_summaries(tmp_path))
    assert int(agg.loc[0, "runs"]) == 2


def test_stub_controller_gives_friendly_error(tmp_path, capsys):
    code = run.main(
        [
            "--controller",
            "scheduled",
            "--schedule",
            "26:17",
            "--scenario",
            "spike",
            "--results-dir",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "isn't implemented yet" in capsys.readouterr().err
    assert not any(tmp_path.rglob("*.json"))
