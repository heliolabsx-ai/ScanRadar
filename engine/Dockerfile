FROM python:3.12-slim
ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir redis==5.2.1 psycopg[binary]==3.2.3 pandas==2.2.3 numpy==2.1.3 requests==2.32.3
COPY . .
CMD ["python", "-u", "main.py"]
