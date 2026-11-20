# Tickets server. Same approach as the household's other Rust services: the toolchain lives in
# the build stage and is thrown away; the image is one binary on Debian slim.
ARG RUST_VERSION=1.99

FROM rust:${RUST_VERSION}-slim-trixie@sha256:1a939748a7cbab172de91a014e3804a97af1394a8c844faa67fce9401c4e40fb AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY static ./static
COPY apps-script ./apps-script
RUN cargo build --release --locked

FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
# ca-certificates: the server calls GitHub and Google over HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10002 --home-dir /data tickets && mkdir -p /data && chown tickets /data
COPY --from=builder /build/target/release/tickets-server /usr/local/bin/tickets-server
USER tickets
ENV DATA_DIR=/data PORT=8050 BIND=0.0.0.0
EXPOSE 8050
# tickets and the Google sign-in live in a volume mounted here, so a new image never loses them
VOLUME ["/data"]
CMD ["tickets-server"]
