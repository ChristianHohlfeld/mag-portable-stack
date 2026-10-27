# Sourced by bootstrap.sh, update.sh and deploy.sh: the native targets a
# release needs. deploy.sh checks every one of them, so a fresh build/ must
# build exactly this list (mag-det was missing before and broke bootstrap).
# shellcheck disable=SC2034
MAG_BUILD_TARGETS=(magroom mag-audit-gateway mag-browser magbot-native mag-bots-publish mag-det mag-numa-swap mag-ci-bridge mag-ci-guard)
MAG_RELEASE_BINARIES=(magroom mag-browser magbot-native mag-bots-publish mag-det)
