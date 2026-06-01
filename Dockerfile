FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

CMD ["uv", "run", "python", "-m", "toop"]
