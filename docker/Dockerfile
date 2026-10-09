#
# AWG GOLANG BUILDING STAGE
# Base: Alpine
#

# Pull the current golang-alpine image.
FROM golang:1.26-alpine3.23 AS awg-go
ARG AMNEZIA_GO_SHA=2ac739347721a985001d71f49fb36d6fcdebe6f9

# Install build-dependencies.
RUN apk add --no-cache \
    git \
    gcc \
    musl-dev

# Standard working directory for WGDashboard
RUN mkdir -p /workspace && \
    git clone https://github.com/WGDashboard/amneziawg-go /workspace/awg && \
    git -C /workspace/awg checkout --detach "$AMNEZIA_GO_SHA" && \
    test "$(git -C /workspace/awg rev-parse HEAD)" = "$AMNEZIA_GO_SHA"

# Enable CGO compilation for AmneziaWG
ENV CGO_ENABLED=1

# Change directory
WORKDIR /workspace/awg
# Compile the binaries
RUN go version && \
    go mod download && \
    go mod verify && \
    go build -ldflags '-linkmode external -extldflags "-fno-PIC -static"' -v -o /usr/bin
#
# AWG TOOLS BUILDING STAGE
# Base: Alpine
#
FROM alpine:3.23 AS awg-tools
ARG AMNEZIA_TOOLS_SHA=5d6179a6d0842e98dfb349c28cf1bd8e4b9d1079

# Install needed dependencies.
RUN apk add --no-cache \
    make \
    git \
    build-base \
    linux-headers \
    ca-certificates

# Get the workspace ready
RUN mkdir -p /workspace && \
    git clone https://github.com/WGDashboard/amneziawg-tools /workspace/awg-tools && \
    git -C /workspace/awg-tools checkout --detach "$AMNEZIA_TOOLS_SHA" && \
    test "$(git -C /workspace/awg-tools rev-parse HEAD)" = "$AMNEZIA_TOOLS_SHA"

# Change directory
WORKDIR /workspace/awg-tools/src
# Compile and change permissions
RUN make && chmod +x wg*

#
# ADMIN UI: build Vue sources into the shipped runtime rather than using
# committed (and possibly stale) dist artifacts.
#
FROM node:22-alpine AS admin-ui
WORKDIR /workspace/app
COPY ./src/static/app/package*.json ./
RUN npm ci --no-audit --no-fund
COPY ./src/static/app ./
RUN npm run build

#
# PIP DEPENDENCY BUILDING
# Base: Alpine
#

# Use the python-alpine image for building pip dependencies
FROM python:3.14-alpine3.23 AS pip-builder

ARG TARGETPLATFORM

# Add the build dependencies and create a Python virtual environment.
RUN apk add --no-cache \
      build-base \
      pkgconfig \
      python3-dev \
      postgresql-dev \
      libffi-dev \
      libpq \
      linux-headers \
      rust \
      cargo \
    && mkdir -p /opt/wgdashboard/src \
    && python3 -m venv /opt/wgdashboard/src/venv

# Copy the requirements file into the build layer.
COPY ./src/requirements.txt /opt/wgdashboard/src
RUN if [ "$TARGETPLATFORM" = "linux/arm/v7" ]; then \
      sed -i 's|psycopg\[binary\]|psycopg[c]|' /opt/wgdashboard/src/requirements.txt; \
    fi; \
    cat /opt/wgdashboard/src/requirements.txt

# Install the pip packages
RUN . /opt/wgdashboard/src/venv/bin/activate && \
    pip3 install --upgrade pip && \
    pip3 install -r /opt/wgdashboard/src/requirements.txt

#
# WGDashboard RUNNING STAGE
# Base: Alpine
#

# Running with the python-alpine image.
FROM python:3.14-alpine3.23 AS final
LABEL maintainer="dselen@nerthus.nl"

# Install only the runtime dependencies
RUN apk add --no-cache \
    iproute2 iptables \
    bash curl procps openrc \
    tzdata wireguard-tools envsubst
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy only the final binaries from the AWG builder stages
COPY --from=awg-go /usr/bin/amneziawg-go /usr/bin/amneziawg-go
COPY --from=awg-tools /workspace/awg-tools/src/wg /usr/bin/awg
COPY --from=awg-tools /workspace/awg-tools/src/wg-quick/linux.bash /usr/bin/awg-quick

# Environment variables
ARG wg_net="10.0.0.1"
ARG wg_subn="24"
ARG wg_port="51820"
ENV TZ="Europe/Amsterdam" \
    global_dns="9.9.9.9" \
    wgd_port="10086" \
    public_ip="" \
    WGDASH=/opt/wgdashboard \
    dynamic_config="true"

# Create directories needed for operation
RUN mkdir /data /configs -p ${WGDASH}/src /etc/amnezia/amneziawg \
    && echo "name_servers=${global_dns}" >> /etc/resolvconf.conf

# Copy the venv and source files from local compiled locations or repos
COPY ./src ${WGDASH}/src
COPY --from=admin-ui /workspace/dist/WGDashboardAdmin/ ${WGDASH}/src/static/dist/WGDashboardAdmin/
COPY --from=pip-builder /opt/wgdashboard/src/venv /opt/wgdashboard/src/venv
COPY ./docker/wg0.conf.template /tmp/wg0.conf.template
COPY ./docker/wg-dashboard-oidc-providers.json.template /tmp/wg-dashboard-oidc-providers.json.template
# Copy in the runtime script, essential.
COPY ./docker/entrypoint.sh /entrypoint.sh

# First WireGuard interface template
RUN export out_adapt=$(ip -o -4 route show to default | awk '{print $NF}') \
    && envsubst < /tmp/wg0.conf.template > /configs/wg0.conf.template \
    && chmod 600 /configs/wg0.conf.template \
    && cat /configs/wg0.conf.template

# HTTP healthcheck: process presence is not panel health.
# Does not check the database; Compose also defines its own HTTP probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD sh -c 'curl -fsS --max-time 5 "http://127.0.0.1:${wgd_port:-10086}/api/handshake" >/dev/null' || exit 1

# Expose ports on the container
EXPOSE 10086
WORKDIR $WGDASH/src

ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
