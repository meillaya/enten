#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GITLEAKS="${GITLEAKS:-gitleaks}"
GITLEAKS_VERSION="8.30.1"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

actual_version="$($GITLEAKS version 2>/dev/null || true)"
if [[ "$actual_version" != "$GITLEAKS_VERSION" ]]; then
  printf 'FAIL: gitleaks %s is required, found %s\n' "$GITLEAKS_VERSION" "${actual_version:-missing}" >&2
  exit 2
fi

cat > "$TMP_DIR/expected-ignore" <<'IGNORE'
acd3c138d73ce4088ef946e0fabadc712f6da3a8:.omo/evidence/final-qa/f3-gitleaks-report.json:generic-api-key:30
acd3c138d73ce4088ef946e0fabadc712f6da3a8:.omo/evidence/final-qa/f3-gitleaks-report.json:slack-bot-token:9
acd3c138d73ce4088ef946e0fabadc712f6da3a8:.omo/evidence/final-qa/f3-gitleaks-report.json:slack-bot-token:10
IGNORE
if ! cmp -s .gitleaksignore "$TMP_DIR/expected-ignore"; then
  printf 'FAIL: .gitleaksignore must contain only the three reviewed historical fingerprints\n' >&2
  diff -u "$TMP_DIR/expected-ignore" .gitleaksignore >&2 || true
  exit 1
fi

mapfile -t private_omx_paths < <(
  git ls-files .omo .omc .omx | grep -Ev '^\.omo/plans/' || true
)
if ((${#private_omx_paths[@]})); then
  printf 'FAIL: private OMX paths are tracked:\n' >&2
  printf '  %s\n' "${private_omx_paths[@]}" >&2
  exit 1
fi

"$GITLEAKS" git \
  --no-banner \
  --redact=100 \
  --log-opts=--all \
  --gitleaks-ignore-path .gitleaksignore \
  --report-format json \
  --report-path "$TMP_DIR/history.json" \
  .

mkdir "$TMP_DIR/archive"
release_paths="$TMP_DIR/release-paths"
while IFS= read -r -d '' path; do
  if [[ -f "$path" || -L "$path" ]]; then
    printf '%s\0' "$path"
  fi
done < <(git ls-files --cached --others --exclude-standard -z) > "$release_paths"
tar --null --files-from "$release_paths" --create --file - | tar -x -C "$TMP_DIR/archive"
mapfile -t archived_private_paths < <(
  find "$TMP_DIR/archive/.omo" -type f ! -path '*/plans/*' -print 2>/dev/null
  find "$TMP_DIR/archive/.omc" "$TMP_DIR/archive/.omx" -type f -print 2>/dev/null
  true
)
if ((${#archived_private_paths[@]})); then
  printf 'FAIL: private OMX paths are present in the release archive:\n' >&2
  printf '  %s\n' "${archived_private_paths[@]}" >&2
  exit 1
fi

"$GITLEAKS" dir \
  --no-banner \
  --redact=100 \
  --report-format json \
  --report-path "$TMP_DIR/archive.json" \
  "$TMP_DIR/archive"

printf 'Release secret gate passed: pinned scanner, reviewed history, and archive are clean.\n'
