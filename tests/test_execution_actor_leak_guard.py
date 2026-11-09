"""End-to-end proof of the autouse execution-actor leak check in ``tests/conftest.py``.

The check runs in a separate pytest process on generated tests, so the deliberate leaks
cannot fail the real run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

GENERATED = '''
import unittest

import pytest

from pandrator.web.accounts.execution_actor import (
    HostActor,
    current_execution_actor,
    execution_actor_scope,
)

ACTOR = HostActor("pandrator-cli")


def test_a_function_leak():
    current_execution_actor.set(ACTOR)


def test_b_starts_clean_after_a_function_leak():
    assert current_execution_actor.get() is None


class LeakInSetUp(unittest.TestCase):
    def setUp(self):
        current_execution_actor.set(ACTOR)

    def test_c_unittest_setup_leak(self):
        pass


def test_d_starts_clean_after_a_setup_leak():
    assert current_execution_actor.get() is None


class LeakInSetUpClass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current_execution_actor.set(ACTOR)

    def test_e_unittest_setup_class_leak(self):
        pass


def test_f_starts_clean_after_a_setup_class_leak():
    assert current_execution_actor.get() is None


@pytest.fixture
def leaking_fixture():
    current_execution_actor.set(ACTOR)
    yield


def test_g_fixture_leak(leaking_fixture):
    pass


def test_h_starts_clean_after_a_fixture_leak():
    assert current_execution_actor.get() is None


def test_i_scoped_actor():
    with execution_actor_scope(ACTOR):
        assert current_execution_actor.get() is ACTOR


def test_j_host_execution_actor(host_execution_actor):
    assert current_execution_actor.get() is not None
'''

NO_WEB_IMPORT = '''
import sys


def test_k_never_imports_the_web_app():
    assert "pandrator.web" not in sys.modules


def test_l_still_unimported_after_the_first_test_teardown():
    assert "pandrator.web" not in sys.modules
'''


Results = dict[str, tuple[set[str], str]]


def _run(tmp_path: Path, name: str, source: str) -> tuple[int, str, Results]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    shutil.copyfile(REPO_ROOT / "tests" / "conftest.py", tests_dir / "conftest.py")
    (tests_dir / name).write_text(source, encoding="utf-8")
    report = tmp_path / "junit.xml"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(REPO_ROOT), environment.get("PYTHONPATH")))
    )
    # Options meant for the outer run, such as --basetemp, must not reach this one.
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
            f"--junitxml={report}", str(tests_dir / name),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    results: Results = {}
    if report.exists():
        for case in ElementTree.parse(report).iter("testcase"):
            kinds = {child.tag for child in case if child.tag in {"error", "failure", "skipped"}}
            text = " ".join(
                (child.get("message") or "") + (child.text or "")
                for child in case
                if child.tag in {"error", "failure"}
            )
            results[case.get("name", "")] = (kinds or {"passed"}, text)
    return result.returncode, result.stdout + result.stderr, results


@pytest.fixture(scope="module")
def leak_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, str, Results]:
    return _run(tmp_path_factory.mktemp("leak"), "test_generated.py", GENERATED)


def test_a_leak_fails_the_run(leak_run):
    code, output, _ = leak_run
    assert code != 0, output


@pytest.mark.parametrize(
    "leaking_test",
    [
        "test_a_function_leak",
        "test_c_unittest_setup_leak",
        "test_e_unittest_setup_class_leak",
        "test_g_fixture_leak",
    ],
)
def test_each_kind_of_leak_is_reported_as_an_error_naming_the_actor_type(leak_run, leaking_test):
    _, output, results = leak_run
    kinds, text = results.get(leaking_test, (set(), ""))
    assert kinds == {"error"}, output
    assert "Test left a HostActor bound as the execution actor" in text, output


def test_leak_report_does_not_print_the_actor(leak_run):
    _, output, results = leak_run
    assert results, output
    assert "pandrator-cli" not in output
    assert all("pandrator-cli" not in text for _, text in results.values())


@pytest.mark.parametrize(
    "following_test",
    [
        "test_b_starts_clean_after_a_function_leak",
        "test_d_starts_clean_after_a_setup_leak",
        "test_f_starts_clean_after_a_setup_class_leak",
        "test_h_starts_clean_after_a_fixture_leak",
    ],
)
def test_failed_check_clears_the_actor_for_the_next_test(leak_run, following_test):
    _, output, results = leak_run
    assert results.get(following_test, (set(), ""))[0] == {"passed"}, output


@pytest.mark.parametrize("scoped_test", ["test_i_scoped_actor", "test_j_host_execution_actor"])
def test_scoped_actors_pass(leak_run, scoped_test):
    _, output, results = leak_run
    assert results.get(scoped_test, (set(), ""))[0] == {"passed"}, output


def test_a_test_that_never_imports_the_web_app_passes_and_leaves_it_unimported(tmp_path):
    code, output, results = _run(tmp_path, "test_no_web.py", NO_WEB_IMPORT)
    assert code == 0, output
    assert {name: kinds for name, (kinds, _) in results.items()} == {
        "test_k_never_imports_the_web_app": {"passed"},
        "test_l_still_unimported_after_the_first_test_teardown": {"passed"},
    }, output
