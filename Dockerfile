FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py bridge.py server.py write_tags.py ./
COPY static/ ./static/
COPY docs/ ./docs/

EXPOSE 5555

CMD ["python3", "server.py"]
