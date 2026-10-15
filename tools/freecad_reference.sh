#!/bin/bash
# freecad_reference.sh — build a goal file with the FreeCAD reference builder (the scripted
# teacher of a Taiga work folder), for compare_freecad.py.
# Version: 2026.10.15.1
#
# Usage: WORK=~/taiga-expanded-0017 TAIGA_REPO=~/Software/taiga-decision-model \
#          tools/freecad_reference.sh GOALS.json OUT_DIR
# Writes OUT_DIR/<name>.step (and .FCStd) and OUT_DIR/freecad.log (times per part).
set -euo pipefail
goals=$(realpath "$1"); out=$(realpath -m "$2")
WORK=${WORK:-$HOME/taiga-expanded-0017}
TAIGA_REPO=${TAIGA_REPO:-$HOME/Software/taiga-decision-model}
mkdir -p "$out"
cd "$WORK/taiga-s1"
source ../freecad.env
.venv/bin/python "$TAIGA_REPO/build/taiga_build_part.py" --goals "$goals" --teacher --quiet --no-gui-data \
    --out "$out" 2>&1 | tee "$out/freecad.log"
