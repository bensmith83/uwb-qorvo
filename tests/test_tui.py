"""Headless integration test of the Textual dashboard with a fake device."""

import asyncio
from unittest.mock import patch

import pytest

from uwb_explorer.parser import parse_line
from uwb_explorer.tui import UwbExplorerApp


class FakeDev:
    version = "0.1.1-test"
    apps = ["LISTENER2", "INITF", "RESPF"]

    def __init__(self):
        self._q = [
            parse_line('{"Block":0,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":42,"LAoA_deg":12.5}]}'),
            parse_line('{"Block":1,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":47,"LAoA_deg":10.0}]}'),
            parse_line('JS00EF{"LSTN":[41,88,42,ca,de,01,00,02,00],"TS":"0x1","O":1,"rsl":-63.2}'),
        ]

    def detect(self):
        return True

    def poll_events(self):
        q, self._q = self._q, []
        return iter(q)

    def stop(self):
        pass


@pytest.mark.timeout(30)
def test_dashboard_populates_from_events():
    async def run():
        app = UwbExplorerApp(port="/dev/null")
        with patch("uwb_explorer.tui.open_cli", return_value=object()), \
             patch("uwb_explorer.tui.Device", return_value=FakeDev()):
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause(0.3)
                await pilot.pause(0.6)
                from textual.widgets import DataTable
                table = app.query_one("#contacts", DataTable)
                assert table.row_count == 2
                assert app.model.contacts["0x0001"].last_distance_cm == 47
                assert app.model.contacts["0x0001"].passive is False
                assert app.model.contacts["0x0002"].passive is True
                assert app.model.frame_count == 1
                await pilot.press("c")
                assert app.model.contacts == {}

    asyncio.run(run())
