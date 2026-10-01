# Jenkins + Docker CLI (for optional use) + Python copied from official image (no apt on controller).
FROM python:3.12-slim AS python

FROM jenkins/jenkins:lts

USER root
COPY --from=python /usr/local /usr/local

ARG DOCKER_VERSION=27.3.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar -xzC /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker

ENV PATH=/usr/local/bin:${PATH}

USER jenkins
