#!/usr/bin/env bash
# 설치된 launchd 작업 4종 unload + 삭제.
set -euo pipefail

TARGET="$HOME/Library/LaunchAgents"
LABELS=(
  "com.aejin.designwiki.nightly"
  # 이전 버전 잔여물 (혹시 설치되어 있으면 같이 제거)
  "com.aejin.designwiki.ingester"
  "com.aejin.designwiki.classifier"
  "com.aejin.designwiki.curator"
  "com.aejin.designwiki.daily-brief"
)

for LABEL in "${LABELS[@]}"; do
  DST="$TARGET/${LABEL}.plist"
  if [[ -f "$DST" ]]; then
    echo "unload + remove: $LABEL"
    launchctl unload "$DST" 2>/dev/null || true
    rm -f "$DST"
  else
    echo "없음(스킵): $LABEL"
  fi
done

echo "✅ 완료. launchctl list | grep designwiki 로 확인하세요."
