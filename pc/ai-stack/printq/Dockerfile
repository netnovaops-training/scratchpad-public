FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# CUPS *client* only -- the server is the `cups` container. This image just
# needs to be able to say "print this" and "how are you" to it.
#   cups-client     lp, lpstat, lpadmin, cupsenable, cupsaccept
#   cups-ipp-utils  ipptool, for toner levels straight from the printer
#   snmp            snmpget, for the LIFETIME PAGE COUNTER
#
# The counter needs SNMP because this Brother does not publish it over IPP --
# it reports toner happily and simply has no page-count attribute. SNMP is a
# CLIENT here only: nothing is installed on Windows and no service is opened.
# The printer has always spoken SNMP; we just never asked it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        cups-client \
        cups-ipp-utils \
        ca-certificates \
        snmp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY printq/app.py /app/app.py

CMD ["python", "-u", "app.py"]
