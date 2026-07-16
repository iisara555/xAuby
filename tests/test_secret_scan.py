import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_PATH = ROOT / "scripts" / "scan_secrets.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("scan_secrets", SCAN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_secret_scan_flags_realistic_secret_assignments():
    scanner = _load_scanner()
    api_key = "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" + "abcdefghijklmno"
    telegram_value = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    text = "\n".join(
        [
            f"BINANCE_API_KEY={api_key}",
            f"TELEGRAM_BOT_TOKEN={telegram_value}",
        ]
    )

    findings = scanner.scan_text("test", "fixture.env", text)

    assert {finding.kind for finding in findings} == {
        "known_secret_literal",
        "sensitive_assignment",
    }
    assert all("ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in finding.fingerprint for finding in findings)


def test_secret_scan_allows_env_names_and_placeholders():
    scanner = _load_scanner()
    text = "\n".join(
        [
            "api_key_env: OKX_API_KEY",
            "api_secret_env: OKX_API_SECRET",
            "GITHUB_TOKEN=ghp_xxxxxxxx",
            "TELEGRAM_BOT_TOKEN=FRESH123456",
            "XAUBY_SAAS_SESSION_SECRET=replace-with-at-least-32-random-bytes",
            "google_client_secret=client-secret-456",
            '"UPDATE users SET pending_totp_secret=NULL,totp_enabled=1 WHERE id=?"',
            "url = f'https://example.test?api_key={api_key}'",
        ]
    )

    assert scanner.scan_text("test", "fixture.txt", text) == []


def test_tracked_files_do_not_contain_potential_secrets():
    result = subprocess.run(
        [sys.executable, str(SCAN_PATH), "--tracked"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
