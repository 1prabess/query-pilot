import json
import re
from typing import Any

import sqlglot

_SQL_FIELD = re.compile(r'"sql"\s*:\s*"(?P<value>(?:[^"\\]|\\.)*)"')


def parse_json_response(text: str) -> Any:
    """Pull a JSON document out of a model reply.

    Three candidates, and the order is the design: the whole reply first, then
    the outermost object, then each fenced block. Fenced blocks go **last**
    because a model asked for JSON will sometimes include a fenced SQL example
    in the `explanation` field, and naive fence-extraction returns that example
    instead of the payload.
    """
    if not text:
        return None
    candidates: list[str] = [text.strip()]

    # The outermost object is tried before fenced blocks, because a fenced SQL
    # example inside the payload would otherwise be mistaken for the answer.
    stripped = text.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end > start:
            candidates.append(stripped[start: end + 1])

    for block in re.findall(r"```[a-zA-Z0-9_+.-]*\s*\n?(.*?)```", stripped, re.DOTALL):
        candidates.append(block.strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _strip_fence(value: Any) -> str:
    """Unwrap a code block the model fenced inside a JSON string.

    `parse_json_response` above is tolerant of a fence *around* the payload.
    What it cannot see is a fence *inside* a string value, because by then the
    JSON has parsed successfully and the fence characters are just characters:

        {"sql": "SELECT 1", "orm_prisma": "```prisma\\nmodel Student {}\\n```"}

    That is not a hypothetical. Code is exactly the kind of answer that invites
    a markdown fence, because that is how code is written everywhere else, and
    the fence characters are not TypeScript -- a snippet copied into a `.ts`
    file fails on line one, in a way that reads like a translation bug rather
    than a formatting one.

    Returns "" for anything that is not a non-empty string, so a missing or null
    key is a tab the frontend renders as unavailable -- not a crash, and not a
    guess at what the model probably meant.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    fenced = re.fullmatch(
        r"```(?:[a-zA-Z0-9_+.-]*)[ \t]*\n?(.*?)```", text, re.DOTALL)
    return fenced.group(1).strip() if fenced else text


def recover_sql(text: str) -> str | None:
    """Pull the SQL out of a reply whose JSON was cut off mid-object.

    Verified against a payload truncated mid-ORM-block:
        json.loads(cut) -> JSONDecodeError: Unterminated string
        recover_sql(cut) -> "SELECT COUNT(*) FROM enrollments"

    Returns None rather than a guess. The reply is truncated at an arbitrary
    byte, so the value may be half a statement -- `SELECT * FROM stud` -- and a
    guess that reaches the validator becomes a 422 the user sees as "your
    question was refused", which is a worse message than "the model ran out of
    room, ask again".

    A doubled-backslash inside the string is escaped, hence `\\.` in the class;
    `"(?:[^"\\]|\\.)*"` is what makes this stop at the *closing* quote rather
    than the first one it meets inside an escaped pair.

    sqlglot is the arbiter, not the regex: the regex finds the field, and
    `parse_one` decides whether what it found is a statement. A field that does
    not parse is a truncated field.

    This is a recovery path, not a parser. It runs only after json.loads has
    already failed, and the recovered string still goes through the validator in
    chapter 06 before anything runs it.
    """
    match = _SQL_FIELD.search(text)
    if match is None:
        return None
    try:
        sql = json.loads(f'"{match.group("value")}"')
    except json.JSONDecodeError:
        return None
    try:
        sqlglot.parse_one(sql, dialect="postgres")
    except sqlglot.ParseError:
        return None
    return sql
