"""Keep the CI runner policy enforced in code, not just in AGENTS.md.

The policy inverted on 2026-08-01 when the repository became public. It used to
require every workflow to run on the home self-hosted runner, to avoid metered
GitHub-hosted minutes on a private repo. Public repositories get standard
hosted runners free, and GitHub's hardening guidance is to use self-hosted
runners *only* with private repositories — a fork can otherwise run its own
code on the operator's machine through a pull request.

So the assertions flipped, but their purpose did not: the runner a workflow
lands on is a security decision and must not drift by accident.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
INTERNAL_PR_GUARD = (
    "github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository"
)

# The one workflow allowed on the home runner: it needs the hardware, and it is
# dispatch-only so no pull request can reach it.
BACKTEST_WORKFLOW = "btc-supertrend-grid-research.yml"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _parsed(name: str) -> dict:
    return yaml.safe_load(_workflow(name))


def _triggers(document: dict) -> dict:
    # YAML 1.1 parses a bare `on:` key as the boolean True.
    trigger = document.get("on", document.get(True))
    return trigger if isinstance(trigger, dict) else {}


def _workflow_names() -> list[str]:
    return sorted(path.name for path in WORKFLOWS.glob("*.yml"))


def test_ci_runs_on_github_hosted_runners() -> None:
    """Only the backtest grid may touch the operator's machine."""
    for name in _workflow_names():
        if name == BACKTEST_WORKFLOW:
            continue
        runs_on = [
            line.strip()
            for line in _workflow(name).splitlines()
            if line.strip().startswith("runs-on:")
        ]
        assert runs_on, f"{name} has no runner declaration"
        assert all(line == "runs-on: ubuntu-latest" for line in runs_on), (
            f"{name} must run on GitHub-hosted runners while this repository is "
            f"public, not on the home runner: {runs_on}"
        )


def _self_hosted_jobs(name: str) -> list[str]:
    """Jobs whose runner is self-hosted, read from `runs-on` rather than raw text.

    Matching the whole file would also hit prose: the workflow comments discuss
    self-hosted runners, and a comment is not a runner assignment.
    """
    jobs = (_parsed(name).get("jobs") or {}).items()
    return [job for job, spec in jobs if "self-hosted" in str(spec.get("runs-on", ""))]


def test_only_the_dispatch_only_backtest_uses_the_home_runner() -> None:
    for name in _workflow_names():
        if not _self_hosted_jobs(name):
            continue
        assert name == BACKTEST_WORKFLOW, (
            f"{name} targets a self-hosted runner; only {BACKTEST_WORKFLOW} may, "
            "because it is the only one a pull request cannot trigger"
        )
        triggers = _triggers(_parsed(name))
        assert set(triggers) == {"workflow_dispatch"}, (
            "a self-hosted workflow must stay dispatch-only so a fork cannot "
            f"reach the operator's machine, got triggers: {sorted(triggers)}"
        )


def test_pull_request_workflows_refuse_forks() -> None:
    """A fork PR must not execute, one guard per job."""
    for name in _workflow_names():
        document = _parsed(name)
        if "pull_request" not in _triggers(document):
            continue
        jobs = document.get("jobs") or {}
        assert jobs, f"{name} declares pull_request but has no jobs"
        for job_name, job in jobs.items():
            assert INTERNAL_PR_GUARD in str(job.get("if", "")), (
                f"{name}:{job_name} is missing the fork-PR guard"
            )


def test_every_workflow_declares_least_privilege() -> None:
    """Stated per workflow so the repository default cannot silently widen it."""
    for name in _workflow_names():
        permissions = _parsed(name).get("permissions")
        assert permissions == {"contents": "read"}, (
            f"{name} must declare `permissions: contents: read`; nothing in CI "
            f"writes, got: {permissions}"
        )


def test_backtest_is_manual_and_uses_local_multiprocessing() -> None:
    text = _workflow(BACKTEST_WORKFLOW)
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "matrix:" not in text
    assert "runs-on: [self-hosted, windows, x64, xauby-backtest]" in text
    assert "scripts/btc_supertrend_okx_pf_grid.py" in text
    assert '--workers "${{ inputs.workers }}"' in text
