#!/bin/sh
# Next.js inlines NEXT_PUBLIC_* env vars into the client JS bundle at BUILD
# time, not at container start -- so a single published image (e.g. the
# Unraid-distributed ghcr.io/jgoldenally/genmonitoring-portal image) would
# otherwise have one Unraid user's API URL baked in forever, unusable for
# anyone else. To make NEXT_PUBLIC_API_URL genuinely configurable per
# deployment (as the Unraid template's env var implies), the image is built
# with a placeholder value baked in, and this entrypoint rewrites every
# occurrence of that placeholder in the built output to the real
# NEXT_PUBLIC_API_URL supplied at container start, before the server runs.
set -eu

PLACEHOLDER="http://__GENMON_RUNTIME_API_URL__"
TARGET="${NEXT_PUBLIC_API_URL:-}"

if [ -n "$TARGET" ] && [ "$TARGET" != "$PLACEHOLDER" ]; then
  echo "genmon-portal: rewriting baked-in API URL placeholder -> $TARGET"
  find /app -type f \( -name "*.js" -o -name "*.html" -o -name "*.json" \) -not -path "*/node_modules/*" -print0 2>/dev/null \
    | xargs -0 grep -l "$PLACEHOLDER" 2>/dev/null \
    | while IFS= read -r f; do
        sed -i "s#$PLACEHOLDER#$TARGET#g" "$f"
      done
fi

exec "$@"
