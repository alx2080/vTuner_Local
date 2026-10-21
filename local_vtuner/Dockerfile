ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3 ca-certificates
COPY server.py /app/server.py
CMD ["python3", "-u", "/app/server.py"]
