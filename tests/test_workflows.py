"""The GitHub Actions workflows cannot be run here, so their structure is checked instead."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def load(name: str) -> dict:
    workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    workflow["on"] = workflow.pop(True)  # YAML 1.1 reads the key `on` as the boolean True
    return workflow


def uses(job: dict) -> list[str]:
    return [step["uses"] for step in job["steps"] if "uses" in step]


def commands(job: dict) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


@pytest.fixture
def pages() -> dict:
    return load("pages.yml")


def test_the_page_is_rebuilt_every_day_on_demand_and_on_every_push_to_main(pages):
    triggers = pages["on"]

    assert triggers["schedule"] == [{"cron": "17 6 * * *"}]
    assert "workflow_dispatch" in triggers
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers) == {"schedule", "workflow_dispatch", "push"}


def test_pages_asks_for_exactly_the_permissions_it_needs(pages):
    assert pages["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}


def test_one_deployment_runs_at_a_time_and_a_running_one_is_not_cancelled(pages):
    assert pages["concurrency"] == {"group": "pages", "cancel-in-progress": False}


def test_the_build_job_builds_the_report_from_live_data_and_uploads_it(pages):
    build = pages["jobs"]["build"]

    assert build["runs-on"] == "ubuntu-latest"
    assert uses(build) == [
        "actions/checkout@v4",
        "astral-sh/setup-uv@v6",
        "actions/upload-pages-artifact@v3",
    ]
    install, report = commands(build)
    assert install == "uv sync --locked"
    assert report == "uv run yield-scan report --out site/index.html --limit 100"
    assert "--from-file" not in report  # live data, not the saved sample
    upload = build["steps"][-1]
    assert upload["with"] == {"path": "site"}
    # the report is built before it is uploaded
    assert [bool(step.get("run")) for step in build["steps"]] == [False, False, True, True, False]


def test_the_deploy_job_publishes_after_the_build_in_the_github_pages_environment(pages):
    deploy = pages["jobs"]["deploy"]

    assert deploy["needs"] == "build"
    assert deploy["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deployment.outputs.page_url }}",
    }
    assert uses(deploy) == ["actions/deploy-pages@v4"]
    assert deploy["steps"][0]["id"] == "deployment"  # the environment url reads its output


def test_the_page_is_not_committed_to_the_repository():
    ignored = (WORKFLOWS.parent.parent / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "site/" in ignored


def test_the_ci_workflow_still_runs_the_linter_the_formatter_check_and_the_tests():
    ci = load("ci.yml")

    assert commands(ci["jobs"]["check"]) == [
        "uv sync --locked",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pytest -q",
    ]
