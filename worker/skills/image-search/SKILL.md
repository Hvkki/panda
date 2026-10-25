---
name: image-search
description: "Find real photos and images on the web (with license and source). Use when the user asks to find, search, show or get pictures, photos, images, illustrations or visual references, or needs images for a post, doc or slide."
triggers: image, images, photo, photos, picture, pictures, find a picture, show me, visual reference, stock photo
---

# Image Search

```bash
python3 ~/.kiro/crew/skills/image-search/find_images.py "red panda eating bamboo" [-n 6] [--download]
```

- Prints a Markdown gallery: `![title](image_url)` plus source page and license.
  **To show the user photos, paste the `![title](image_url)` lines into your answer** — they render
  as inline images straight from the web. You do NOT need to download them and you do NOT create a
  file for each one.
- `--download` saves them to `~/work/images/<query>/` — a working folder that is NOT turned into a
  download card. Use it only when you will build them into a document (reference the saved paths in
  the Markdown you pass to `panda-docs`), so the user gets the document, not dozens of photo cards.
  `--out ~/outputs/...` only if the user explicitly asked for the image files themselves.
- Sources: Openverse (Creative Commons, 700M+ images) and Wikimedia Commons.
  Always keep the credit line when the user will publish the image.
- For logos, products or people the free sources lack, use web search and only
  show images from pages you actually fetched.
