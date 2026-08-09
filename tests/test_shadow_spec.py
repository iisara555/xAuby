from __future__ import annotations

import json
from pathlib import Path

from xauby.saas.certification import config_fingerprint
from xauby.saas.shadow_spec import build_shadow_spec, materialize_shadow_spec


def _preset(preset_id: str, strategy: str, mult: float) -> dict:
    return {
        "id": preset_id,
        "symbol": "BTCUSDT",
        "target_id": "okx-swap",
        "exchange_id": "okx",
        "strategy": strategy,
        "primary_timeframe": "4h",
        "confirm_timeframe": "",
        "execution_profile": {"mult": mult},
        "certification_status": "certified",
    }


def test_materialized_shadow_spec_is_two_candidate_research_only(
    tmp_path: Path, monkeypatch
) -> None:
    presets = {
        "champion-a": _preset("champion-a", "supertrend_ema200", 4.0),
        "challenger-b": _preset("challenger-b", "supertrend_ema200", 3.5),
    }
    monkeypatch.setattr(
        "xauby.saas.shadow_spec.preset_for_candidate",
        lambda preset_id: presets[preset_id],
    )
    pool = {
        "symbol": "BTCUSDT",
        "target_id": "okx-swap",
        "champion_id": "champion-a",
        "candidates": [
            {
                "preset_id": preset_id,
                "certificate_config_fingerprint": config_fingerprint(preset),
            }
            for preset_id, preset in presets.items()
        ],
    }

    first = build_shadow_spec(pool, "pilot-1")
    second = build_shadow_spec(pool, "pilot-1")
    assert first == second
    assert first is not None
    assert first["research_only"] is True
    assert first["broker_access"] is False
    assert first["candidate_limit"] == 2
    assert [item["role"] for item in first["candidates"]] == ["champion", "challenger"]

    materialize_shadow_spec(pool, "pilot-1", tmp_path)
    status_path = tmp_path / "shadow" / "BTCUSDT" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "prepared"
    status["status"] = "healthy"
    status_path.write_text(json.dumps(status), encoding="utf-8")

    materialize_shadow_spec(pool, "pilot-1", tmp_path)
    preserved = json.loads(status_path.read_text(encoding="utf-8"))
    assert preserved["status"] == "healthy"


def test_shadow_spec_waits_for_a_challenger() -> None:
    assert (
        build_shadow_spec(
            {
                "symbol": "BTCUSDT",
                "target_id": "okx-swap",
                "champion_id": "champion-a",
                "candidates": [{"preset_id": "champion-a"}],
            },
            "pilot-1",
        )
        is None
    )


def test_shadow_spec_shares_challenger_regime_feed_without_mutating_champion(
    monkeypatch,
) -> None:
    champion = _preset("champion-a", "supertrend_ema200", 4.0)
    challenger = _preset("challenger-b", "supertrend_ema200", 4.0)
    challenger["confirm_timeframe"] = "1d"
    challenger["execution_profile"] = {
        "use_d1_regime_filter": True,
        "use_d1_regime_filter_long": True,
        "use_d1_regime_filter_short": False,
    }
    presets = {"champion-a": champion, "challenger-b": challenger}
    monkeypatch.setattr(
        "xauby.saas.shadow_spec.preset_for_candidate",
        lambda preset_id: presets[preset_id],
    )
    pool = {
        "symbol": "BTCUSDT",
        "target_id": "okx-swap",
        "champion_id": "champion-a",
        "candidates": [
            {
                "preset_id": preset_id,
                "certificate_config_fingerprint": config_fingerprint(preset),
            }
            for preset_id, preset in presets.items()
        ],
    }

    spec = build_shadow_spec(pool, "pilot-1")

    assert spec is not None
    assert spec["timeframe"] == "4h"
    assert spec["regime_timeframe"] == "1d"
    assert spec["candidates"][0]["strategy_config"] == {"mult": 4.0}
    assert spec["candidates"][1]["strategy_config"]["use_d1_regime_filter"] is True
