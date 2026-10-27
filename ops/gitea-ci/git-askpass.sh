#!/usr/bin/env bash
# Used only by the trusted bridge, never in the worker VM.
set +x
set -euo pipefail
case ${1:-} in
  *Username*github.com*) printf '%s\n' x-access-token ;;
  *Password*github.com*) cat "${CREDENTIALS_DIRECTORY:?}/github" ;;
  *Username*127.0.0.1*) printf '%s\n' mag-ci ;;
  *Password*127.0.0.1*) cat "${CREDENTIALS_DIRECTORY:?}/gitea" ;;
  *) exit 1 ;;
esac
