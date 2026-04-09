FROM python:3.12-alpine

WORKDIR /app

# Dependencias del sistema (mínimas — Alpine)
RUN apk add --no-cache gcc musl-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generator.py .

CMD ["python", "-u", "generator.py"]
