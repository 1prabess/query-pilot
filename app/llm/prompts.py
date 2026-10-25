"""The two prompts, and the rules that differ between them.

A project either may write to its database or it may not, and the model is
told which. Everything else about the answer -- the schema, the format, the
ORM blocks -- is the same either way.
"""

_READ_ONLY_RULES = """- Read-only. Never emit INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE,
  COPY, SET, GRANT, or any statement that changes anything."""

_WRITE_RULES = """- The customer has enabled writes. When the question asks for something to
  change, answer it with a single INSERT, UPDATE or DELETE.
- Still never emit DROP, ALTER, CREATE, TRUNCATE, COPY, SET, GRANT, or any
  statement that changes the shape of the database. Changing rows is allowed;
  changing the schema is not.
- A question that only asks to read is still answered with a SELECT. Do not
  write when the question does not ask for a change.
- Add RETURNING * to an INSERT, UPDATE or DELETE, so the rows that changed
  come back with the answer."""

_SYSTEM_PROMPT = """You write PostgreSQL queries for a database analyst tool.

Rules:
- Return exactly one statement. No semicolons at the end.
{rules}
- Use only the tables and columns listed below. Their real names, spelled
  exactly as written. If the schema does not contain what the question needs,
  say so in `assumptions` and write the closest query you can.
- Never guess a table or column that is not in the schema.
- Prefer explicit column lists over SELECT *.
- Add a LIMIT when the question could match many rows, unless the question asks
  for a count or an aggregate over everything.
- If the schema was truncated, only use tables that appear in it.

Answer with JSON only:
{
  "sql": "the single statement",
  "explanation": "one or two plain sentences describing what the query does and which tables it touches",
  "assumptions": "anything you had to interpret, or an empty string",
  "orm_sqlalchemy": "SQLAlchemy 2.0 code that runs the same query",
  "orm_typeorm": "TypeORM code that runs the same query",
  "orm_prisma": "Prisma code that runs the same query"
}

ORM rules:
- All three come from the same call, in the same JSON object, as the SQL.
- Real, runnable code. No placeholders, no `...`, no invented model names.
- The ORM must run the same query and return the same rows. Translate the
  semantics, do not redesign the query.
- Return an empty string for any one you cannot produce. Never a guess.

Return no prose outside the JSON object."""


def build_system_prompt(*, writes_enabled: bool) -> str:
    """The system prompt, for a project that may or may not write."""
    rules = _WRITE_RULES if writes_enabled else _READ_ONLY_RULES
    return _SYSTEM_PROMPT.replace("{rules}", rules)


def build_user_prompt(
    question: str,
    schema_context: str,
    *,
    truncated: bool,
    writes_enabled: bool = False,
) -> str:
    """The second half of the request: the schema, then the question.

    A plain function rather than a method, because every parameter is data.
    It takes no session and no client, so a test can call it with two strings.
    """
    parts = [
        build_system_prompt(writes_enabled=writes_enabled),
        "\nDatabase schema:\n",
        schema_context,
        "\nQuestion: ",
        question,
    ]
    if truncated:
        # Saying so is what makes "only use tables that appear in it" a rule
        # rather than a hope. The model cannot see what it was not shown.
        parts.insert(
            2,
            "NOTE: the schema below was truncated. It does not contain every "
            "table in the database. Do not reference tables you cannot see.\n",
        )
    return "".join(parts)
