# Two stages so pip, wheels, and build metadata never reach the image that
# holds the notes. The runtime stage has an interpreter, the venv, and nothing
# else - no git, no compiler, no package manager cache.
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src src
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir .

FROM python:3.13-slim
# Numeric USER below, but the account still has to exist: fastmcp and uvicorn
# call getpwuid() on startup and a missing passwd entry aborts the process.
RUN adduser --disabled-password --gecos "" --uid 1000 app
COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Numeric, so the Deployment's runAsNonRoot check can verify it without
# resolving a name.
USER 1000
EXPOSE 8080
CMD ["obsidian-mcp-serve"]
