import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import windows_temp_root as wtr


class WindowsTempRootTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.drive = Path(tmp.name)
        # Anchor of a path under a fake drive: use the real anchor via a fake temp dir.
        self.temp_dir = self.drive / "Temp"
        self.patch_anchor()

    def patch_anchor(self):
        original = wtr.short_root_path
        self.original_short_root_path = original
        drive = self.drive

        def fake(temp_dir):
            if not Path(temp_dir).anchor:
                raise wtr.ShortRootUnavailable("no anchor")
            return drive / wtr.ROOT_NAME

        wtr.short_root_path = fake
        self.addCleanup(setattr, wtr, "short_root_path", original)

    def run_dir(self, **kwargs):
        kwargs.setdefault("platform", "win32")
        kwargs.setdefault("temp_dir", self.temp_dir)
        return wtr.make_run_dir(**kwargs)

    def test_windows_without_basetemp_makes_marked_run_dir(self):
        run = self.run_dir()
        root = self.drive / "pt"
        self.assertEqual(run.parent, root)
        self.assertTrue((root / wtr.MARKER_NAME).is_file())
        self.assertEqual(os.listdir(run), [])
        self.assertNotEqual(run, self.run_dir())

    def test_explicit_basetemp_is_untouched(self):
        self.assertIsNone(self.run_dir(explicit_basetemp=Path("x")))
        self.assertFalse((self.drive / "pt").exists())

    def test_other_platform_is_untouched(self):
        self.assertIsNone(self.run_dir(platform="linux"))
        self.assertFalse((self.drive / "pt").exists())

    def test_stale_runs_are_pruned_and_new_kept(self):
        first = self.run_dir()
        root = first.parent
        old = root / "old"
        old.mkdir()
        t = 1_000_000.0
        os.utime(old, (t, t))
        os.utime(first, (t + 3600, t + 3600))
        later = self.run_dir(clock=lambda: t + wtr.MAX_AGE_SECONDS + 10)
        self.assertFalse(old.exists())
        self.assertTrue(first.exists())
        self.assertTrue(later.exists())

    def test_foreign_folder_is_left_alone(self):
        foreign = self.drive / "pt"
        foreign.mkdir()
        (foreign / "keep").mkdir()
        with self.assertRaises(wtr.ShortRootUnavailable):
            self.run_dir()
        self.assertTrue((foreign / "keep").exists())

    def test_uncreatable_root_raises(self):
        blocker = self.drive / "blocker"
        blocker.write_text("a file, so nothing can be created beneath it")
        wtr.short_root_path = lambda temp_dir: blocker / wtr.ROOT_NAME
        with self.assertRaises(wtr.ShortRootUnavailable):
            self.run_dir()

    def test_empty_unmarked_root_is_adopted_and_marked(self):
        (self.drive / "pt").mkdir()
        run = self.run_dir()
        self.assertTrue((self.drive / "pt" / wtr.MARKER_NAME).is_file())
        self.assertEqual(run.parent, self.drive / "pt")

    def test_second_call_with_existing_root_still_returns_run_dir(self):
        first = self.run_dir()
        second = self.run_dir()
        self.assertEqual(first.parent, second.parent)
        self.assertNotEqual(first, second)

    def test_real_short_root_path(self):
        original = self.original_short_root_path
        absolute = Path(self.temp_dir).resolve()
        self.assertEqual(original(absolute), Path(absolute.anchor) / "pt")
        with self.assertRaises(wtr.ShortRootUnavailable):
            original(Path("relative") / "temp")


class PytestConfigureHookTests(unittest.TestCase):
    def test_sets_basetemp_without_warning(self):
        from tests import conftest

        config = SimpleNamespace(
            option=SimpleNamespace(basetemp=None), issue_config_time_warning=MagicMock()
        )
        with patch.object(conftest, "make_run_dir", return_value=Path("C:/pt/abc")):
            conftest.pytest_configure(config)
        self.assertEqual(config.option.basetemp, Path("C:/pt/abc"))
        config.issue_config_time_warning.assert_not_called()

    def test_warns_and_leaves_basetemp_when_root_unavailable(self):
        from tests import conftest

        config = SimpleNamespace(
            option=SimpleNamespace(basetemp=None), issue_config_time_warning=MagicMock()
        )
        with patch.object(conftest, "make_run_dir", side_effect=wtr.ShortRootUnavailable("no")):
            conftest.pytest_configure(config)
        self.assertIsNone(config.option.basetemp)
        config.issue_config_time_warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
