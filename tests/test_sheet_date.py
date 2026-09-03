"""Offline tests for worksheet date detection and filename generation."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sheet_date import (  # noqa: E402
    cell_address,
    date_from_cell,
    find_date_in_grid,
    parse_a1_cell,
    parse_date_text,
    serial_to_date,
)


def _date_cell(serial, display, fmt="DATE"):
    return {
        "effectiveValue": {"numberValue": serial},
        "formattedValue": display,
        "effectiveFormat": {"numberFormat": {"type": fmt}},
    }


def _text_cell(text):
    return {"effectiveValue": {"stringValue": text}, "formattedValue": text}


def _number_cell(value, display):
    return {
        "effectiveValue": {"numberValue": value},
        "formattedValue": display,
        "effectiveFormat": {"numberFormat": {"type": "NUMBER"}},
    }


class ParseA1Tests(unittest.TestCase):
    def test_simple_cells(self):
        self.assertEqual(parse_a1_cell("A1"), (0, 0))
        self.assertEqual(parse_a1_cell("b2"), (1, 1))
        self.assertEqual(parse_a1_cell("Z10"), (9, 25))
        self.assertEqual(parse_a1_cell("AA3"), (2, 26))
        self.assertEqual(parse_a1_cell("$C$4"), (3, 2))

    def test_invalid(self):
        for bad in ("", "A", "1", "A0", "A1:B2", "Sheet1!A1", "A-1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_a1_cell(bad)


class SerialTests(unittest.TestCase):
    def test_known_serials(self):
        self.assertEqual(serial_to_date(1), date(1899, 12, 31))
        self.assertEqual(serial_to_date(46266), date(2026, 9, 1))
        self.assertEqual(serial_to_date(46267.75), date(2026, 9, 2))  # DATE_TIME with time part

    def test_garbage(self):
        self.assertIsNone(serial_to_date(-5))
        self.assertIsNone(serial_to_date(float("nan")))
        self.assertIsNone(serial_to_date(float("inf")))


class ParseTextTests(unittest.TestCase):
    Y = 2026

    def test_numeric_formats(self):
        self.assertEqual(parse_date_text("01.09.2026", self.Y), date(2026, 9, 1))
        self.assertEqual(parse_date_text("Sana: 2.09.2026", self.Y), date(2026, 9, 2))
        self.assertEqual(parse_date_text("02/09/2026", self.Y), date(2026, 9, 2))
        self.assertEqual(parse_date_text("02-09-26", self.Y), date(2026, 9, 2))
        self.assertEqual(parse_date_text("2026-09-01", self.Y), date(2026, 9, 1))
        self.assertEqual(parse_date_text("Hisobot 2026-09-01 holatiga", self.Y), date(2026, 9, 1))

    def test_month_names(self):
        self.assertEqual(parse_date_text("1-sentyabr", self.Y), date(2026, 9, 1))
        self.assertEqual(parse_date_text("2 sentabr 2026", self.Y), date(2026, 9, 2))
        self.assertEqual(parse_date_text("1 - сентябрь 2026 й.", self.Y), date(2026, 9, 1))
        self.assertEqual(parse_date_text("3 сентября 2026", self.Y), date(2026, 9, 3))
        self.assertEqual(parse_date_text("Sep 1, 2026", self.Y), date(2026, 9, 1))
        self.assertEqual(parse_date_text("KUNLIK HISOBOT — 31-avgust", self.Y), date(2026, 8, 31))
        self.assertEqual(parse_date_text("15 may", self.Y), date(2026, 5, 15))
        self.assertEqual(parse_date_text("15 мая", self.Y), date(2026, 5, 15))

    def test_not_dates(self):
        for text in ("", "Jami", "Savdo hisoboti", "12.5", "Jami 12 ta", "mart oyi rejasi",
                     "1234567", "3 ta mahsulot", "100 000 so'm", "32.13.2026", "2 martaba"):
            self.assertIsNone(parse_date_text(text, self.Y), msg=text)


class CellTests(unittest.TestCase):
    Y = 2026

    def test_date_formatted_number(self):
        cell = {
            "effectiveValue": {"numberValue": 46266},
            "formattedValue": "01.09.2026",
            "effectiveFormat": {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}},
        }
        self.assertEqual(date_from_cell(cell, self.Y), date(2026, 9, 1))

    def test_datetime_formatted_number(self):
        cell = {
            "effectiveValue": {"numberValue": 46267.5},
            "formattedValue": "02.09.2026 12:00",
            "effectiveFormat": {"numberFormat": {"type": "DATE_TIME"}},
        }
        self.assertEqual(date_from_cell(cell, self.Y), date(2026, 9, 2))

    def test_plain_number_is_not_a_date(self):
        cell = {
            "effectiveValue": {"numberValue": 46266},
            "formattedValue": "46266",
            "effectiveFormat": {"numberFormat": {"type": "NUMBER"}},
        }
        self.assertIsNone(date_from_cell(cell, self.Y))

    def test_text_date(self):
        cell = {"effectiveValue": {"stringValue": "Sana: 01.09.2026"}, "formattedValue": "Sana: 01.09.2026"}
        self.assertEqual(date_from_cell(cell, self.Y), date(2026, 9, 1))

    def test_formula_cell_uses_formatted_value(self):
        # =TODAY()-2 style cells: effectiveValue is a number with a DATE format
        cell = {
            "effectiveValue": {"numberValue": 46266},
            "formattedValue": "1 сентябрь",
            "effectiveFormat": {"numberFormat": {"type": "DATE", "pattern": "d mmmm"}},
        }
        self.assertEqual(date_from_cell(cell, self.Y), date(2026, 9, 1))

    def test_empty(self):
        self.assertIsNone(date_from_cell({}, self.Y))
        self.assertIsNone(date_from_cell({"formattedValue": ""}, self.Y))


class GridScanTests(unittest.TestCase):
    Y = 2026

    def test_reading_order_first_date_wins(self):
        row_data = [
            {"values": [{"formattedValue": "SAVDO HISOBOTI"}, {}, {"formattedValue": "Jami 12 ta"}]},
            {},  # empty row -> no "values" key
            {"values": [{"formattedValue": "Sana:"}, _date_cell(46266, "01.09.2026")]},
            {"values": [{"formattedValue": "02.09.2026"}]},
        ]
        found = find_date_in_grid(row_data, self.Y)
        self.assertEqual(found.value, date(2026, 9, 1))
        self.assertEqual((found.row, found.column), (2, 1))
        self.assertEqual(cell_address(found.row, found.column), "B3")
        self.assertTrue(found.visible)

    def test_visible_full_date_beats_earlier_partial_display(self):
        # Header row holds real date values but only *shows* the year / month
        # number (e.g. =EOMONTH(...) formatted "yyyy" / "m"); the reader sees
        # "01.09.2026" as the date, so that cell must win.
        row_data = [
            {"values": [_date_cell(46266, "2026"), _date_cell(46266, "2026"), _date_cell(46265, "2026")]},
            {"values": [_date_cell(46266, "01.09.2026"), _date_cell(46266, "9"), _date_cell(46265, "8")]},
        ]
        found = find_date_in_grid(row_data, self.Y)
        self.assertEqual(found.value, date(2026, 9, 1))
        self.assertEqual((found.row, found.column), (1, 0))
        self.assertTrue(found.visible)

    def test_partial_display_is_only_a_fallback(self):
        row_data = [
            {"values": [_number_cell(2026, "2026"), _date_cell(46265, "8")]},
            {"values": [{"formattedValue": "Jami"}]},
        ]
        found = find_date_in_grid(row_data, self.Y)
        self.assertEqual(found.value, date(2026, 8, 31))
        self.assertFalse(found.visible)
        self.assertEqual((found.row, found.column), (0, 1))

    def test_hidden_rows_and_columns_are_skipped(self):
        row_data = [
            {"values": [_date_cell(46265, "31.08.2026")]},  # hidden helper row
            {"values": [_date_cell(46264, "30.08.2026"), _date_cell(46266, "01.09.2026")]},
        ]
        found = find_date_in_grid(row_data, self.Y, hidden_rows={0}, hidden_columns={0})
        self.assertEqual(found.value, date(2026, 9, 1))
        self.assertEqual((found.row, found.column), (1, 1))
        # Without hiding, reading order applies.
        self.assertEqual(find_date_in_grid(row_data, self.Y).value, date(2026, 8, 31))

    def test_real_value_trusted_over_locale_text(self):
        # US-locale display "9/1/2026" would be misread as 9 January by the
        # day-first text parser; the serial says 1 September.
        found = find_date_in_grid([{"values": [_date_cell(46266, "9/1/2026")]}], self.Y)
        self.assertEqual(found.value, date(2026, 9, 1))
        self.assertTrue(found.visible)

    def test_no_date(self):
        self.assertIsNone(find_date_in_grid([{"values": [{"formattedValue": "x"}]}], self.Y))
        self.assertIsNone(find_date_in_grid([], self.Y))


def _make_config(tz="Asia/Tashkent"):
    cfg = MagicMock()
    cfg.spreadsheet_id = "SPREADSHEET"
    cfg.timezone = ZoneInfo(tz)
    return cfg


def _grid_response(sheet_id, row_data):
    return {"sheets": [{"properties": {"sheetId": sheet_id}, "data": [{"rowData": row_data}]}]}


class SheetsServiceDateTests(unittest.TestCase):
    def _service(self, response=None, side_effect=None):
        from app.sheets_service import SheetsService

        service = SheetsService.__new__(SheetsService)
        service._config = _make_config()
        api = MagicMock()
        execute = api.spreadsheets.return_value.getByDataFilter.return_value.execute
        if side_effect is not None:
            execute.side_effect = side_effect
        else:
            execute.return_value = response
        service._api = api
        return service, api

    def test_auto_detect_uses_scan_range(self):
        row_data = [{"values": [{"formattedValue": "Sana: 02.09.2026"}]}]
        service, api = self._service(_grid_response(111, row_data))
        self.assertEqual(service.get_worksheet_date(111), date(2026, 9, 2))
        body = api.spreadsheets.return_value.getByDataFilter.call_args.kwargs["body"]
        self.assertEqual(body["dataFilters"][0]["gridRange"], {
            "sheetId": 111, "startRowIndex": 0, "endRowIndex": 40,
            "startColumnIndex": 0, "endColumnIndex": 26,
        })
        self.assertTrue(body["includeGridData"])

    def test_explicit_cell_requests_single_cell(self):
        row_data = [{"values": [{
            "effectiveValue": {"numberValue": 46266},
            "formattedValue": "01.09.2026",
            "effectiveFormat": {"numberFormat": {"type": "DATE"}},
        }]}]
        service, api = self._service(_grid_response(222, row_data))
        self.assertEqual(service.get_worksheet_date(222, "C3"), date(2026, 9, 1))
        body = api.spreadsheets.return_value.getByDataFilter.call_args.kwargs["body"]
        self.assertEqual(body["dataFilters"][0]["gridRange"], {
            "sheetId": 222, "startRowIndex": 2, "endRowIndex": 3,
            "startColumnIndex": 2, "endColumnIndex": 3,
        })

    def test_hidden_rows_from_metadata_are_skipped_and_cell_logged(self):
        response = {"sheets": [{"properties": {"sheetId": 111}, "data": [{
            "startRow": 0, "startColumn": 0,
            "rowMetadata": [{"hiddenByUser": True}, {}, {"hiddenByFilter": True}, {}],
            "columnMetadata": [{}, {"hiddenByUser": True}, {}],
            "rowData": [
                {"values": [_date_cell(46265, "31.08.2026")]},
                {"values": [{"formattedValue": "Savdo"}, _date_cell(46264, "30.08.2026"), {"formattedValue": "2026"}]},
                {"values": [_date_cell(46263, "29.08.2026")]},
                {"values": [{}, {}, _date_cell(46266, "01.09.2026")]},
            ],
        }]}]}
        service, _ = self._service(response)
        with self.assertLogs("app.sheets_service", level="INFO") as logs:
            self.assertEqual(service.get_worksheet_date(111), date(2026, 9, 1))
        self.assertIn("cell C4 displays '01.09.2026'", logs.output[0])

    def test_explicit_cell_ignores_hidden_flag(self):
        response = {"sheets": [{"properties": {"sheetId": 111}, "data": [{
            "startRow": 1, "startColumn": 2,
            "rowMetadata": [{"hiddenByUser": True}],
            "columnMetadata": [{"hiddenByUser": True}],
            "rowData": [{"values": [_date_cell(46266, "01.09.2026")]}],
        }]}]}
        service, _ = self._service(response)
        with self.assertLogs("app.sheets_service", level="INFO") as logs:
            self.assertEqual(service.get_worksheet_date(111, "C2"), date(2026, 9, 1))
        self.assertIn("cell C2", logs.output[0])

    def test_ignores_other_sheets_in_response(self):
        response = {"sheets": [
            {"properties": {"sheetId": 999}, "data": [{"rowData": [{"values": [{"formattedValue": "05.05.2025"}]}]}]},
        ]}
        service, _ = self._service(response)
        self.assertIsNone(service.get_worksheet_date(111))

    def test_api_error_returns_none(self):
        from googleapiclient.errors import HttpError

        resp = MagicMock(status=500, reason="boom")
        service, _ = self._service(side_effect=HttpError(resp, b"{}"))
        self.assertIsNone(service.get_worksheet_date(111))

    def test_empty_sheet_returns_none(self):
        service, _ = self._service({"sheets": [{"properties": {"sheetId": 111}, "data": [{}]}]})
        self.assertIsNone(service.get_worksheet_date(111))


class PDFServiceFilenameTests(unittest.TestCase):
    def _pdf_service(self, sheet_date):
        from app.pdf_service import PDFService

        sheets = MagicMock()
        sheets.get_worksheet_date.return_value = sheet_date
        sheets.export_worksheet_pdf.return_value = b"%PDF-1.4 fake"
        cfg = _make_config()
        service = PDFService(sheets, cfg)
        service._tmp_dir = Path(tempfile.mkdtemp())
        return service, sheets

    def test_filename_uses_sheet_date_not_today(self):
        service, sheets = self._pdf_service(date(2026, 9, 1))
        path = service.generate_worksheet_pdf(111, "savdo", "B2")
        try:
            self.assertEqual(path.name, "savdo_2026-09-01.pdf")
            self.assertEqual(path.read_bytes(), b"%PDF-1.4 fake")
            sheets.get_worksheet_date.assert_called_once_with(111, "B2")
        finally:
            service.cleanup(path)

    def test_filename_falls_back_to_today(self):
        service, sheets = self._pdf_service(None)
        today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
        path = service.generate_worksheet_pdf(222, "qoldiq")
        try:
            self.assertEqual(path.name, f"qoldiq_{today}.pdf")
            sheets.get_worksheet_date.assert_called_once_with(222, None)
        finally:
            service.cleanup(path)


class ConfigDateCellTests(unittest.TestCase):
    BASE_ENV = {
        "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHANNEL_ID": "-100", "ADMIN_TELEGRAM_ID": "1",
        "GOOGLE_SHEET_URL": "https://docs.google.com/spreadsheets/d/abc/edit",
        "WORKSHEET_1_ID": "1", "WORKSHEET_2_ID": "2",
    }

    def _load(self, extra):
        from app.config import load_config

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
            key_path = f.name
        env = {**self.BASE_ENV, "GOOGLE_SERVICE_ACCOUNT_FILE": key_path, **extra}
        try:
            with patch.dict(os.environ, env, clear=True):
                return load_config(env_file="/nonexistent/.env")
        finally:
            os.unlink(key_path)

    def test_defaults_are_the_production_cells(self):
        cfg = self._load({})
        self.assertEqual(cfg.worksheet_1.date_cell, "E4")
        self.assertEqual(cfg.worksheet_2.date_cell, "B1")

    def test_blank_means_default_and_auto_means_scan(self):
        cfg = self._load({"WORKSHEET_1_DATE_CELL": "  ", "WORKSHEET_2_DATE_CELL": "auto"})
        self.assertEqual(cfg.worksheet_1.date_cell, "E4")
        self.assertIsNone(cfg.worksheet_2.date_cell)

    def test_explicit_cells_normalised(self):
        cfg = self._load({"WORKSHEET_1_DATE_CELL": " b2 ", "WORKSHEET_2_DATE_CELL": "$D$5"})
        self.assertEqual(cfg.worksheet_1.date_cell, "B2")
        self.assertEqual(cfg.worksheet_2.date_cell, "D5")

    def test_invalid_cell_rejected(self):
        from app.exceptions import ConfigError

        with self.assertRaises(ConfigError):
            self._load({"WORKSHEET_1_DATE_CELL": "A1:B2"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
