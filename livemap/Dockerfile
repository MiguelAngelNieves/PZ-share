FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py pzcore.py ./
COPY static ./static
ENV PZMAP_DATA=/data
EXPOSE 8095
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8095", "--proxy-headers"]
