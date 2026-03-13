#!/usr/bin/env bash
# Pinned uv release assets used by scripts/init_system.sh.

UV_VERSION="0.10.7"
UV_RELEASE_BASE_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"

UV_TARBALL_X86_64_GNU="uv-x86_64-unknown-linux-gnu.tar.gz"
UV_TARBALL_X86_64_GNU_SHA256="9ac6cee4e379a5abfca06e78a777b26b7ba1f81cb7935b97054d80d85ac00774" # pragma: allowlist secret

UV_TARBALL_X86_64_MUSL="uv-x86_64-unknown-linux-musl.tar.gz"
UV_TARBALL_X86_64_MUSL_SHA256="992529add6024e67135b1c80617abd2eca7be2cf0b99b3911f923de815bd8dc1" # pragma: allowlist secret

UV_TARBALL_AARCH64_GNU="uv-aarch64-unknown-linux-gnu.tar.gz"
UV_TARBALL_AARCH64_GNU_SHA256="20efc27d946860093650bcf26096a016b10fdaf03b13c33b75fbde02962beea9" # pragma: allowlist secret

UV_TARBALL_AARCH64_MUSL="uv-aarch64-unknown-linux-musl.tar.gz"
UV_TARBALL_AARCH64_MUSL_SHA256="115291f9943531a3b63db3a2eabda8b74b8da4831551679382cb309c9debd9f7" # pragma: allowlist secret
