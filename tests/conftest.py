import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.windows_temp_root import ShortRootUnavailable, make_run_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Give Windows runs a short temp root so test paths stay under MAX_PATH."""
    try:
        run_dir = make_run_dir(explicit_basetemp=config.option.basetemp)
    except ShortRootUnavailable as error:
        config.issue_config_time_warning(
            pytest.PytestConfigWarning(f"Using pytest's default temp root: {error}"), stacklevel=2
        )
        return
    if run_dir is not None:
        config.option.basetemp = run_dir


@pytest.fixture
def host_execution_actor() -> Iterator[None]:
    """Explicit host provenance for standalone command and workflow tests."""
    from pandrator.web.accounts.execution_actor import HostActor, execution_actor_scope

    with execution_actor_scope(HostActor("pandrator-cli")):
        yield


@pytest.fixture
def host_model_acquisition() -> Iterator[None]:
    """Explicit authority for standalone tests of synthetic model downloads."""
    from pandrator.logic.model_acquisition import host_model_acquisition_scope

    with host_model_acquisition_scope():
        yield


@pytest.fixture(autouse=True)
def _no_leaked_execution_actor() -> Iterator[None]:
    """Fail any test that leaves an execution actor bound after it finishes."""
    yield
    # A test that never imported the web app cannot have set an actor, so never import it here.
    module = sys.modules.get("pandrator.web.accounts.execution_actor")
    if module is None:
        return
    leaked = module.current_execution_actor.get()
    if leaked is None:
        return
    module.current_execution_actor.set(None)
    pytest.fail(
        f"Test left a {type(leaked).__name__} bound as the execution actor; "
        "scope it with execution_actor_scope or host_execution_actor.",
        pytrace=False,
    )
