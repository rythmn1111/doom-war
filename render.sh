#!/usr/bin/env bash
# Turn captured 1920x1080 frames into a Twitter-ready MP4.
# Usage: ./render.sh frames/round2_realtime out/round2.mp4 [fps]
set -euo pipefail

SRC=${1:?usage: render.sh <frame-dir> <out.mp4> [fps]}
OUT=${2:?usage: render.sh <frame-dir> <out.mp4> [fps]}
FPS=${3:-30}

mkdir -p "$(dirname "$OUT")"
[ -e "$OUT" ] && { echo "refusing to overwrite $OUT"; exit 1; }

n=$(ls "$SRC"/f_*.png 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -gt 0 ] || { echo "no frames in $SRC"; exit 1; }
echo "rendering $n frames at ${FPS}fps -> $OUT"

# yuv420p + even dimensions so every player and Twitter's transcoder accepts it.
ffmpeg -loglevel warning -framerate "$FPS" -i "$SRC/f_%06d.png" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
  -vf "scale=1920:1080:flags=lanczos" -movflags +faststart "$OUT"

# A short GIF of the first 12 seconds, for the reply-thread preview.
GIF="${OUT%.mp4}.gif"
if [ ! -e "$GIF" ]; then
  ffmpeg -loglevel warning -t 12 -i "$OUT" \
    -vf "fps=15,scale=1040:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" \
    "$GIF"
fi

echo "done:"
ls -lh "$OUT" "$GIF" 2>/dev/null || ls -lh "$OUT"
