FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/freaky.db
RUN mkdir -p /data
CMD ["python", "bot.py"]
