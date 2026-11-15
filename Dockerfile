# Образ для запуску додатку на своєму сервері (VPS). Інструкція — docs/DEPLOY.md.
# python:3.12-slim — мінімальний Linux із Python (без зайвого, образ менший)
FROM python:3.12-slim

WORKDIR /app

# Спершу лише requirements.txt: Docker кешує цей крок, і при зміні коду пакети не перевстановлюються
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Потім увесь код і дані (що НЕ копіювати — див. .dockerignore)
COPY . .

# Streamlit усередині контейнера слухає порт 8501 на всіх адресах (0.0.0.0), щоб до нього
# достукався Caddy з тієї ж docker-мережі. Назовні порт не відкриваємо — лише через Caddy.
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
