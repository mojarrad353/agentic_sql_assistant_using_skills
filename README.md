# Agentic SQL Assistant with Progressive Skills

![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Framework-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=black)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?logo=postgresql&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic_Workflows-orange)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?logo=docker&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--5--mini-412991?logo=openai&logoColor=white)

A production-grade, agentic SQL assistant built with **LangGraph** and **LangChain**.

**Main Idea**: This program acts as an intelligent bridge between natural language and your PostgreSQL business data. It is designed to generate accurate SQL queries for various business domains—such as **Sales Analytics**, **Inventory Management**, and more—by dynamically loading the relevant schema "skills" only when needed.

## 🚀 Key Features

- **Dual Execution Modes**: Choose between **Automatic** (instant results) or **Human-in-the-Loop** (review before execution) for safety and control.
- **Agentic Architecture**: Powered by [LangGraph](https://langchain-ai.github.io/langgraph/), enabling cyclic reasoning and state management.
- **Progressive Skill Loading**: Minimizes context usage by only loading relevant schemas (e.g., Sales, Inventory) when explicitly requested by the agent.
- **File-Based Skills System**: Add new skills by creating local folders—no code changes required.
- **Production Performance**: Implements **Database Connection Pooling** for efficient resource management.
- **Observability**: Built-in logging and integration with [LangSmith](https://smith.langchain.com/) for deep tracing.
- **Container Readiness**: Fully Dockerized for seamless deployment.

## 📂 Project Structure

```
src/sql_assistant/
├── agent.py           # LangGraph agent definition (nodes, edges)
├── api.py             # FastAPI Backend (REST API)
├── database.py        # DB Connection Pooling logic
├── config.py          # Configuration & environment variables
├── main.py            # CLI entry point
└── skills/            # Skills Repository
    ├── repository.py  # Logic to load skills from disk
    ├── sales_analytics/
    │   ├── description.txt  # Lightweight description for the agent
    │   └── content.md       # Full schema & logic
    └── ...
web-app/               # React Frontend (Vite)
├── src/
│   ├── App.jsx        # Main UI logic (Chat + Toggle)
│   └── ...
└── ...
Dockerfile.api         # Backend container definition
web-app/Dockerfile.web # Frontend container definition
docker-compose.yml     # Multi-service orchestration
```

## 🛠️ Setup

This project is managed with `uv`.

1. **Clone and Install**:
   ```bash
   git clone <repository-url>
   cd agentic_sql_assistant_using_skills
   uv sync
   ```

2. **Configure Environment**:
   Create a `.env` file in the root:
   ```ini
   OPENAI_API_KEY=sk-proj-...
   OPENAI_MODEL_NAME=gpt-5-mini  # or gpt-4-turbo, etc.

   # Optional: LangSmith Tracing
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=lsv2_...
   LANGSMITH_PROJECT=sql-assistant-skills

   # PostgreSQL
   POSTGRES_USER=
   POSTGRES_PASSWORD=
   POSTGRES_DB=business
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5432
   ```

3. **Database Initialization**:
   Start the Postgres container and seed it:
   ```bash
   docker compose up db -d
   uv run scripts/generate_data.py
   ```

## 🚀 Running the App

### Manual Execution (Development)
1. **Start Backend**: `uv run -m src.sql_assistant.api`
2. **Start Frontend**: `cd web-app && npm install && npm run dev`

### 🚢 Full Stack (Production Mode)
Run everything in containers:
```bash
docker compose up --build -d
```
*   **Web UI**: http://localhost:5173
*   **API**: http://localhost:8000

## ⚡ Execution & Persistence

- **Human-in-the-Loop**: Safe execution where you approve generated SQL.
- **Connection Pooling**: Tool execution is optimized using a built-in connection pool to prevent database fatigue.

## 🧩 Adding New Skills

You can extend the agent's knowledge without writing Python code.

1.  Create a new directory in `src/sql_assistant/skills/`:
    ```bash
    mkdir src/sql_assistant/skills/marketing_campaigns
    ```
2.  Add a `description.txt`:
    *   *Content*: "Schema for marketing campaigns, leads, and conversion metrics."
3.  Add a `content.md`:
    *   *Content*: The full DDL statements, table descriptions, and business rules (e.g., "A 'conversion' is defined as...").

The agent will automatically discover the new skill on the next restart.

## 🧪 Testing
```bash
uv run pytest
```
