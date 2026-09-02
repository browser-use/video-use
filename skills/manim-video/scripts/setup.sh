#!/usr/bin/env bash
# quick environment check for the manim video skill
# it looks for python manim latex and ffmpeg and prints a pass or fail line for each

set -euo pipefail
# terminal color codes for the pass and fail markers
G="\033[0;32m"; R="\033[0;31m"; N="\033[0m"
# print a green plus line for a satisfied prerequisite
ok() { echo -e "  ${G}+${N} $1"; }
# print a red x line for a missing prerequisite
fail() { echo -e "  ${R}x${N} $1"; }
echo ""; echo "Manim Video Skill — Setup Check"; echo ""
errors=0
# probe each prerequisite and bump the error count when one is missing
command -v python3 &>/dev/null && ok "Python $(python3 --version 2>&1 | awk '{print $2}')" || { fail "Python 3 not found"; errors=$((errors+1)); }
python3 -c "import manim" 2>/dev/null && ok "Manim $(manim --version 2>&1 | head -1)" || { fail "Manim not installed: pip install manim"; errors=$((errors+1)); }
command -v pdflatex &>/dev/null && ok "LaTeX (pdflatex)" || { fail "LaTeX not found (macOS: brew install --cask mactex-no-gui)"; errors=$((errors+1)); }
command -v ffmpeg &>/dev/null && ok "ffmpeg" || { fail "ffmpeg not found"; errors=$((errors+1)); }
echo ""
# print the overall verdict based on the error count
[ $errors -eq 0 ] && echo -e "${G}All prerequisites satisfied.${N}" || echo -e "${R}$errors prerequisite(s) missing.${N}"
echo ""
