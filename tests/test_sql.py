import pytest

from hud.gcx import validate_read_only_clickhouse_sql
from hud.sql import SqlParamError, parse_params, render_sql, with_settings


def test_render_sql_uses_defaults_and_overrides() -> None:
    sql = "-- @param days=7\n-- @param owner=nn\nSELECT {owner:String}, {days:UInt32}"

    assert render_sql(sql, {"days": "1"}).endswith("SELECT 'nn', 1")


def test_render_sql_allows_empty_default() -> None:
    sql = "-- @param owner=\n-- @param days=3\nSELECT {owner:String}, {days:UInt32}"

    assert render_sql(sql, {}).endswith("SELECT '', 3")


def test_render_sql_escapes_strings() -> None:
    assert render_sql("SELECT {name:String}", {"name": "a'b\\"}) == "SELECT 'a\\'b\\\\'"


@pytest.mark.parametrize(
    ("sql", "params", "message"),
    [
        ("SELECT {owner:String}", {}, "missing SQL parameters: owner"),
        ("SELECT 1", {"owner": "nn"}, "no placeholder for: owner"),
        ("SELECT {days:UInt32}", {"days": "1; DROP"}, "must be UInt32"),
        ("SELECT {day:Date}", {"day": "yesterday"}, "must be Date"),
        ("SELECT {x:Array}", {"x": "1"}, "unsupported parameter type"),
    ],
)
def test_render_sql_rejects_bad_params(sql: str, params: dict[str, str], message: str) -> None:
    with pytest.raises(SqlParamError, match=message):
        render_sql(sql, params)


def test_render_sql_ignores_placeholders_in_literals_and_comments() -> None:
    sql = "-- window {days:UInt32}\nSELECT '{days:UInt32}', {days:UInt32}"

    assert render_sql(sql, {"days": "3"}) == "-- window {days:UInt32}\nSELECT '{days:UInt32}', 3"


@pytest.mark.parametrize(
    ("sql", "params", "message"),
    [
        ("SELECT {ids:Array(String)}", {"ids": "a"}, "unsupported parameter type Array\\(String\\)"),
        ("SELECT {n:UInt32}", {"n": "-1"}, "must be UInt32"),
    ],
)
def test_render_sql_rejects_unsupported_or_out_of_range(sql: str, params: dict[str, str], message: str) -> None:
    with pytest.raises(SqlParamError, match=message):
        render_sql(sql, params)


def test_parse_params_rejects_repeated_names() -> None:
    with pytest.raises(SqlParamError, match="more than once"):
        parse_params(["owner=nn", "owner=optim"])


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1;\n", "SELECT 1\nSETTINGS max_execution_time = 5"),
        ("SELECT 1 -- note", "SELECT 1\nSETTINGS max_execution_time = 5"),
        ("SELECT 1;\n-- end", "SELECT 1\nSETTINGS max_execution_time = 5"),
        ("SELECT format('{}', 1)", "SELECT format('{}', 1)\nSETTINGS max_execution_time = 5"),
        ("SELECT * FROM (SELECT 1 SETTINGS max_threads = 1)", "SELECT * FROM (SELECT 1 SETTINGS max_threads = 1)\nSETTINGS max_execution_time = 5"),
        ("SELECT 1 SETTINGS max_threads = 1", "SELECT 1 SETTINGS max_threads = 1"),
        ("SELECT 1 FORMAT JSON", "SELECT 1 FORMAT JSON"),
        ("SHOW TABLES", "SHOW TABLES"),
    ],
)
def test_with_settings_appends_only_when_safe(sql: str, expected: str) -> None:
    assert with_settings(sql, {"max_execution_time": 5}) == expected


@pytest.mark.parametrize("sql", ["SELECT 1;\n-- end", "SELECT 1; /* c */", "SELECT 1 -- c"])
def test_settings_output_stays_single_statement(sql: str) -> None:
    validate_read_only_clickhouse_sql(with_settings(sql, {"max_execution_time": 5}))
