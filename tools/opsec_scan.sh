#!/usr/bin/env bash
# Exit codes: 0 = clean, 1 = unlisted finding, 2 = scanner/input error.
set -euo pipefail
trap 'printf "%s\n" "OPSEC scanner error" >&2; exit 2' ERR
# Match UTF-8 characters, not their individual bytes, on Linux and Git Bash.
export LC_ALL=C.UTF-8

fail() {
  printf 'OPSEC: %s\n' "$1" >&2
  exit 2
}

# Keep the existing CI rules; bracket spelling prevents self-matches.
rules=(email vocab-a vocab-b project name)
patterns=('mcq08[9]' 'bukmache[r]' 'bettin[g]' 'CS2Op[s]' 'Micha[łl]')
combined=$(IFS='|'; printf '%s' "${patterns[*]}")

if [[ $# == 1 && $1 == --tree ]]; then
  # Git's PCRE mode preserves UTF-8 classes on Windows too; missing support is
  # a scanner error. Only file names are printed, never the matching content.
  if git grep -ilP "$combined" -- ':!*.png' ':!*.mp4'; then
    exit 1
  else
    status=$?
    case "$status" in
      1) exit 0 ;;
      *) fail "tree scan failed" ;;
    esac
  fi
fi

[[ $# == 2 || $# == 4 ]] || fail "expected --tree or --range BASE..HEAD [--allowlist FILE]"
[[ $1 == --range ]] || fail "unknown mode"
range=$2
allowlist=''
if [[ $# == 4 ]]; then
  [[ $3 == --allowlist && -n $4 ]] || fail "invalid allowlist option"
  allowlist=$4
fi

[[ $range == *..* && $range != *...* ]] || fail "expected a two-ended range"
base=${range%%..*}
head=${range#*..}
[[ -n $base && -n $head && $head != *..* ]] || fail "empty or malformed endpoint"
[[ ! $base =~ ^0+$ && ! $head =~ ^0+$ ]] || fail "zero endpoint"
base_sha=$(git rev-parse --verify --end-of-options "$base^{commit}") || fail "invalid base"
head_sha=$(git rev-parse --verify --end-of-options "$head^{commit}") || fail "invalid head"
count=$(git rev-list --count "$base_sha..$head_sha" --) || fail "cannot read range"
[[ $count =~ ^[0-9]+$ && $count != 0 ]] || fail "empty range"

declare -A allowed=()
if [[ -n $allowlist ]]; then
  [[ -f $allowlist && -r $allowlist ]] || fail "cannot read allowlist"
  mapfile -t entries < "$allowlist" || fail "cannot read allowlist"
  for entry in "${entries[@]}"; do
    entry=${entry%$'\r'}
    [[ -z $entry || $entry == \#* ]] && continue
    [[ $entry =~ ^[0-9a-f]{40}:.+:(email|vocab-a|vocab-b|project|name)$ && $entry != *$'\t'* ]] || fail "invalid allowlist entry"
    allowed["$entry"]=1
  done
fi

# Parse only added hunk lines. A content line resembling +++ is still content.
# Refuse unsupported quoted/control-character paths rather than skip a file.
# pipefail propagates both Git and parser errors, including after partial output.
if ! added=$(git -c core.quotePath=false log --no-color --no-ext-diff --no-textconv \
    --no-merges --no-renames --format='OPSEC-COMMIT %H' -p --unified=0 \
    --src-prefix=a/ --dst-prefix=b/ "$base_sha..$head_sha" -- | awk '
  /^OPSEC-COMMIT [0-9a-f]+$/ { sha = $2; path = ""; hunk = 0; next }
  /^diff --git / { path = ""; hunk = 0; next }
  !hunk && /^\+\+\+ / {
    if ($0 == "+++ /dev/null") { path = ""; next }
    if (substr($0, 1, 6) != "+++ b/") exit 2
    path = substr($0, 7)
    sub(/\t$/, "", path)
    if (path == "" || path ~ /[\t\r]/) exit 2
    next
  }
  /^@@ / { hunk = 1; next }
  hunk && /^\+/ {
    if (sha == "" || path == "") exit 2
    printf "%s:%s\t%s\n", sha, path, substr($0, 2)
  }
'); then
  fail "history scan failed"
fi

declare -A seen=()
result=0
tab=$'\t'
for i in "${!rules[@]}"; do
  # Anchor past the metadata so a file name cannot trigger a content rule.
  if matches=$(grep -iE "^[^${tab}]*${tab}.*(${patterns[$i]})" <<< "$added"); then
    fingerprints=$(awk -F '\t' -v rule="${rules[$i]}" '{ print $1 ":" rule }' <<< "$matches")
    while IFS= read -r fingerprint; do
      if [[ -z ${allowed["$fingerprint"]+yes} && -z ${seen["$fingerprint"]+yes} ]]; then
        printf '%s\n' "$fingerprint"
        seen["$fingerprint"]=1
        result=1
      fi
    done <<< "$fingerprints"
  else
    status=$?
    [[ $status == 1 ]] || fail "content scan failed"
  fi
done
exit "$result"
