import contextlib
import io
import json
import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import test_lanes


class TestLaneManifestTests(unittest.TestCase):
    def test_manifest_covers_every_current_test_file_exactly_once(self):
        test_lanes.validate_manifest()

    def test_validation_reports_unknown_missing_and_duplicate_paths(self):
        manifest = {
            "first": ("tests/test_duplicate.py", "tests/test_unknown.py"),
            "second": ("tests/test_duplicate.py",),
        }
        with self.assertRaises(test_lanes.TestLaneManifestError) as raised:
            test_lanes.validate_manifest(
                manifest,
                discovered={"tests/test_duplicate.py", "tests/test_missing.py"},
            )

        message = str(raised.exception)
        self.assertIn("unknown paths: tests/test_unknown.py", message)
        self.assertIn("missing paths: tests/test_missing.py", message)
        self.assertIn(
            "duplicate paths: tests/test_duplicate.py (first, second)",
            message,
        )

    def test_ci_workflows_reference_the_split_web07_lanes(self):
        root = Path(__file__).resolve().parents[1]
        preview = (root / ".github/workflows/web-preview.yml").read_text()
        windows = (root / ".github/workflows/windows-serial-lanes.yml").read_text()
        for index in range(1, 13):
            lane = f"web-07-{index:02d}-serial"
            self.assertIn(lane, test_lanes.TEST_LANES)
            self.assertIn(f"lanes: {lane}", preview)
            self.assertIn(f"          - {lane}\n", windows)
        self.assertNotIn("lanes: web-07-serial", preview)
        self.assertNotIn("          - web-07-serial\n", windows)

    def test_serial_web_lane_size_is_bounded(self):
        paths = tuple(f"tests/test_many_{i:03d}.py" for i in range(61))
        with self.assertRaises(test_lanes.TestLaneManifestError) as raised:
            test_lanes.validate_manifest(
                {"web-overgrown-serial": paths}, discovered=set(paths)
            )
        self.assertIn(
            "oversized serial web lanes (>60 files): web-overgrown-serial (61)",
            str(raised.exception),
        )

    def test_payload_is_manifest_ordered_and_json_serializable(self):
        manifest = {
            "second": ("tests/test_second.py",),
            "first": ("tests/test_first.py",),
        }

        payload = test_lanes.lane_payload(manifest)

        self.assertEqual(
            payload,
            {
                "lanes": [
                    {"name": "second", "files": ["tests/test_second.py"]},
                    {"name": "first", "files": ["tests/test_first.py"]},
                ]
            },
        )
        self.assertEqual(json.loads(json.dumps(payload, sort_keys=True)), payload)

    def test_project_and_pixi_configuration_keep_lane_contracts_explicit(self):
        project = tomllib.loads(
            (test_lanes.REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        dev_dependencies = project["project"]["optional-dependencies"]["dev"]
        self.assertIn("build>=1.6,<2", dev_dependencies)
        self.assertIn("pytest-xdist==3.8.0", dev_dependencies)
        pytest_options = project["tool"]["pytest"]["ini_options"]
        self.assertIn("--strict-markers", pytest_options["addopts"])

        pixi = tomllib.loads(
            (test_lanes.REPO_ROOT / "pixi.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(pixi["environments"]["default"], ["dev"])
        tasks = pixi["tasks"]
        self.assertEqual(
            tasks["check-test-lanes"],
            "python scripts/test_lanes.py check",
        )
        self.assertEqual(
            tasks["test-fast"],
            {
                "cmd": "python scripts/test_lanes.py run fast-xdist",
                "depends-on": ["check-test-lanes"],
            },
        )
        for task_name in ("test-full", "test-profile"):
            with self.subTest(task=task_name):
                self.assertEqual(tasks[task_name]["depends-on"], ["check-test-lanes"])
                self.assertNotIn("-n", tasks[task_name]["cmd"])
        self.assertIn(
            "--durations=30 --durations-min=0.5",
            tasks["test-profile"]["cmd"],
        )


def _matrix_lanes_by_os(workflow_text: str) -> dict[str, list[str]]:
    """Read the python-lanes matrix of web-preview.yml without a YAML parser."""
    _, found, job = workflow_text.partition("\n  python-lanes:\n")
    if not found:
        raise AssertionError("web-preview.yml has no python-lanes job")
    job = re.split(r"^  \S", job, maxsplit=1, flags=re.M)[0]
    _, found, after = job.partition("\n        include:\n")
    if not found:
        raise AssertionError("web-preview.yml has no python-lanes matrix")
    kept: list[str] = []
    for line in after.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        if not line.lstrip().startswith("#"):
            kept.append(line)
    by_os: dict[str, list[str]] = {}
    for entry in re.split(r"^ {10}- ", "\n".join(kept), flags=re.M)[1:]:
        os_match = re.search(r"^\s*os: (\S+)\s*$", entry, re.M)
        lanes_match = re.search(
            r"^\s*lanes: (?:>-?\s*)?(.*?)(?=^\s*\w+:|\Z)", entry, re.M | re.S
        )
        if os_match is None or lanes_match is None:
            raise AssertionError(f"matrix entry lacks os or lanes: {entry!r}")
        by_os.setdefault(os_match.group(1), []).extend(lanes_match.group(1).split())
    return by_os


def _matrix_problems(by_os: dict[str, list[str]], manifest_lanes: set[str]) -> list[str]:
    problems: list[str] = []
    for os_name in sorted({"ubuntu-latest", "windows-latest", *by_os}):
        lanes = by_os.get(os_name, [])
        for lane in sorted(manifest_lanes - set(lanes)):
            problems.append(f"{os_name}: lane {lane} is missing from the matrix")
        for lane in sorted(set(lanes) - manifest_lanes):
            problems.append(f"{os_name}: matrix names unknown lane {lane}")
        for lane in sorted({name for name in lanes if lanes.count(name) > 1}):
            problems.append(f"{os_name}: lane {lane} is listed twice")
    return problems


def _manual_windows_lanes(workflow_text: str) -> list[str]:
    """Read the lane list of windows-serial-lanes.yml without a YAML parser."""
    _, found, after = workflow_text.partition("\n        lane:\n")
    listed = re.match(r"(?: {10}- \S+\n)+", after) if found else None
    if listed is None:
        raise AssertionError("windows-serial-lanes.yml has no lane list")
    return re.findall(r"- (\S+)", listed.group())


def _workflow_text(name: str) -> str:
    return (test_lanes.REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


class TestLaneMatrixTests(unittest.TestCase):
    def test_ci_matrix_runs_every_manifest_lane_on_both_operating_systems(self):
        by_os = _matrix_lanes_by_os(_workflow_text("web-preview.yml"))
        self.assertEqual([], _matrix_problems(by_os, set(test_lanes.TEST_LANES)))

    def test_manual_windows_workflow_names_only_manifest_lanes(self):
        lanes = _manual_windows_lanes(_workflow_text("windows-serial-lanes.yml"))
        self.assertEqual([], sorted(set(lanes) - set(test_lanes.TEST_LANES)))

    def test_windows_jobs_fail_when_ffmpeg_is_not_installed(self):
        # web-preview.yml installs FFmpeg inline as upstream wrote it; every
        # Windows install must still fail when ffprobe is missing.
        preview = _workflow_text("web-preview.yml")
        self.assertEqual(2, preview.count("- name: Install Windows FFmpeg"))
        self.assertEqual(2, preview.count("Get-Command ffprobe"))
        windows = _workflow_text("windows-serial-lanes.yml")
        self.assertEqual(1, windows.count("uses: ./.github/actions/install-windows-ffmpeg"))
        self.assertNotIn("choco install", windows)

    def test_matrix_check_names_a_missing_lane_and_an_unknown_lane(self):
        manifest_lanes = {"fast-xdist", "web-01-serial", "web-02-serial"}
        by_os = {
            "ubuntu-latest": ["fast-xdist", "web-01-serial", "web-02-serial"],
            "windows-latest": ["fast-xdist", "web-01-serial", "web-99-serial"],
            "macos-latest": ["fast-xdist", "web-01-serial", "web-02-serial", "web-98-serial"],
        }
        self.assertEqual(
            [
                "macos-latest: matrix names unknown lane web-98-serial",
                "windows-latest: lane web-02-serial is missing from the matrix",
                "windows-latest: matrix names unknown lane web-99-serial",
            ],
            _matrix_problems(by_os, manifest_lanes),
        )

    def test_matrix_check_names_a_duplicate_lane_and_an_absent_operating_system(self):
        self.assertEqual(
            [
                "ubuntu-latest: lane web-01-serial is listed twice",
                "windows-latest: lane web-01-serial is missing from the matrix",
            ],
            _matrix_problems(
                {"ubuntu-latest": ["web-01-serial", "web-01-serial"]}, {"web-01-serial"}
            ),
        )

    def test_a_lane_in_two_entries_stays_listed_twice(self):
        text = (
            "jobs:\n"
            "  python-lanes:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        include:\n"
            "          - name: ubuntu-services\n"
            "            os: ubuntu-latest\n"
            "            lanes: >-\n"
            "              mcp-serial manager-serial\n"
            "          - name: ubuntu-mcp\n"
            "            os: ubuntu-latest\n"
            "            lanes: mcp-serial\n"
        )
        self.assertEqual(
            {"ubuntu-latest": ["mcp-serial", "manager-serial", "mcp-serial"]},
            _matrix_lanes_by_os(text),
        )

    def test_an_unreadable_matrix_fails_instead_of_passing_empty(self):
        job = "jobs:\n  python-lanes:\n    steps:\n"
        later_matrix = (
            "  e2e:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        include:\n"
            "          - os: ubuntu-latest\n"
            "            lanes: not-counted\n"
        )
        entry = "jobs:\n  python-lanes:\n    strategy:\n      matrix:\n        include:\n"
        for name, text in {
            "no job": "jobs:\n  other:\n    steps:\n",
            "no matrix": job,
            "matrix only in a later job": job + later_matrix,
            "entry without lanes": entry + "          - os: ubuntu-latest\n",
            "entry without os": entry + "          - lanes: web-01-serial\n",
        }.items():
            with self.subTest(name), self.assertRaises(AssertionError):
                _matrix_lanes_by_os(text)

    def test_a_missing_manual_lane_list_fails_instead_of_passing_empty(self):
        for name, text in {
            "no list": "jobs:\n  python-lanes:\n    steps:\n",
            "empty list": "      matrix:\n        lane:\n    steps:\n",
        }.items():
            with self.subTest(name), self.assertRaises(AssertionError):
                _manual_windows_lanes(text)

    def test_folded_lane_lists_count_every_lane(self):
        text = (
            "jobs:\n"
            "  python-lanes:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        include:\n"
            "          - name: ubuntu-services\n"
            "            os: ubuntu-latest\n"
            "            lanes: >-\n"
            "              installer-serial manager-serial mcp-serial\n"
            "              system-media-serial\n"
            "        exclude:\n"
            "          - name: ignored\n"
            "            os: ubuntu-latest\n"
            "            lanes: not-counted\n"
            "    steps:\n"
        )
        self.assertEqual(
            {
                "ubuntu-latest": [
                    "installer-serial",
                    "manager-serial",
                    "mcp-serial",
                    "system-media-serial",
                ]
            },
            _matrix_lanes_by_os(text),
        )


class TestLaneCommandTests(unittest.TestCase):
    def test_list_json_is_stable(self):
        outputs: list[str] = []
        for _ in range(2):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(0, test_lanes.main(["list", "--json"]))
            outputs.append(stream.getvalue())

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(test_lanes.lane_payload(), json.loads(outputs[0]))

    def test_serial_command_uses_explicit_deduplicated_files(self):
        command = test_lanes.build_pytest_command(
            ["installer-serial", "web-01-serial", "installer-serial"],
            Path("artifacts/results.xml"),
        )

        self.assertEqual(
            command[:6],
            [
                test_lanes.sys.executable,
                "-m",
                "pytest",
                "-q",
                "--junitxml",
                str(Path("artifacts/results.xml")),
            ],
        )
        self.assertEqual(
            command[6:],
            [
                *test_lanes.TEST_LANES["installer-serial"],
                *test_lanes.TEST_LANES["web-01-serial"],
            ],
        )
        self.assertNotIn("-n", command)
        self.assertNotIn("--dist=loadfile", command)

    def test_fast_command_adds_only_the_vetted_xdist_options(self):
        command = test_lanes.build_pytest_command(["fast-xdist"])

        self.assertEqual(
            command[:7],
            [
                test_lanes.sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "2",
                "--dist=loadfile",
            ],
        )
        self.assertEqual(command[7:], list(test_lanes.TEST_LANES["fast-xdist"]))

    def test_fast_lane_cannot_share_an_invocation_with_serial_lanes(self):
        with self.assertRaisesRegex(
            test_lanes.TestLaneUsageError,
            "cannot be combined with serial test lanes",
        ):
            test_lanes.build_pytest_command(["fast-xdist", "installer-serial"])

    def test_unknown_lane_is_rejected_before_pytest_is_started(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(1, test_lanes.main(["run", "not-a-lane"]))

        self.assertIn("Unknown test lane: not-a-lane", stderr.getvalue())

    def test_run_propagates_pytest_exit_code_and_creates_junit_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            junitxml = Path(directory) / "nested" / "results.xml"
            with (
                patch.object(test_lanes, "validate_manifest") as validate,
                patch.object(
                    test_lanes.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=7),
                ) as run,
            ):
                self.assertEqual(
                    7,
                    test_lanes.run_lanes(["installer-serial"], junitxml),
                )

            validate.assert_called_once_with()
            self.assertTrue(junitxml.parent.is_dir())
            self.assertEqual(
                run.call_args.args[0],
                test_lanes.build_pytest_command(["installer-serial"], junitxml),
            )
            self.assertEqual(run.call_args.kwargs, {"check": False})


if __name__ == "__main__":
    unittest.main()
