FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080
ENV PORT=8080

CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "60"]
