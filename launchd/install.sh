#!/usr/bin/env bash
# launchd plist 4종을 템플릿에서 렌더링해서 ~/Library/LaunchAgents/ 에 설치 후 로드.
# 재실행해도 안전 (이미 로드된 건 unload 후 재로드).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

# .env 로드 (AGENT_HOME이 .env에 있으면 그걸 우선)
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

AGENT_HOME="${AGENT_HOME:-$REPO_ROOT}"

# uv 실제 경로
UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "❌ uv를 찾을 수 없습니다. 'brew install uv' 후 다시 실행하세요." >&2
  exit 1
fi

# PATH (launchd는 로그인 쉘 PATH를 상속하지 않음 → 명시적으로 지정)
LAUNCHD_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$UV_BIN")"

TARGET="$HOME/Library/LaunchAgents"
mkdir -p "$TARGET"
mkdir -p "$AGENT_HOME/logs"

LABELS=(
  "com.aejin.designwiki.nightly"
)

echo "▶ AGENT_HOME: $AGENT_HOME"
echo "▶ UV_BIN    : $UV_BIN"
echo "▶ LaunchAgents: $TARGET"
echo

for LABEL in "${LABELS[@]}"; do
  SRC="$HERE/${LABEL}.plist.template"
  DST="$TARGET/${LABEL}.plist"

  if [[ ! -f "$SRC" ]]; then
    echo "⚠️  템플릿 없음: $SRC" >&2
    continue
  fi

  echo "── $LABEL"

  # 이미 로드돼 있으면 unload
  if launchctl list | grep -q "$LABEL"; then
    echo "  unload 이전 버전"
    launchctl unload "$DST" 2>/dev/null || true
  fi

  # 템플릿 치환
  sed \
    -e "s|__AGENT_HOME__|${AGENT_HOME}|g" \
    -e "s|__UV_BIN__|${UV_BIN}|g" \
    -e "s|__PATH__|${LAUNCHD_PATH}|g" \
    "$SRC" > "$DST"

  chmod 644 "$DST"
  launchctl load "$DST"
  echo "  ✅ loaded → $DST"
done

echo
echo "상태 확인:"
launchctl list | grep designwiki || echo "  (아직 로드된 작업 없음)"
