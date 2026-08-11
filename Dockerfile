ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SPRAYCONTROL_ADDON=1

# numpy, scipy and shapely publish musl wheels for the architectures this
# add-on targets, so no toolchain is needed at build time.
RUN apk add --no-cache tini

WORKDIR /app
COPY pyproject.toml README.md ./
COPY custom_components/spraying_control/spraycontrol ./custom_components/spraying_control/spraycontrol
RUN pip install --no-cache-dir .

COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8099
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/run.sh"]
