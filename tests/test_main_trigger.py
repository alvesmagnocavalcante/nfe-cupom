import argparse
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main_trigger


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.temporary = self.root / "temporary"
        self.heartbeat_destination = self.root / "heartbeat"
        for directory in (
            self.source,
            self.destination,
            self.temporary,
            self.heartbeat_destination,
        ):
            directory.mkdir()
        self.now = datetime.now()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def settings(self, sources=None):
        return main_trigger.Settings(
            sources=tuple(sources or (self.source,)),
            destination=self.destination,
            temporary=self.temporary,
            heartbeat_destination=self.heartbeat_destination,
            heartbeat_file=self.root / "state" / "ultima_execucao.txt",
            hotel="Teste",
        )

    def create_current_xml(self, directory, name="nfe.xml", content="<nfe />"):
        path = directory / name
        path.write_text(content, encoding="utf-8")
        timestamp = self.now.timestamp()
        os.utime(path, (timestamp, timestamp))
        return path

    def test_sync_replaces_same_size_different_content(self):
        source = self.create_current_xml(self.source, content="<a>1</a>")
        target = self.create_current_xml(self.destination, content="<a>2</a>")

        result = main_trigger.sync(self.settings(), now=self.now)

        self.assertEqual(result, 0)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertFalse(list(self.destination.glob("*.part")))

    def test_sync_rejects_duplicate_names_from_different_sources(self):
        second_source = self.root / "second-source"
        second_source.mkdir()
        self.create_current_xml(self.source, content="<first />")
        self.create_current_xml(second_source, content="<second />")

        with self.assertRaisesRegex(RuntimeError, "Colisão de nomes entre origens"):
            main_trigger.sync(self.settings((self.source, second_source)), now=self.now)

        self.assertFalse((self.destination / "nfe.xml").exists())

    def test_atomic_copy_validates_content_and_removes_partial_file(self):
        source = self.create_current_xml(self.source, content="conteúdo válido")
        target = self.destination / source.name

        main_trigger.atomic_copy_verified(source, target)

        self.assertTrue(main_trigger.files_match(source, target))
        self.assertFalse(list(self.destination.glob("*.part")))

    def test_corrupted_copy_does_not_replace_existing_target(self):
        source = self.create_current_xml(self.source, content="novo")
        target = self.destination / source.name
        target.write_text("atual", encoding="utf-8")

        def corrupt_copy(unused_source, temporary):
            temporary.write_text("ruim", encoding="utf-8")

        with patch.object(main_trigger.shutil, "copy2", side_effect=corrupt_copy):
            with self.assertRaisesRegex(OSError, "Conteúdo divergente"):
                main_trigger.atomic_copy_verified(source, target)

        self.assertEqual(target.read_text(encoding="utf-8"), "atual")
        self.assertFalse(list(self.destination.glob("*.part")))


class GoogleDriveTests(unittest.TestCase):
    def test_waits_for_directory_after_starting_google_drive(self):
        class DelayedDirectory:
            def __init__(self):
                self.available = False

            def is_dir(self):
                return self.available

            def mkdir(self, parents=False, exist_ok=False):
                if not self.available:
                    raise OSError("unidade indisponível")

            def __str__(self):
                return "H:\\Drive"

        directory = DelayedDirectory()

        def make_available():
            directory.available = True

        with (
            patch.object(main_trigger, "running_on_windows", return_value=True),
            patch.object(main_trigger, "start_google_drive", side_effect=make_available) as start,
        ):
            main_trigger.ensure_temporary_directory(directory, False, timeout_seconds=0, poll_seconds=0)

        start.assert_called_once_with()


class AlertTests(unittest.TestCase):
    def test_run_alerts_when_configuration_fails(self):
        arguments = argparse.Namespace(
            config=Path("inexistente.ini"),
            log_file=Path("ignorado.log"),
            dry_run=False,
        )
        with patch.object(main_trigger, "send_alert") as alert:
            result = main_trigger.run(arguments)

        self.assertEqual(result, 1)
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
