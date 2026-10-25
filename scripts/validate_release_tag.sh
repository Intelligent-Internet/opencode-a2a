#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 || -z "$1" ]]; then
  echo "Usage: $0 <v-prefixed-tag>" >&2
  exit 2
fi

release_tag="$1"
case "${release_tag}" in
  v*) ;;
  *)
    echo "Release tag must start with 'v': ${release_tag}" >&2
    exit 1
    ;;
esac

tag_ref="refs/tags/${release_tag}"
if ! git check-ref-format "${tag_ref}" || ! git show-ref --verify --quiet "${tag_ref}"; then
  echo "Release tag does not exist or is not a valid tag: ${release_tag}" >&2
  exit 1
fi

tag_commit="$(git rev-parse "${tag_ref}^{commit}")"
checkout_commit="$(git rev-parse --verify HEAD)"
if [[ "${tag_commit}" != "${checkout_commit}" ]]; then
  echo "Release tag ${release_tag} does not point to the checked-out commit." >&2
  exit 1
fi

echo "Validated release tag ${release_tag} at ${checkout_commit}."
