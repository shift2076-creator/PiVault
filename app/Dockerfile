FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIVAULT_DATA=/data PIVAULT_NOTIFICATION_ROOT=/notifications
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
RUN chmod -R a+rX /app
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD ["python", "/app/healthcheck.py"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--access-logfile", "/dev/null", "--error-logfile", "-", "wsgi:application"]
