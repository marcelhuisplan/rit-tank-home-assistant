FROM python:3.14-alpine
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
WORKDIR /app
COPY app.py pdf_report.py google_places.py routing.py home_assistant.py trips.py assistant.py /app/
COPY captur-2014.png /app/captur-2014.png
COPY captur-2014.jpg /app/captur-2014.jpg
COPY huisplan-logo.jpg /app/huisplan-logo.jpg
COPY huisplan-icon-*.png /app/
COPY run.sh /app/run.sh
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-nld tesseract-ocr-data-eng
RUN pip install --no-cache-dir websocket-client==1.8.0 cryptography google-api-python-client google-auth
RUN chmod a+x /app/run.sh
CMD ["/app/run.sh"]
