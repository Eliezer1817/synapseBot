FROM python:3.10-slim
WORKDIR /app

RUN sed -i 's|http:|https:|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1

COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

EXPOSE 8080

CMD ["python", "server.py"]
