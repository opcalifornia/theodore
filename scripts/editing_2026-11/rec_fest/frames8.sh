#!/bin/zsh
# usage: frames8.sh "<path>|<tc>|<frames>|<lens>"
IFS='|' read f tc n lens <<< "$1"; c=$(basename "$f" | cut -c6-9); S=/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/recfest
for p in 4 16 28 40 52 64 76 90; do fr=$((n*p/100)); REDline --i "/Volumes/Pool 1/Rec Fest Year 2/${f#./}" --format 3 --res 8 -s $fr -e $fr --outDir $S/shotlog --o ${c}_$(printf %02d $p) >/dev/null 2>&1; done
echo $c
