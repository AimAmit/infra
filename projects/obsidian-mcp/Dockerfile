# Two stages so uv, pip, wheels, and build metadata never reach the image that
# holds the notes. The runtime stage has an interpreter, the venv, and nothing
# else - no git, no compiler, no package manager, no source tree.
#
# Dependencies come from uv.lock, which pins all 75 transitive packages by
# version and hash. `fastmcp>=2.10` in pyproject.toml means a rebuild six
# months from now silently resolves to whatever is current; --frozen means it
# resolves to exactly what was reviewed, and a tampered artifact fails the
# hash check rather than landing in a container that can read every note.
FROM ghcr.io/astral-sh/uv:0.9.17-python3.13-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src src
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/AimAmit/infra
# Numeric USER below, but the account still has to exist: fastmcp and uvicorn
# call getpwuid() on startup and a missing passwd entry aborts the process.
RUN adduser --disabled-password --gecos "" --uid 1000 app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Numeric, so the Deployment's runAsNonRoot check can verify it without
# resolving a name.
USER 1000
EXPOSE 8080
CMD ["obsidian-mcp-serve"]
