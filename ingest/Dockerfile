FROM python:3.12-slim
ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir kiteconnect==5.0.1 pyotp==2.9.0 requests==2.32.3 redis==5.2.1 psycopg[binary]==3.2.3 pandas==2.2.3 schedule==1.2.2
COPY . .
CMD ["python", "-u", "main.py"]
