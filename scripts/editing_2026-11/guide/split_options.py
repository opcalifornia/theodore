"""One-time: replace the paired A-H pages in theodore_guide.html with one page per option, then renumber and fix page refs."""
import re
from option_pages import OPTS, CSS, page
p = "theodore_guide.html"; s = open(p).read()
a = s.index("<!-- 5 · A + B -->"); b = s.index("<!-- 9 · MR.H METHOD (1) -->")
s = s[:a] + "".join(page(o, 5 + i) for i, o in enumerate(OPTS)) + s[b:]
s = s.replace("</style>", CSS + "</style>", 1)
# sequential page numbers
parts = re.split(r'(?=<section class="page)', s); n = 0; out = []
for part in parts:
    if part.startswith('<section class="page'):
        n += 1
        part = re.sub(r'(<div class="foot">.*?<span class="mono">)\d\d(</span></div>)', lambda m: f"{m.group(1)}{n:02d}{m.group(2)}", part, count=1, flags=re.S)
    out.append(part)
s = "".join(out)
s = s.replace("pages 09 and 10", "pages 13 and 14").replace("Parts built · page 11", "Parts built · page 15").replace("(page 12)", "(page 16)")
open(p, "w").write(s); print("sections", n)
