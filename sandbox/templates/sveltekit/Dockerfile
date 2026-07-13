FROM node:${NODE_VERSION}-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git procps \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable

WORKDIR /workspace/app
EXPOSE ${PORT}
CMD ["sleep", "infinity"]
