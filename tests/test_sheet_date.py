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

from app.sheets_service import WorksheetDate  # noqa: E402
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


class SheetsServiceLookupNoteTests(SheetsServiceDateTests):
    def test_explicit_empty_cell_note(self):
        service, _ = self._service(_grid_response(111, [{"values": [{}]}]))
        lookup = service.lookup_worksheet_date(111, "E4")
        self.assertIsNone(lookup.value)
        self.assertEqual(lookup.cell, "E4")
        self.assertEqual(lookup.note, "E4 katagi bo'sh")

    def test_explicit_non_date_cell_note(self):
        service, _ = self._service(_grid_response(111, [{"values": [_text_cell("Jami")]}]))
        lookup = service.lookup_worksheet_date(111, "E4")
        self.assertIsNone(lookup.value)
        self.assertEqual(lookup.note, "E4 katagidagi 'Jami' sana emas")

    def test_found_cell_note_is_empty(self):
        service, _ = self._service(_grid_response(111, [{"values": [_date_cell(46266, "01.09.2026")]}]))
        lookup = service.lookup_worksheet_date(111, "E4")
        self.assertEqual(lookup.value, date(2026, 9, 1))
        self.assertEqual((lookup.cell, lookup.display, lookup.note), ("E4", "01.09.2026", ""))

    def test_api_error_note(self):
        from googleapiclient.errors import HttpError

        resp = MagicMock(status=403, reason="forbidden")
        service, _ = self._service(side_effect=HttpError(resp, b"{}"))
        lookup = service.lookup_worksheet_date(111, "E4")
        self.assertIsNone(lookup.value)
        self.assertIn("HTTP 403", lookup.note)


class ReportServiceEndToEndTests(unittest.TestCase):
    """/report and the scheduler both go through ReportService: the file
    handed to Telegram must carry the worksheet's date, not today's."""

    def _run(self, lookups):
        import asyncio
        from app.config import WorksheetConfig
        from app.pdf_service import PDFService
        from app.report_service import ReportService

        sheets = MagicMock()
        sheets.lookup_worksheet_date.side_effect = lambda sheet_id, cell: lookups[sheet_id]
        sheets.export_worksheet_pdf.return_value = b"%PDF-1.4 fake"
        cfg = _make_config()
        cfg.telegram_channel_id = -100
        cfg.worksheets = (
            WorksheetConfig(sheet_id=111, slug="savdo", label="Savdo", date_cell="E4"),
            WorksheetConfig(sheet_id=222, slug="qoldiq", label="Qoldiq", date_cell="B1"),
        )
        pdf_service = PDFService(sheets, cfg)
        pdf_service._tmp_dir = Path(tempfile.mkdtemp())
        service = ReportService(pdf_service, cfg)

        bot = MagicMock()
        sent = []

        async def send_document(**kwargs):
            sent.append(kwargs)

        bot.send_document = send_document
        result = asyncio.run(service.generate_and_send_reports(bot, triggered_by="admin:1"))
        return result, sent, service

    def test_report_command_sends_files_named_by_sheet_dates(self):
        result, sent, service = self._run({
            111: WorksheetDate(date(2026, 9, 1), "E4", "01.09.2026"),
            222: WorksheetDate(date(2026, 9, 2), "B1", "02.09.2026"),
        })
        self.assertTrue(result.overall_success)
        self.assertEqual([s["filename"] for s in sent], ["savdo_2026-09-01.pdf", "qoldiq_2026-09-02.pdf"])
        self.assertEqual([s["chat_id"] for s in sent], [-100, -100])
        self.assertTrue(all(wr.date_from_sheet for wr in result.worksheet_results))
        self.assertEqual(service.last_run.worksheet_results[0].filename, "savdo_2026-09-01.pdf")

    def test_fallback_is_flagged_but_still_delivered(self):
        from app.bot import _date_fallback_warnings, _describe_worksheet_result

        result, sent, _ = self._run({
            111: WorksheetDate(date(2026, 9, 1), "E4", "01.09.2026"),
            222: WorksheetDate(None, "B1", "", "B1 katagi bo'sh"),
        })
        today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
        self.assertTrue(result.overall_success)
        self.assertEqual(sent[1]["filename"], f"qoldiq_{today}.pdf")
        warnings = _date_fallback_warnings(result)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Qoldiq", warnings[0])
        self.assertIn("B1 katagi bo'sh", warnings[0])
        self.assertIn("E4 katagidan", _describe_worksheet_result(result.worksheet_results[0]))
        self.assertIn("bugungi sana", _describe_worksheet_result(result.worksheet_results[1]))


class PDFServiceFilenameTests(unittest.TestCase):
    def _pdf_service(self, lookup):
        from app.pdf_service import PDFService

        sheets = MagicMock()
        sheets.lookup_worksheet_date.return_value = lookup
        sheets.export_worksheet_pdf.return_value = b"%PDF-1.4 fake"
        cfg = _make_config()
        service = PDFService(sheets, cfg)
        service._tmp_dir = Path(tempfile.mkdtemp())
        return service, sheets

    def test_filename_uses_sheet_date_not_today(self):
        service, sheets = self._pdf_service(WorksheetDate(date(2026, 9, 1), "E4", "01.09.2026"))
        generated = service.generate_worksheet_pdf(111, "savdo", "E4")
        try:
            self.assertEqual(generated.path.name, "savdo_2026-09-01.pdf")
            self.assertEqual(generated.path.read_bytes(), b"%PDF-1.4 fake")
            self.assertTrue(generated.date_from_sheet)
            self.assertEqual(generated.date_cell, "E4")
            self.assertEqual(generated.date_note, "")
            sheets.lookup_worksheet_date.assert_called_once_with(111, "E4")
        finally:
            service.cleanup(generated.path)

    def test_filename_falls_back_to_today(self):
        service, sheets = self._pdf_service(WorksheetDate(None, "B1", "", "B1 katagi bo'sh"))
        today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
        generated = service.generate_worksheet_pdf(222, "qoldiq", "B1")
        try:
            self.assertEqual(generated.path.name, f"qoldiq_{today}.pdf")
            self.assertFalse(generated.date_from_sheet)
            self.assertEqual(generated.date_note, "B1 katagi bo'sh")
            sheets.lookup_worksheet_date.assert_called_once_with(222, "B1")
        finally:
            service.cleanup(generated.path)


class _ConfigTestBase(unittest.TestCase):
    BASE_ENV = {
        "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHANNEL_ID": "-100", "ADMIN1_ID": "1",
        "GOOGLE_SHEET_URL": "https://docs.google.com/spreadsheets/d/abc/edit",
        "WORKSHEET_1_ID": "1", "WORKSHEET_2_ID": "2",
    }

    def _load(self, extra, drop=()):
        from app.config import load_config

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
            key_path = f.name
        env = {**self.BASE_ENV, "GOOGLE_SERVICE_ACCOUNT_FILE": key_path, **extra}
        for name in drop:
            env.pop(name, None)
        try:
            with patch.dict(os.environ, env, clear=True):
                return load_config(env_file="/nonexistent/.env")
        finally:
            os.unlink(key_path)


class ConfigAdminTests(_ConfigTestBase):
    def test_numbered_admins_with_gaps(self):
        cfg = self._load({"ADMIN1_ID": "111", "ADMIN2_ID": " 222 ", "ADMIN7_ID": "777"})
        self.assertEqual(cfg.admin_telegram_ids, frozenset({111, 222, 777}))

    def test_legacy_single_variable_still_works(self):
        cfg = self._load({"ADMIN_TELEGRAM_ID": "555"}, drop=("ADMIN1_ID",))
        self.assertEqual(cfg.admin_telegram_ids, frozenset({555}))

    def test_legacy_and_numbered_are_merged_and_deduplicated(self):
        cfg = self._load({"ADMIN_TELEGRAM_ID": "555", "ADMIN1_ID": "555", "ADMIN2_ID": "666"})
        self.assertEqual(cfg.admin_telegram_ids, frozenset({555, 666}))

    def test_blank_numbered_entries_are_ignored(self):
        cfg = self._load({"ADMIN1_ID": "111", "ADMIN2_ID": "   "})
        self.assertEqual(cfg.admin_telegram_ids, frozenset({111}))

    def test_no_admin_is_a_config_error(self):
        from app.exceptions import ConfigError

        with self.assertRaises(ConfigError) as ctx:
            self._load({"ADMIN1_ID": ""})
        self.assertIn("ADMIN1_ID", str(ctx.exception))

    def test_non_numeric_admin_names_the_variable(self):
        from app.exceptions import ConfigError

        with self.assertRaises(ConfigError) as ctx:
            self._load({"ADMIN2_ID": "123,456"})
        self.assertIn("ADMIN2_ID", str(ctx.exception))

    def test_is_admin_accepts_every_configured_admin(self):
        from app.authorization import is_admin

        cfg = self._load({"ADMIN1_ID": "111", "ADMIN2_ID": "222"})
        self.assertTrue(is_admin(111, cfg))
        self.assertTrue(is_admin(222, cfg))
        self.assertFalse(is_admin(333, cfg))


class ConfigDateCellTests(_ConfigTestBase):

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
