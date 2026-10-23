# QueryPilot [ongoing project]

**Turn natural language into secure, executable SQL.**

QueryPilot is a multi-tenant PostgreSQL SaaS platform that lets users interact with their databases using plain English. Ask a question, get a generated SQL query, execute the query, get the results, and export the same query into **SQLAlchemy, TypeORM, or Prisma**.

## Who Is This For?

- **Data Analysts** who want to retrieve insights using natural language instead of manually crafting SQL.
- **Developers** who want to explore databases and generate SQL without writing every query from scratch.
- **Backend Engineers** who work with PostgreSQL and want to translate business requirements into executable queries and ORM code.
- **Teams Building AI-Powered Applications** that need a foundation for schema-aware query generation and controlled database execution.
- **Learners and Experimenters** interested in LLM integration, SQL validation, database security, and multi-tenant backend architecture.

## How It Works

Each user creates a project and connects their own PostgreSQL database. QueryPilot introspects the database schema, provides the relevant context to an LLM, validates the generated SQL, executes it, and writes under strict permission constraints.

- **Natural Language to SQL:** Translate plain-English questions into PostgreSQL queries.
- **Schema-Aware Generation:** Generate queries using the structure of the connected database.
- **SQL Validation:** Validate generated queries before execution.
- **Read-Only Execution:** Run queries with restricted database permissions, row limits, and execution timeouts.
- **Multi-Tenant Architecture:** Isolate projects and manage database connections across users.
- **ORM Code Generation:** Get equivalent query implementations for SQLAlchemy, TypeORM, and Prisma.

## Getting Started

Start the PostgreSQL service:

```bash
docker compose up -d
```

The database runs on port `55432`.

Set up and start the backend:

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Verify the API

```bash
curl -s http://127.0.0.1:8000/api/v1/healthz
```

Expected response:

```json
{
  "status": "ok"
}
```

## API Documentation

Explore endpoints and test requests interactively through the Swagger UI:

**http://127.0.0.1:8000/docs**

## Run Tests

```bash
cd backend
uv run pytest -q
```
