#!/usr/bin/env bash
#
# 新しいエピソードのフォルダを templates/ から作成します。
#
#   ./scripts/new-episode.sh 001 京都の朝さんぽ
#     -> episodes/EP001-京都の朝さんぽ/ を作成
#
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "使い方: $0 <エピソード番号> <短いタイトル>" >&2
  echo "例:     $0 001 京都の朝さんぽ" >&2
  exit 1
fi

number="$1"
shift
title="$*"

# 番号を 3 桁ゼロ埋めに正規化する（1 -> 001）
if ! [[ "$number" =~ ^[0-9]+$ ]]; then
  echo "エラー: エピソード番号は数字で指定してください（例: 001）" >&2
  exit 1
fi
number=$(printf '%03d' "$((10#$number))")

# タイトル内のスペースはハイフンに寄せる（命名規則に合わせる）
title="${title// /-}"

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
episode_id="EP${number}"
episode_dir="${repo_root}/episodes/${episode_id}-${title}"

if [ -e "$episode_dir" ]; then
  echo "エラー: ${episode_dir} はすでに存在します" >&2
  exit 1
fi

mkdir -p "$episode_dir"

for template in "${repo_root}"/templates/*.md; do
  filename=$(basename "$template")
  # 雛形の中の EP000 / タイトル未定 を差し替える
  sed -e "s/EP000/${episode_id}/g" \
      -e "s/タイトル未定/${title}/g" \
      "$template" > "${episode_dir}/${filename}"
done

echo "作成しました: episodes/${episode_id}-${title}/"
echo
echo "次にやること:"
echo "  1. 企画書.md を書く"
echo "  2. episodes/README.md の一覧に 1 行追加する"
