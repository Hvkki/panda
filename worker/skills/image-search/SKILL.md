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
  Paste the lines you want into your answer — the app shows them as images.
- `--download` also saves them to `~/outputs/images/<query>/` (use for docs/slides,
  then reference the local path in the Markdown you convert).
- Sources: Openverse (Creative Commons, 700M+ images) and Wikimedia Commons.
  Always keep the credit line when the user will publish the image.
- For logos, products or people the free sources lack, use web search and only
  show images from pages you actually fetched.
