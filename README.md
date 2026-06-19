# Auto Reign

Auto Reign v1 is scoped to local infrastructure for a backend service, frontend service, and persistent local data volume. This repository task only defines the repository-level setup files needed before application code is added.

## Local Setup

Create a local environment file from the checked-in example:

```sh
cp .env.example .env
```

Validate the Docker Compose configuration:

```sh
docker compose config
```

Start the local stack:

```sh
docker compose up --build
```

The backend service is exposed on `BACKEND_PORT`, defaulting to `8000`. The frontend service is exposed on `FRONTEND_PORT`, defaulting to `3000`.

## Environment Variables

`BACKEND_HOST` controls the backend bind host inside the container.
`BACKEND_PORT` controls the host port mapped to backend port `8000`.
`FRONTEND_PORT` controls the host port mapped to frontend port `3000`.
`DATA_DIR` is the container data directory.
`SQLITE_PATH` is the SQLite database path inside the container.
`CHROMA_DIR` is the Chroma persistence directory inside the container.
`DEFAULT_COLLECTION` names the default collection.
`EMBEDDING_PROVIDER` selects the embedding provider.
`EMBEDDING_MODEL` selects the embedding model.
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, and `QWEN_API_KEY` provide model provider credentials.
`OPENAI_CHAT_MODELS`, `DEEPSEEK_CHAT_MODELS`, and `QWEN_CHAT_MODELS` list available chat models by provider.
`NEXT_PUBLIC_API_BASE_URL` configures the frontend API base URL.

## V1 Scope

The v1 repository infrastructure defines the baseline local developer workflow and environment contract. Backend and frontend application scaffolding, business logic, tests, and production deployment configuration are outside this task.
