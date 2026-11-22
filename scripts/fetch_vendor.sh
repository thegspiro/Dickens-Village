#!/bin/sh
# Download the 3D viewer and its decoders into app/static/vendor so they are
# served by the app itself (no CDN). Needs Node.js/npm and network access.
# The Docker build runs this; run it by hand when developing outside Docker.
#
#   scripts/fetch_vendor.sh            # into app/static/vendor
#   scripts/fetch_vendor.sh DEST_DIR
#
# npm checks each package against the registry's published integrity hash.
set -eu

MODEL_VIEWER_VERSION="${MODEL_VIEWER_VERSION:-4.0.0}"
THREE_VERSION="${THREE_VERSION:-0.169.0}"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest="${1:-$script_dir/../app/static/vendor}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

cd "$work"
npm pack --silent "@google/model-viewer@$MODEL_VIEWER_VERSION" "three@$THREE_VERSION" >/dev/null
mkdir mv three
tar -xzf google-model-viewer-"$MODEL_VIEWER_VERSION".tgz -C mv
tar -xzf three-"$THREE_VERSION".tgz -C three

mkdir -p "$dest/model-viewer" "$dest/draco" "$dest/basis"
cp mv/package/dist/model-viewer.min.js "$dest/model-viewer/"
cp mv/package/LICENSE "$dest/model-viewer/LICENSE" 2>/dev/null || true
cp three/package/examples/jsm/libs/draco/gltf/draco_decoder.js \
   three/package/examples/jsm/libs/draco/gltf/draco_decoder.wasm \
   three/package/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js "$dest/draco/"
cp three/package/examples/jsm/libs/basis/basis_transcoder.js \
   three/package/examples/jsm/libs/basis/basis_transcoder.wasm "$dest/basis/"
cp three/package/LICENSE "$dest/LICENSE-three" 2>/dev/null || true
printf 'model-viewer %s\nthree %s (draco, basis)\n' "$MODEL_VIEWER_VERSION" "$THREE_VERSION" > "$dest/VERSIONS"
echo "Vendored files written to $dest"
