from pathlib import Path

from xauby.runtime.pair_registry import PairRegistry


def test_read_only_registry_never_syncs_yaml(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "bot_config.yaml"
    original = "data:\n  pairs: [BTCUSDT]\n"
    cfg_path.write_text(original, encoding="utf-8")
    whitelist = tmp_path / "coin_whitelist.json"
    whitelist.write_text(
        '{"quote_asset":"USDT","assets":[{"symbol":"BTC","enabled":true}]}',
        encoding="utf-8",
    )
    cfg = {
        "data": {
            "pairs": ["XAUUSDT"],
            "hybrid_dynamic_coin_config": {
                "sync_yaml_pairs_from_whitelist": True,
                "whitelist_json_path": "coin_whitelist.json",
            },
        },
        "portfolio": {"quote_asset": "USDT"},
        "strategy": {"active": "xauby_actionzone"},
    }
    registry = PairRegistry(
        cfg,
        config_path=str(cfg_path),
        project_root=str(tmp_path),
        read_only=True,
    )

    registry.load(None)

    assert cfg_path.read_text(encoding="utf-8") == original
