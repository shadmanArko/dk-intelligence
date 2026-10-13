"""Build the two deliverables from deck-source.html + fonts.css:
standalone  a complete HTML document, everything inlined, works offline from any folder
artifact    the same page as a fragment for the Artifact tool (it adds its own document skeleton)"""

import pathlib
import re

src = pathlib.Path("deck-source.html").read_text()
fonts = pathlib.Path("fonts.css").read_text()
# one shared file serves all three IBM Plex Sans weights: embed it once, with a weight range
faces = fonts.split("\n")
plex = [f for f in faces if "IBM Plex Sans" in f]
if len(plex) == 3 and len({re.search(r"base64,([^)]+)\)", f).group(1) for f in plex}) == 1:
    merged = re.sub(r"font-weight:\d+", "font-weight:400 600", plex[0], count=1)
    faces = [f for f in faces if "IBM Plex Sans" not in f] + [merged]
fonts_css = "\n".join(faces)
title = re.search(r"<title>.*?</title>", src, flags=re.S).group(0)
meta_charset = '<meta charset="utf-8">'
style_open = src.index("<style>")
body_start = src.index("</style>") + len("</style>")
style_css = src[style_open + len("<style>") : src.index("</style>")]
body = src[body_start:]
FONT_STYLE = "<style>\n" + fonts_css + "\n</style>\n"
# artifact fragment: title, meta, fonts, style, body
artifact = (
    title
    + "\n"
    + '<meta name="color-scheme" content="light">\n'
    + FONT_STYLE
    + "<style>"
    + style_css
    + "</style>"
    + body
)
# standalone document
standalone = (
    '<!doctype html>\n<html lang="en">\n<head>\n' + meta_charset + "\n"
    '<meta name="viewport" content="width=device-width,initial-scale=1">\n<meta name="color-scheme" content="light">\n'
    + title
    + "\n"
    + FONT_STYLE
    + "<style>"
    + style_css
    + "</style>\n</head>\n<body>"
    + body
    + "\n</body>\n</html>\n"
)
NAME = "AI-Native-Autonomous-Digital-Marketing-Department_Social-Media-MVP-v1.0.html"
pathlib.Path("content-studio-deck.html").write_text(artifact)  # same path as before: republishing keeps the link
pathlib.Path(NAME).write_text(standalone)
for n, t in (("artifact", artifact), ("standalone", standalone)):
    assert "fonts.googleapis" not in t and "http://" not in t.replace("http://www.w3.org/2000/svg", ""), n
    ext = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', t)
    print(n, len(t) // 1024, "KB", "external refs:", ext)
print(NAME)
