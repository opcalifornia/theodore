#!/bin/zsh
# usage: dense.sh CLIP STEP
c=$1; step=$2; S=/private/tmp/claude-501/-Users-admin-theodore/412eb248-a16d-5d91-aec8-d9cc64479fe2/scratchpad/recfest
l=$(grep "_${c}_" $S/clips.txt | head -1); f=$(echo "$l" | cut -d'|' -f1); n=$(echo "$l" | cut -d'|' -f3)
fr=0; while [ $fr -lt $n ]; do REDline --i "/Volumes/Pool 1/Rec Fest Year 2/${f#./}" --format 3 --res 8 -s $fr -e $fr --outDir $S/dense --o ${c}_$(printf %05d $fr) >/dev/null 2>&1; fr=$((fr+step)); done; echo done $c
