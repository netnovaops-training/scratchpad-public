# The watchdog. Deliberately tiny.
#
# NO DEPENDENCIES AT ALL -- watch.py uses only the standard library. A monitor
# that can be broken by a dependency update is a monitor that will be broken
# on the day it was needed, and this one exists precisely because the last
# outage went unnoticed.
FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY watch.py /app/watch.py

# Not root. It opens outbound TCP connections and nothing else.
RUN adduser -D -H watcher
USER watcher

CMD ["python", "/app/watch.py"]
