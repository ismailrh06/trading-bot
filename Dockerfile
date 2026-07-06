# Bot de trading — image pour Railway, Fly.io, ou tout VPS avec Docker.
#
# Les secrets (clés API, Telegram…) se passent en variables d'environnement
# de la plateforme — JAMAIS copiés dans l'image.
#
# Persistance : monter un volume sur /app/state (positions, base SQLite)
# et idéalement /app/logs et /app/data.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# le bot lit .env s'il existe, sinon les variables d'environnement système
CMD ["python", "main.py", "run"]
