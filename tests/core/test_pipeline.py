"""End-to-end: the runner writes CSV/JSON/PNG and the plotter builds the leaderboard."""

import argparse
import json

import pandas as pd
import pytest

import controller.llm_client as client_mod
from controller.llm_client import LLMClient
from controller.llm_controller import LLMController
from controller.openrouter_client import OpenRouterClient
from controller.static import StaticController
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


# ------------------------------------------------------------ --provider


@pytest.fixture
def no_env(monkeypatch):
    """Hide any real .env / keys / model settings from the client constructors."""
    monkeypatch.setattr(client_mod, "load_dotenv", lambda *a, **k: None)
    for name in ("SCALE_MODEL", "OPENROUTER_MODEL", "OPENROUTER_UPSTREAM"):
        monkeypatch.delenv(name, raising=False)


def llm_args(tmp_path, **overrides):
    values = dict(
        provider="anthropic",
        model=None,
        openrouter_upstream=None,
        cache_dir=tmp_path,
        offline=True,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_make_llm_client_picks_the_provider(tmp_path, no_env):
    default = run.make_llm_client(llm_args(tmp_path), repeat_index=0)
    assert type(default) is LLMClient and default.provider_name == "anthropic"

    client = run.make_llm_client(
        llm_args(
            tmp_path,
            provider="openrouter",
            model="vendor/model",
            openrouter_upstream="Fireworks",
        ),
        repeat_index=2,
    )
    assert isinstance(client, OpenRouterClient)
    assert (client.model, client.upstream) == ("vendor/model", ["Fireworks"])
    assert client.repeat_index == 2 and client.offline is True


def test_openrouter_without_a_model_exits_clearly(tmp_path, no_env):
    with pytest.raises(SystemExit, match="needs a model"):
        run.make_llm_client(llm_args(tmp_path, provider="openrouter"), repeat_index=0)


def test_default_label_adds_the_model_only_for_openrouter(tmp_path, no_env):
    anthropic_run = LLMController(run.make_llm_client(llm_args(tmp_path), 0))
    assert run.default_label(anthropic_run) == "llm"

    openrouter_run = LLMController(
        run.make_llm_client(
            llm_args(tmp_path, provider="openrouter", model="vendor/model"), 0
        ),
        use_context=True,
    )
    assert openrouter_run.name == "llm_context"  # the controller itself is unchanged
    assert run.safe_label(run.default_label(openrouter_run)) == "llm_context-vendor_model"
    assert run.default_label(StaticController(replicas=2)) == "static2"


def test_summary_records_provider(tmp_path):
    run.main(
        ["--controller", "static", "--scenario", "spike", "--results-dir", str(tmp_path)]
    )
    summary = json.loads((tmp_path / "spike" / "static2" / "seed0_rep0.json").read_text())
    assert summary["provider"] is None and summary["upstream"] is None
