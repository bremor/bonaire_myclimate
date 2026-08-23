"""Tests for MyClimate TCP message framing."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest


HELPERS_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "bonaire_myclimate"
    / "BonairePyClimate"
    / "helpers.py"
)
PACKAGE_NAME = "bonaire_test_package"
PACKAGE = types.ModuleType(PACKAGE_NAME)
PACKAGE.__path__ = []
CONSTANTS = types.ModuleType(f"{PACKAGE_NAME}.const")
CONSTANTS.PORT_DISCOVERY = 10001
CONSTANTS.PORT_LOCAL = 10003
sys.modules[PACKAGE_NAME] = PACKAGE
sys.modules[CONSTANTS.__name__] = CONSTANTS

SPEC = importlib.util.spec_from_file_location(f"{PACKAGE_NAME}.helpers", HELPERS_PATH)
HELPERS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPERS
SPEC.loader.exec_module(HELPERS)
HandleServer = HELPERS.HandleServer


MESSAGE_ONE = b"<myclimate><response>discovery</response></myclimate>"
MESSAGE_TWO = b"<myclimate><response>installation</response></myclimate>"


class TestHandleServer(unittest.TestCase):
    """Verify that the stream protocol emits complete XML documents."""

    def setUp(self):
        self.messages = []
        self.protocol = HandleServer(lambda _: None, self.messages.append, lambda: None)

    def test_complete_message(self):
        self.protocol.data_received(MESSAGE_ONE)

        self.assertEqual(self.messages, [MESSAGE_ONE])

    def test_fragmented_message(self):
        split_at = 20

        self.protocol.data_received(MESSAGE_ONE[:split_at])
        self.assertEqual(self.messages, [])

        self.protocol.data_received(MESSAGE_ONE[split_at:])
        self.assertEqual(self.messages, [MESSAGE_ONE])

    def test_concatenated_messages(self):
        self.protocol.data_received(MESSAGE_ONE + MESSAGE_TWO)

        self.assertEqual(self.messages, [MESSAGE_ONE, MESSAGE_TWO])

    def test_complete_message_followed_by_fragment(self):
        split_at = 24

        self.protocol.data_received(MESSAGE_ONE + MESSAGE_TWO[:split_at])
        self.assertEqual(self.messages, [MESSAGE_ONE])

        self.protocol.data_received(MESSAGE_TWO[split_at:])
        self.assertEqual(self.messages, [MESSAGE_ONE, MESSAGE_TWO])

    def test_junk_before_message(self):
        self.protocol.data_received(b"\r\nnoise" + MESSAGE_ONE)

        self.assertEqual(self.messages, [MESSAGE_ONE])


if __name__ == "__main__":
    unittest.main()
