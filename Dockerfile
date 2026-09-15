FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOCAB_DB_PATH=/data/vocab.db

WORKDIR /app

RUN groupadd --system wordbox \
    && useradd --system --gid wordbox --home-dir /app wordbox \
    && mkdir /data \
    && chown wordbox:wordbox /data

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "python-dotenv==1.2.3" "python-telegram-bot==22.8"

COPY bot.py ./

USER wordbox

CMD ["python", "bot.py"]
