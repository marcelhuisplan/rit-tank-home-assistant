FROM python:3.14-alpine
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
WORKDIR /app
COPY app.py /app/app.py
COPY run.sh /app/run.sh
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-nld tesseract-ocr-data-eng
RUN pip install --no-cache-dir websocket-client==1.8.0 cryptography google-api-python-client google-auth
RUN chmod a+x /app/run.sh
CMD ["/app/run.sh"]
