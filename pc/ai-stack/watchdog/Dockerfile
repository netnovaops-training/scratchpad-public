# The watchdog. Deliberately tiny.
#
# NO DEPENDENCIES AT ALL -- watch.py uses only the standard library. A monitor
# that can be broken by a dependency update is a monitor that will be broken
# on the day it was needed, and this one exists precisely because the last
# outage went unnoticed.
FROM python:3.15-alpine@sha256:d44b1254cb92329418d164b22b20d86bbd3869d48e80e09e476531a409a728bb

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY watch.py /app/watch.py

# Not root. It opens outbound TCP connections and nothing else.
RUN adduser -D -H watcher
USER watcher

CMD ["python", "/app/watch.py"]
