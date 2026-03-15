FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG OCR_TESSERACT_LANG_PACKS="eng deu pol"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
    && for lang in ${OCR_TESSERACT_LANG_PACKS}; do \
        [ -n "${lang}" ] || continue; \
        apt-get install -y --no-install-recommends "tesseract-ocr-${lang}"; \
    done \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
