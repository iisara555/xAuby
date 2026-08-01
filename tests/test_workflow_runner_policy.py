from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
INTERNAL_PR_GUARD = (
    "github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository"
)
GITHUB_HOSTED_RUNNER = "runs-on: ubuntu-latest"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_every_workflow_uses_standard_github_hosted_runner() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        runs_on = [line.strip() for line in text.splitlines() if "runs-on:" in line]
        assert runs_on, f"{path.name} has no runner declaration"
        assert all(line == GITHUB_HOSTED_RUNNER for line in runs_on), (
            f"{path.name} must use the standard GitHub-hosted runner: {runs_on}"
        )
        assert "self-hosted" not in text


def test_pr_ci_preserves_internal_only_guard() -> None:
    for name in (
        "secret-scan.yml",
        "security.yml",
        "test-frontend.yml",
        "test-python.yml",
    ):
        text = _workflow(name)
        job_count = text.count(GITHUB_HOSTED_RUNNER)
        assert job_count > 0
        assert text.count(INTERNAL_PR_GUARD) == job_count


def test_backtest_is_manual_and_uses_single_job_multiprocessing() -> None:
    text = _workflow("btc-supertrend-grid-research.yml")
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "matrix:" not in text
    assert GITHUB_HOSTED_RUNNER in text
    assert "timeout-minutes: 360" in text
    assert "shell: bash" in text
    assert "scripts/btc_supertrend_okx_pf_grid.py" in text
    assert '--workers "${{ inputs.workers }}"' in text
