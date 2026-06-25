import os
import argparse
import signal
import sys

# Ensure the working directory is the script's directory
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir:
    os.chdir(script_dir)


def _load_logging_cfg(config_path: str) -> dict:
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("logging", {})
    except Exception:
        return {}


_pre_parser = argparse.ArgumentParser(add_help=False)
_pre_parser.add_argument("--config", type=str, default="bot_config.yaml")
_pre_args, _ = _pre_parser.parse_known_args()

from xauby.observability import setup_logging

# Configure structured logging before engine import
run_id = setup_logging(logging_cfg=_load_logging_cfg(_pre_args.config))

from xauby.engine.trading import LiteTradingEngine
from xauby.runtime.config_error import ConfigError


def main():
    parser = argparse.ArgumentParser(
        description="xAuby : Alternative Store of Value Trading System — engine launcher",
    )
    parser.add_argument("--config", type=str, default="bot_config.yaml", help="Path to bot_config.yaml")
    parser.add_argument("--simulate", action="store_true", help="Force simulation (paper trading) mode")
    parser.add_argument("--live", action="store_true", help="Force live trading mode (caution!)")
    parser.add_argument("--read-only", action="store_true", help="Force read-only mode (skip placing actual orders)")
    parser.add_argument("--pair", type=str, help="Override trading pair symbol (e.g. XAUTUSDT)")

    args = parser.parse_args()

    # Apply command line argument overrides to environment variables
    if args.simulate:
        os.environ["SIMULATE_ONLY"] = "true"
        print("Override: Simulation mode enabled.")
    elif args.live:
        os.environ["SIMULATE_ONLY"] = "false"
        print("Override: Live trading mode enabled.")

    if args.read_only:
        os.environ["BOT_READ_ONLY"] = "true"
        print("Override: Read-only mode enabled.")

    if args.pair:
        os.environ["DEFAULT_SYMBOL"] = args.pair
        print(f"Override: Trading pair symbol set to {args.pair}.")

    engine = None
    try:
        engine = LiteTradingEngine(config_path=args.config)
        print(f"Observability run_id: {engine.run_id} (logging run_id: {run_id})")

        def _handle_sigterm(signum, frame):
            print("\nBot received SIGTERM, shutting down gracefully...")
            if engine:
                try:
                    engine.stop(reason="Graceful shutdown (SIGTERM)")
                except Exception:
                    pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, _handle_sigterm)
        engine.start()
    except ConfigError as e:
        print(f"\nConfiguration error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        msg = str(e)
        if "already running" in msg:
            print(f"\n[BLOCKED] {msg}")
            sys.exit(2)   # exit code 2 = duplicate instance
        print(f"\nBot encountered a fatal error: {e}")
        try:
            if engine:
                engine.stop(reason=str(e))
        except Exception:
            pass
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        try:
            if engine:
                engine.stop()
        except Exception:
            pass
        sys.exit(0)
    except Exception as e:
        print(f"\nBot encountered a fatal error: {e}")
        try:
            if engine:
                engine.stop(reason=str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
