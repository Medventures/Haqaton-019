# Демо-МИС: заглушка медицинской информационной системы клиники.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"
COPY mis .
ENV MIS_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8100
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
