# =============================================================================
#  n8n, with the Docker CLI added.
#
#  This USED to be `apk add docker-cli curl`, and that's what broke: n8n's
#  official image went distroless in a recent release. The runtime it ships
#  now has no apk, no apt-get, no package manager of any kind -- there is
#  nothing there for `apk add` to run, so it failed with exit 127 ("command
#  not found") on the very first line.
#
#  The fix is to not need a package manager at all: COPY the docker CLI
#  binary in directly from the small official image that's nothing BUT that
#  binary, rather than asking n8n's image to install anything.
# =============================================================================
FROM docker:29.9-cli@sha256:5e6baf67325a79f64ed3bed6863954d5e05d0f3d4e57cbdb14809346814bbfdb AS dockercli

FROM n8nio/n8n:latest@sha256:d02752c6ab446ae9db8deec2f9cc887c378f77cdcad31650814df936b7386dfb

# A straight file copy, not an install step -- so it works whether or not
# THIS image has a package manager either. If a future n8n release removes
# even the shell Execute Command needs, that will surface as its own clear
# failure; this line specifically no longer depends on apk existing.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

# Root, not the image's default `node` user. Worth saying plainly: this is not
# a real weakening of anything. The moment /var/run/docker.sock is mounted in
# at all, the container can ask the Docker daemon to start a new container
# with any mount, any privilege, any user it likes -- which is root on the
# HOST regardless of what user n8n's own process happens to run as. The `node`
# user was never a meaningful boundary once that mount exists; it just usually
# also blocks the docker-cli from opening the socket, which would make every
# Execute Command step fail with "permission denied" for no real security
# benefit. Root here matches the access level already granted, honestly.
USER root
