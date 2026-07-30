from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
INTERNAL_PR_GUARD = (
    "github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository"
)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_no_workflow_uses_metered_github_hosted_runner() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        runs_on = [line.strip() for line in text.splitlines() if "runs-on:" in line]
        assert runs_on, f"{path.name} has no runner declaration"
        assert all("self-hosted" in line for line in runs_on), (
            f"{path.name} must use the home self-hosted runner: {runs_on}"
        )


def test_pr_ci_uses_internal_only_home_runner() -> None:
    for name in ("secret-scan.yml", "security.yml"):
        text = _workflow(name)
        job_count = text.count("runs-on: [self-hosted, linux, x64, xauby-ci]")
        assert job_count > 0
        assert text.count(INTERNAL_PR_GUARD) == job_count


def test_backtest_is_manual_and_uses_local_multiprocessing() -> None:
    text = _workflow("btc-supertrend-grid-research.yml")
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "matrix:" not in text
    assert "runs-on: [self-hosted, linux, x64, xauby-backtest]" in text
    assert "scripts/btc_supertrend_okx_pf_grid.py" in text
    assert '--workers "${{ inputs.workers }}"' in text
