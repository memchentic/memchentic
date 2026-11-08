# Build the Sphinx site, then serve static HTML with nginx.
FROM python:3.12-slim AS build

WORKDIR /opt/src

COPY docs/sphinx/requirements.txt /tmp/docs-requirements.txt
RUN pip install --no-cache-dir -r /tmp/docs-requirements.txt \
    && rm -f /tmp/docs-requirements.txt

COPY docs/sphinx/ /opt/src/docs/sphinx/
RUN python -m sphinx \
        -c /opt/src/docs/sphinx/source \
        -b html \
        /opt/src/docs/sphinx/source \
        /tmp/docs-html \
    && test -f /tmp/docs-html/index.html

FROM nginx:alpine

COPY docker/docs/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /tmp/docs-html /usr/share/nginx/html

EXPOSE 80
