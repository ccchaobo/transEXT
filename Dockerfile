ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY deploy/requirements.lock ./requirements.lock
COPY packages ./packages
COPY services ./services
COPY adapters ./adapters
COPY benchmarks/rr_m0 ./benchmarks/rr_m0
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -c requirements.lock '.[ocr]'

FROM ${PYTHON_IMAGE} AS runtime
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|http://deb.debian.org/debian|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 fonts-noto-cjk fonts-wqy-zenhei fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 reader && useradd --uid 10001 --gid reader --no-create-home reader \
    && mkdir -p /data /opt/research-reader/deploy \
    && chown reader:reader /data
COPY --from=builder /opt/venv /opt/venv
COPY apps/web /opt/research-reader/apps/web
COPY deploy/container-health.py deploy/archive.py /opt/research-reader/deploy/
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RR_DATA_DIR=/data RR_WEB_ROOT=/opt/research-reader/apps/web RR_ENV_FILE=/dev/null \
    HOME=/tmp OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
WORKDIR /opt/research-reader
USER reader:reader
# Models are packaged inside rapidocr-onnxruntime; deployment needs no OCR download.
RUN python -c 'from rr_adapters.ocr import RapidOCRProvider; RapidOCRProvider()'
RUN python -c 'from rr_adapters.fonts import register_pdf_fonts; assert register_pdf_fonts()["cjk_available"] == "true"'
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "rr_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765", "--no-proxy-headers"]
