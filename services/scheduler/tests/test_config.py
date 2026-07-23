"""Policy config loading, validation, and hot reload (AN-048)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ancora_scheduler.config import ConfigStore, SchedulerConfig, load_config


def write(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_a_json_policy(tmp_path: Path) -> None:
    path = write(
        tmp_path / "policy.json",
        {
            "rate_limits": {"gemini": {"rps": 5, "burst": 10}},
            "watermarks": {"ancora-cpu": {"soft": 10, "hard": 20}},
            "tenants": {"acme": {"weight": 3, "budget_usd": 25.0}},
        },
    )
    config = load_config(path)
    assert config.rate_limits["gemini"].rps == 5
    assert config.watermark_for("ancora-cpu").hard == 20
    assert config.tenant("acme").weight == 3


def test_loads_a_yaml_policy(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "rate_limits:\n  gemini: { rps: 5, burst: 10 }\nbudget:\n  mode: hard\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.budget.mode == "hard"
    assert config.rate_limits["gemini"].burst == 10


def test_the_shipped_policy_document_is_valid() -> None:
    """The file we mount into the container must actually load."""
    repo_root = Path(__file__).resolve().parents[3]
    config = load_config(repo_root / "deploy" / "scheduler" / "policy.yaml")
    assert "gemini" in config.rate_limits
    assert config.watermark_for("ancora-gpu").hard == 24


def test_unknown_keys_are_rejected_rather_than_ignored(tmp_path: Path) -> None:
    # A misspelled key that silently does nothing is worse than a startup error.
    path = write(tmp_path / "policy.json", {"watermark": {"default": {"soft": 1}}})
    with pytest.raises(ValueError, match="watermark"):
        load_config(path)


def test_invalid_values_name_the_offending_field(tmp_path: Path) -> None:
    path = write(tmp_path / "policy.json", {"rate_limits": {"x": {"rps": 0, "burst": 5}}})
    with pytest.raises(ValueError, match="rps"):
        load_config(path)


def test_hard_watermark_below_soft_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "policy.json", {"watermarks": {"q": {"soft": 100, "hard": 10}}})
    with pytest.raises(ValueError, match="hard"):
        load_config(path)


def test_bad_budget_mode_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "policy.json", {"budget": {"mode": "aggressive"}})
    with pytest.raises(ValueError, match="soft, hard, off"):
        load_config(path)


def test_store_reloads_when_the_file_changes(tmp_path: Path) -> None:
    path = write(tmp_path / "policy.json", {"budget": {"mode": "soft"}})
    store = ConfigStore.from_path(path)
    assert store.config.budget.mode == "soft"

    write(tmp_path / "policy.json", {"budget": {"mode": "hard"}})
    # mtime granularity can be coarse; force the comparison rather than sleeping.
    store._mtime = None
    assert store.reload_if_changed() is True
    assert store.config.budget.mode == "hard"


def test_a_broken_edit_keeps_the_last_good_policy_serving(tmp_path: Path) -> None:
    path = write(tmp_path / "policy.json", {"budget": {"mode": "hard"}})
    store = ConfigStore.from_path(path)
    assert store.config.budget.mode == "hard"

    path.write_text("{ not json", encoding="utf-8")
    store._mtime = None
    assert store.reload_if_changed() is False
    # A typo must degrade to "the change didn't take", never to "no policy".
    assert store.config.budget.mode == "hard"
    assert store.last_error is not None


def test_a_missing_file_falls_back_to_built_in_defaults(tmp_path: Path) -> None:
    store = ConfigStore.from_path(tmp_path / "absent.yaml")
    assert isinstance(store.config, SchedulerConfig)
    assert store.config.rate_limits  # the built-in defaults, not an empty policy


def test_rate_limit_lookup_falls_back_to_default() -> None:
    cfg = SchedulerConfig.model_validate(
        {"rate_limits": {"default": {"rps": 1, "burst": 1}, "gemini": {"rps": 5, "burst": 5}}}
    )
    assert cfg.rate_limit_for("gemini", None).rps == 5  # type: ignore[union-attr]
    assert cfg.rate_limit_for("openai", None).rps == 1  # type: ignore[union-attr]
    assert cfg.rate_limit_for(None, None).rps == 1  # type: ignore[union-attr]
