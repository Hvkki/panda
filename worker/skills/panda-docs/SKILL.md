---
name: panda-docs
description: "Create downloadable PDF, Word (.docx) or PowerPoint (.pptx) files from Markdown. Use whenever the user asks for a document, report, letter, CV, handout, ebook, slides, deck or presentation file."
triggers: pdf, docx, word document, report, letter, cv, resume, pptx, powerpoint, slides, deck, presentation, handout
---

# Panda Docs

Write the content as Markdown, then convert it with one command. Files saved in
`~/outputs/` appear in the user's app as downloadable cards automatically.

```bash
python3 ~/.kiro/crew/skills/panda-docs/make_doc.py SOURCE.md ~/outputs/Name.pdf  [--theme ember|ocean|forest|mono] [--title "Title"]
python3 ~/.kiro/crew/skills/panda-docs/make_doc.py SOURCE.md ~/outputs/Name.docx [--theme ...]
python3 ~/.kiro/crew/skills/panda-docs/make_doc.py SOURCE.md ~/outputs/Name.pptx [--theme ...]
```

Supported Markdown: `#`/`##`/`###` headings, paragraphs, **bold**, *italic*,
`- ` and `1. ` lists, `> ` quotes, `|` tables, `---` (page break in PDF/DOCX),
and images `![caption](path-or-url)`.

## Slides (.pptx)
- Each `# ` or `## ` heading starts a new slide; the first `# ` slide is the title slide.
- Under a heading: bullets become slide bullets (keep 3–6 per slide, short);
  a paragraph becomes the subtitle/body; one image per slide is placed on the right.
- Add speaker notes with a line starting `Notes:`.

For a deck or document built from photos, download the photos to `~/work/` (not `~/outputs/`) and
reference those paths in your Markdown — the user then gets the one finished file, not every photo.

## Quality bar
- Give the file a human name (`Minnie-dog-sitter-guide.pptx`, not `output.pptx`).
- Lead with a clear title, keep paragraphs short, use tables for comparisons.
- Never invent facts; mark placeholders like `[your phone number]`.
- After creating the file, tell the user what's inside in 2–4 bullets; do not
  paste the whole content back into chat.
