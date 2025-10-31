# Usa una imagen base de Python
FROM python:3.10-slim

# Establece el directorio de trabajo en /app
WORKDIR /app

# Copia los archivos del subdirectorio Dashboard al directorio de trabajo del contenedor
COPY . .

# Instala las dependencias de Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
# Expone el puerto en el que se ejecuta la aplicación
EXPOSE 8000

# El comando para ejecutar la aplicación
CMD ["python", "server.py"]
