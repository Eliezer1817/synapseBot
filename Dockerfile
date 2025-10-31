# Usa una imagen base de Python
FROM python:3.9-slim

# Establece el directorio de trabajo en /app
WORKDIR /app

# Copia los archivos del subdirectorio Dashboard al directorio de trabajo del contenedor
COPY ./Dashboard/ .

# Instala las dependencias de Python
RUN pip install --no-cache-dir iqoptionapi numpy pandas lightgbm ta

# Expone el puerto en el que se ejecuta la aplicación
EXPOSE 8000

# El comando para ejecutar la aplicación
CMD ["python", "server.py"]
