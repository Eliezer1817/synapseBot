FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y git libgomp1

# Instala git para permitir instalar desde repositorios
RUN apt-get update && apt-get install -y git

COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

EXPOSE 8000

CMD ["python", "server.py"]
