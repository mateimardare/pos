FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tools ./tools
RUN useradd -r -u 10001 svc && mkdir -p /data/scripts && chown -R svc /data
USER svc
ENV SCRIPTS_DIR=/data/scripts
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
