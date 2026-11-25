FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app/backend
COPY frontend /app/frontend
COPY data /app/data

ENV PYTHONPATH=/app/backend
ENV DATABASE_URL=sqlite:////data/app.db
ENV JWT_SECRET=DEMO_ONLY_JWT_SECRET_NOT_FOR_PRODUCTION
ENV DEMO_PASSWORD=DemoOnly#2026
ENV AUTO_SEED=1

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
