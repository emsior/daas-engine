#!/usr/bin/env bash
# Exit codes: 0 = clean, 1 = unlisted finding, 2 = scanner/input error.
set -euo pipefail
export LC_ALL=C
trap 'printf "%s\n" "OPSEC scanner error" >&2; exit 2' ERR

fail() {
  printf 'OPSEC: %s\n' "$1" >&2
  exit 2
}

# Keep the five existing rules. Spell multibyte letters as exact bytes, not
# a character class: after removing NUL, UTF-16 letters retain their 01 byte.
rules=(email vocab-a vocab-b project name)
patterns=('mcq08[9]' 'bukmache[r]' 'bettin[g]' 'CS2Op[s]'
  $'[Mm][Ii][Cc][Hh][Aa]([lL]|\xc5\x82|\xc5\x81|B\x01|A\x01|\x01B|\x01A)')
combined=$(IFS='|'; printf '%s' "${patterns[*]}")

mode=''
allowlist=''
if [[ $# == 1 && $1 == --tree ]]; then
  mode=tree
else
  [[ $# == 2 || $# == 4 ]] || fail "expected --tree or --range BASE..HEAD [--allowlist FILE]"
  [[ $1 == --range ]] || fail "unknown mode"
  mode=range
  range=$2
  if [[ $# == 4 ]]; then
    [[ $3 == --allowlist && -n $4 ]] || fail "invalid allowlist option"
    allowlist=$4
  fi
fi

declare -A allowed=() seen=()
if [[ -n $allowlist ]]; then
  [[ -f $allowlist && -r $allowlist ]] || fail "cannot read allowlist"
  while IFS= read -r entry || [[ -n $entry ]]; do
    entry=${entry%$'\r'}
    [[ -z $entry || $entry == \#* ]] && continue
    [[ $entry =~ ^[0-9a-f]{40}:.+:(email|vocab-a|vocab-b|project|name)$ && $entry != *$'\t'* ]] || fail "invalid allowlist entry"
    allowed["$entry"]=1
  done < "$allowlist"
fi

tmp=$(mktemp -d) || fail "cannot create temporary directory"
trap 'rm -rf -- "$tmp"' EXIT
# All Git diagnostics stay private, including errors mentioning unsafe paths.
root=$(git rev-parse --show-toplevel 2> "$tmp/errors") || fail "cannot locate repository"
cd -- "$root" || fail "cannot enter repository"
: > "$tmp/findings"
: > "$tmp/names"

validate_path() {
  [[ -n $1 && $1 != *\"* && ! $1 =~ [[:cntrl:]] ]] || fail "unsupported path"
}

# Do not use grep -q: consume the entire producer and preserve pipefail errors.
matches() {
  local status
  { tr -d '\000' < "$1" > "$tmp/normalized"; } 2> "$tmp/errors" || fail "normalization failed"
  if grep -a -i -E -- "$2" "$tmp/normalized" > /dev/null 2> "$tmp/errors"; then
    # UTF-16 residual A/B bytes are code units, not ASCII letters to fold.
    # The explicit ASCII classes retain case-insensitive semantics for the name.
    if [[ $2 == "${patterns[4]}" ]]; then
      if grep -a -E -- "$2" "$tmp/normalized" > /dev/null 2> "$tmp/errors"; then
        return 0
      else
        status=$?
        [[ $status == 1 ]] || fail "matching failed"
        return 1
      fi
    fi
    return 0
  else
    status=$?
    [[ $status == 1 ]] || fail "matching failed"
    return 1
  fi
}

safe_path() {
  local pattern
  safe=$1
  validate_path "$safe"
  printf '%s' "$safe" > "$tmp/path"
  for pattern in "${patterns[@]}"; do
    if matches "$tmp/path" "$pattern"; then
      safe="/name/$(git hash-object --stdin < "$tmp/path" 2> "$tmp/errors")" || fail "cannot hash path"
      break
    fi
  done
}

record() {
  local fingerprint
  safe_path "$2"
  if [[ $mode == tree ]]; then
    fingerprint="$safe:$3"
  else
    fingerprint="$1:$safe:$3"
  fi
  if [[ -z ${allowed["$fingerprint"]+yes} && -z ${seen["$fingerprint"]+yes} ]]; then
    printf '%s\n' "$fingerprint" >> "$tmp/findings"
    seen["$fingerprint"]=1
  fi
}

scan_blob() {
  local i
  if ! matches "$3" "$combined"; then return 0; fi
  for i in "${!rules[@]}"; do
    if matches "$3" "${patterns[$i]}"; then
      record "$1" "$2" "${rules[$i]}"
    fi
  done
}

parse_patch() {
  awk '
  /^OPSEC-COMMIT / {
    if (length($2) != 40 || $2 !~ /^[0-9a-f]+$/) exit 2
    sha = $2; merge = (NF > 3); path = ""; hunk = 0; next
  }
  /^diff --git / { path = ""; hunk = 0; next }
  !hunk && /^\+\+\+ / {
    if ($0 == "+++ /dev/null") { path = ""; next }
    if (substr($0, 1, 6) != "+++ b/") exit 2
    path = substr($0, 7)
    sub(/\t$/, "", path)
    if (path == "" || path ~ /[[:cntrl:]"]/) exit 2
    next
  }
  /^@@ / { hunk = 1; next }
  hunk && /^\+/ {
    if (sha == "" || path == "") exit 2
    printf "%s\t%d\t%s\t%s\n", sha, merge, path, substr($0, 2)
  }
'
}

scan_records() {
  local i tab expression status sha path
  tr -d '\000' < "$1" > "$tmp/normalized-records" || fail "normalization failed"
  for i in "${!rules[@]}"; do
    # Match content only, not the SHA, merge flag or path columns.
    tab=$'\t'
    expression="^[^${tab}]*${tab}[^${tab}]*${tab}[^${tab}]*${tab}.*(${patterns[$i]})"
    if grep -a -i -E -- "$expression" "$tmp/normalized-records" > "$tmp/matches" 2> "$tmp/errors"; then
      if [[ ${rules[$i]} == name ]]; then
        if grep -a -E -- "$expression" "$tmp/matches" > "$tmp/exact" 2> "$tmp/errors"; then
          awk '{ print }' "$tmp/exact" > "$tmp/matches"
        else
          status=$?
          [[ $status == 1 ]] || fail "content scan failed"
          continue
        fi
      fi
      awk -F '\t' '{ printf "%s\t%s\n", $1, $3 }' "$tmp/matches" > "$tmp/locations"
      while IFS=$'\t' read -r sha path; do
        record "$sha" "$path" "${rules[$i]}"
      done < "$tmp/locations"
    else
      status=$?
      [[ $status == 1 ]] || fail "content scan failed"
    fi
  done
}

if [[ $mode == tree ]]; then
  git ls-files -z -- . ':!*.png' ':!*.mp4' > "$tmp/paths" 2> "$tmp/errors" || fail "tree enumeration failed"
  while IFS= read -r -d '' path; do
    validate_path "$path"
    printf 'tree\t0\t%s\t%s\n' "$path" "$path" >> "$tmp/names"
  done < "$tmp/paths"
  scan_records "$tmp/names"
  empty_tree=$(git hash-object -t tree --stdin < /dev/null 2> "$tmp/errors") || fail "cannot identify empty tree"
  # Git emits working-tree bytes (including staged additions and link targets)
  # without following a tracked symbolic link outside the repository.
  if ! {
    printf 'OPSEC-COMMIT %040d\n' 0
    git -c core.quotePath=false diff --text --no-color --no-ext-diff --no-textconv \
      --no-renames --unified=0 --src-prefix=a/ --dst-prefix=b/ "$empty_tree" \
      -- . ':!*.png' ':!*.mp4' 2> "$tmp/errors"
  } | parse_patch > "$tmp/added"; then
    fail "tree scan failed"
  fi
  scan_records "$tmp/added"
else
  [[ $range == *..* && $range != *...* ]] || fail "expected a two-ended range"
  base=${range%%..*}
  head=${range#*..}
  [[ -n $base && -n $head && $head != *..* ]] || fail "empty or malformed endpoint"
  [[ ! $base =~ ^0+$ && ! $head =~ ^0+$ ]] || fail "zero endpoint"
  base_sha=$(git rev-parse --verify --end-of-options "$base^{commit}" 2> "$tmp/errors") || fail "invalid base"
  head_sha=$(git rev-parse --verify --end-of-options "$head^{commit}" 2> "$tmp/errors") || fail "invalid head"
  git rev-list --parents "$base_sha..$head_sha" -- > "$tmp/commits" 2> "$tmp/errors" || fail "cannot read range"
  [[ -s $tmp/commits ]] || fail "empty range"

  # The marker contains only Git-generated object IDs, never a commit message.
  # Raw content remains in files/pipes: no Bash substitution can discard NUL.
  if ! git -c core.quotePath=false log --text --no-color --no-ext-diff --no-textconv \
      --no-renames --full-history --diff-merges=first-parent --format='OPSEC-COMMIT %H %P' -p --unified=0 \
      --src-prefix=a/ --dst-prefix=b/ "$base_sha..$head_sha" -- ':/*' ':!*.png' ':!*.mp4' 2> "$tmp/errors" | parse_patch > "$tmp/added"; then
    fail "history scan failed"
  fi

  while read -r sha parent other_parents; do
    merge=0
    [[ -z $other_parents ]] || merge=1
    if [[ -n $parent ]]; then
      git diff-tree -r -z --no-commit-id --name-only --diff-filter=ACR -M -C "$parent" "$sha" -- ':/*' ':!*.png' ':!*.mp4' > "$tmp/paths" 2> "$tmp/errors" || fail "cannot read names"
    else
      git diff-tree --root -r -z --no-commit-id --name-only --diff-filter=ACR -M -C "$sha" -- ':/*' ':!*.png' ':!*.mp4' > "$tmp/paths" 2> "$tmp/errors" || fail "cannot read names"
    fi
    while IFS= read -r -d '' path; do
      validate_path "$path"
      printf '%s\t%d\t%s\n' "$sha" "$merge" "$path" >> "$tmp/names"
    done < "$tmp/paths"
  done < "$tmp/commits"

  # Account for a merge addition only through the same secondary parent:
  # the exact raw line/path must survive there, and its non-merge origin must
  # belong to base..that-parent. Neither another branch nor deleted history
  # can authorize a new merge resolution. Keep content out of Bash variables.
  : > "$tmp/accounted-lines"
  : > "$tmp/accounted-names"
  while read -r sha parent other_parents; do
    [[ -n $other_parents ]] || continue
    awk -F '\t' -v sha="$sha" '$1 == sha && !seen[$3]++ { print $3 }' \
      "$tmp/added" "$tmp/names" > "$tmp/merge-paths"
    [[ -s $tmp/merge-paths ]] || continue
    for secondary in $other_parents; do
      git rev-list --no-merges "$base_sha..$secondary" -- > "$tmp/ancestors" 2> "$tmp/errors" || fail "cannot read parent ancestry"
      : > "$tmp/parent-lines"
      : > "$tmp/parent-names"
      while IFS= read -r path; do
        git ls-tree -r -z --full-tree "$secondary" -- ":(literal)$path" > "$tmp/parent-entry" 2> "$tmp/errors" || fail "cannot read parent tree"
        while IFS=$'\t' read -r -d '' metadata parent_path; do
          [[ $parent_path == "$path" ]] || continue
          printf '%s\t1\t%s\n' "$sha" "$path" >> "$tmp/parent-names"
          read -r object_mode object_type object_sha <<< "$metadata"
          [[ $object_type == blob ]] || continue
          git cat-file blob "$object_sha" > "$tmp/parent-blob" 2> "$tmp/errors" || fail "cannot read parent content"
          printf '%s\t1\t%s\n' "$sha" "$path" > "$tmp/parent-prefix"
          awk 'FILENAME == ARGV[1] { prefix = $0; next }
            { printf "%s\t%s\n", prefix, $0 }' \
            "$tmp/parent-prefix" "$tmp/parent-blob" >> "$tmp/parent-lines"
        done < "$tmp/parent-entry"
      done < "$tmp/merge-paths"

      awk -F '\t' '
        FILENAME == ARGV[1] { ancestor[$1] = 1; next }
        {
          content = $0
          for (i = 0; i < 3; i++) sub(/^[^\t]*\t/, "", content)
          key = $3 SUBSEP content
        }
        FILENAME == ARGV[2] {
          if ($2 == 0 && ($1 in ancestor)) origin[key] = 1
          next
        }
        key in origin { print }
      ' "$tmp/ancestors" "$tmp/added" "$tmp/parent-lines" >> "$tmp/accounted-lines"
      awk -F '\t' '
        FILENAME == ARGV[1] { ancestor[$1] = 1; next }
        FILENAME == ARGV[2] {
          if ($2 == 0 && ($1 in ancestor)) origin[$3] = 1
          next
        }
        $3 in origin { print }
      ' "$tmp/ancestors" "$tmp/names" "$tmp/parent-names" >> "$tmp/accounted-names"
    done
  done < "$tmp/commits"

  # Merge line provenance is keyed by merge SHA, path and unnormalized bytes.
  awk -F '\t' '
    {
      content = $0
      for (i = 0; i < 3; i++) sub(/^[^\t]*\t/, "", content)
      key = $1 SUBSEP $3 SUBSEP content
    }
    FILENAME == ARGV[1] { accounted[key] = 1; next }
    $2 == 0 || !(key in accounted) { print }
  ' "$tmp/accounted-lines" "$tmp/added" > "$tmp/unaccounted"
  scan_records "$tmp/unaccounted"

  while read -r sha parent other_parents; do
    git show -s --format='%B' "$sha" -- > "$tmp/message" 2> "$tmp/errors" || fail "cannot read message"
    scan_blob "$sha" /message "$tmp/message"
    git show -s --format='%an %ae %cn %ce' "$sha" -- > "$tmp/identity" 2> "$tmp/errors" || fail "cannot read identity"
    scan_blob "$sha" /identity "$tmp/identity"
  done < "$tmp/commits"
  # Name provenance likewise belongs to this merge, not to the whole range.
  awk -F '\t' '
    { key = $1 SUBSEP $3 }
    FILENAME == ARGV[1] { accounted[key] = 1; next }
    $2 == 0 || !(key in accounted) { printf "%s\t%s\n", $1, $3 }
  ' "$tmp/accounted-names" "$tmp/names" > "$tmp/new-names"
  while IFS=$'\t' read -r sha path; do
    printf '%s' "$path" > "$tmp/path-name"
    scan_blob "$sha" "$path" "$tmp/path-name"
  done < "$tmp/new-names"
fi

# Publish only after every producer, parser and matcher has succeeded.
if [[ -s $tmp/findings ]]; then
  awk '{ print }' "$tmp/findings"
  exit 1
fi
exit 0
