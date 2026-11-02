from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z0-9_]+(?:\([^{}]*\))?)\}")
DEFAULT_RE = re.compile(r"^[ \t]*--[ \t]*@param[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*?)[ \t]*$", re.MULTILINE)
INTEGER_TYPES = {f"{prefix}{bits}" for prefix in ("Int", "UInt") for bits in (8, 16, 32, 64)}
FLOAT_TYPES = {"Float32", "Float64"}


class SqlParamError(ValueError):
    pass


@dataclass(frozen=True)
class SqlToken:
    """A code token outside string literals and comments."""

    text: str
    start: int
    end: int
    depth: int

    @property
    def is_word(self) -> bool:
        return bool(WORD_RE.fullmatch(self.text))


def lex_sql(sql: str) -> list[SqlToken]:
    """Split SQL into words and single-character symbols, skipping quotes and comments."""
    tokens = []
    depth = 0
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if char in {"'", '"', "`"}:
            index = skip_quoted_sql(sql, index, char)
        elif char == "-" and next_char == "-" or char == "#":
            index = skip_line_comment(sql, index)
        elif char == "/" and next_char == "*":
            index = skip_block_comment(sql, index + 2)
        elif char.isspace():
            index += 1
        elif match := WORD_RE.match(sql, index):
            tokens.append(SqlToken(match.group(0).upper(), index, match.end(), depth))
            index = match.end()
        else:
            depth -= char == ")"
            tokens.append(SqlToken(char, index, index + 1, depth))
            depth += char == "("
            index += 1
    return tokens


def skip_quoted_sql(sql: str, index: int, quote_char: str) -> int:
    index += 1
    while index < len(sql):
        if sql[index] == "\\":
            index += 2
            continue
        if sql[index] == quote_char:
            if quote_char in {"'", '"'} and index + 1 < len(sql) and sql[index + 1] == quote_char:
                index += 2
                continue
            return index + 1
        index += 1
    return index


def skip_line_comment(sql: str, index: int) -> int:
    while index < len(sql) and sql[index] not in {"\n", "\r"}:
        index += 1
    return index


def skip_block_comment(sql: str, index: int) -> int:
    while index + 1 < len(sql):
        if sql[index] == "*" and sql[index + 1] == "/":
            return index + 2
        index += 1
    return len(sql)


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_params(params: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for param in params:
        key, separator, value = param.partition("=")
        if not separator or not key:
            raise SqlParamError(f"parameter must look like name=value: {param!r}")
        if key in parsed:
            raise SqlParamError(f"parameter {key} given more than once")
        parsed[key] = value
    return parsed


def render_sql(sql: str, params: dict[str, str]) -> str:
    """Substitute ClickHouse-style {name:Type} placeholders client-side.

    Grafana's ClickHouse datasource cannot bind query parameters, so HUD renders typed
    literals itself. Placeholders inside string literals and comments are left alone.
    Defaults come from `-- @param name=value` comment lines.
    """
    matches = [
        match
        for token in lex_sql(sql)
        if token.text == "{" and (match := PARAM_RE.match(sql, token.start))
    ]
    values = dict(DEFAULT_RE.findall(sql)) | params
    placeholders = {match.group(1) for match in matches}
    if missing := sorted(placeholders - values.keys()):
        raise SqlParamError(f"missing SQL parameters: {', '.join(missing)} (pass -p name=value)")
    if unused := sorted(params.keys() - placeholders):
        raise SqlParamError(f"SQL has no placeholder for: {', '.join(unused)}")
    for match in reversed(matches):
        literal = sql_literal(match.group(1), match.group(2), values[match.group(1)])
        sql = sql[: match.start()] + literal + sql[match.end() :]
    return sql


def sql_literal(name: str, type_name: str, value: str) -> str:
    try:
        if type_name in INTEGER_TYPES:
            number = int(value)
            if type_name.startswith("U") and number < 0:
                raise ValueError(value)
            return str(number)
        if type_name in FLOAT_TYPES:
            return repr(float(value))
        if type_name == "Date":
            return sql_string(date.fromisoformat(value).isoformat())
    except ValueError as error:
        raise SqlParamError(f"parameter {name} must be {type_name}, got {value!r}") from error
    if type_name == "String":
        return sql_string(value)
    raise SqlParamError(f"unsupported parameter type {type_name} for {name}; use String, Int*/UInt*, Float*, or Date")


def with_settings(sql: str, settings: dict[str, int]) -> str:
    """Append a SETTINGS clause to a SELECT/WITH query without its own top-level SETTINGS/FORMAT.

    The clause goes right after the last code token, so trailing semicolons and comments
    cannot swallow it or turn the query into two statements.
    """
    code = [token for token in lex_sql(sql) if token.text != ";"]
    if not settings or not code or code[0].text not in {"SELECT", "WITH"}:
        return sql
    for token, following in zip(code, [*code[1:], None], strict=True):
        is_clause = token.depth == 0 and token.text in {"SETTINGS", "FORMAT"}
        if is_clause and (following is None or following.text != "("):
            return sql
    clause = ", ".join(f"{name} = {value}" for name, value in settings.items())
    return f"{sql[: code[-1].end]}\nSETTINGS {clause}"
