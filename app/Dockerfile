FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates/ templates/
RUN useradd -r -u 1000 appuser
USER appuser
EXPOSE 8080
CMD ["python", "app.py"]
