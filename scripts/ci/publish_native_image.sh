#!/usr/bin/env bash
# Build and push the memchentic application image for one native arch (no QEMU).
# Required env: ARCH, PLATFORM, VERSION, REGISTRY, GHCR_NAMESPACE (or
# GITHUB_REPOSITORY_OWNER), PACKAGE_NAME, GITHUB_SHA, GH_TOKEN, GH_ACTOR,
# SOURCE_URL.
#
# Optional: PUBLISH_BUILD_ATTEMPTS (default 3) retries BuildKit EOF / rpc flakes.
set -euo pipefail

printf '%s' "${GH_TOKEN}" | docker login ghcr.io --username "${GH_ACTOR}" --password-stdin

owner="$(printf '%s' "${GHCR_NAMESPACE:-${GITHUB_REPOSITORY_OWNER}}" | tr '[:upper:]' '[:lower:]')"
package="${PACKAGE_NAME:-memchentic}"
image_base="${REGISTRY}/${owner}/${package}"

if [ -z "${VERSION:-}" ] || [ "${VERSION}" = "null" ]; then
  echo "Missing VERSION" >&2
  exit 1
fi
if [ -z "${ARCH:-}" ] || [ -z "${PLATFORM:-}" ]; then
  echo "Missing ARCH/PLATFORM" >&2
  exit 1
fi

arch_tag="${image_base}:${VERSION}-${ARCH}"

label_args=(
  --label "org.opencontainers.image.source=${SOURCE_URL}"
  --label "org.opencontainers.image.revision=${GITHUB_SHA}"
  --label "org.opencontainers.image.title=${package}"
  --label "org.opencontainers.image.version=${VERSION}"
)

publish_args=(
  --platform "${PLATFORM}"
  --provenance=mode=max
  --sbom=true
  --push
  "${label_args[@]}"
  --tag "${arch_tag}"
)

# Retry BuildKit drops (rpc Unavailable / EOF) after a short backoff.
buildx_build() {
  local attempts="${PUBLISH_BUILD_ATTEMPTS:-3}"
  local attempt=1
  local logfile status
  logfile="$(mktemp)"
  while true; do
    set +e
    set -o pipefail
    docker buildx build "$@" 2>&1 | tee "${logfile}"
    status=$?
    set +o pipefail
    set -e
    if [ "${status}" -eq 0 ]; then
      rm -f "${logfile}"
      return 0
    fi
    if [ "${attempt}" -ge "${attempts}" ]; then
      rm -f "${logfile}"
      return "${status}"
    fi
    if ! grep -Eqi 'error reading from server: EOF|code = Unavailable|rpc error' "${logfile}"; then
      rm -f "${logfile}"
      return "${status}"
    fi
    echo "buildx failed with BuildKit EOF/Unavailable (attempt ${attempt}/${attempts}); retrying" >&2
    attempt=$((attempt + 1))
    sleep $((attempt * 5))
    : > "${logfile}"
  done
}

echo "Pushing native ${PLATFORM} image ${arch_tag}"
buildx_build \
  "${publish_args[@]}" \
  --file docker/Dockerfile \
  .
