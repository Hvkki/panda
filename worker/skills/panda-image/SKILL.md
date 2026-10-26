---
name: panda-image
description: "Make NEW pictures with AI: draw, paint, generate or design an image, illustration, poster, wallpaper, avatar, sticker, logo idea or picture of anything. Uses the user's ChatGPT plan through Codex (no extra cost). To FIND existing photos on the web, use image-search instead."
triggers: draw, drawing, generate an image, generate a picture, make an image, make a picture, create an image, create a picture, paint, illustration, poster, wallpaper, avatar, sticker, logo idea, image generation, codex
---

# Panda Image (Codex)

```bash
python3 ~/.kiro/crew/skills/panda-image/make_image.py "<detailed description>" ~/outputs/<short-name>.png
```

- One picture per call, about 45 s (measured: 31–56 s, 44 s on average). For several pictures, run
  the calls one after another, not in parallel. While it runs, the app shows a drawing panda with a %.
- Then show it in your reply with the path the script printed: `![caption](~/outputs/<short-name>.png)`.
  The app shows it inline (no download card). Don't paste the path as text.
- Write a rich prompt in English: subject, setting, style, colours, mood, framing, and "No text" unless
  the user wants words in the picture. Keep everything the user asked for.
- `.jpg` / `.webp` names are converted; an existing name gets `-2`, `-3`.
- Cost: about 0.4% of the plan's 5-hour limit per picture (5 pictures: 16% → 18%; the weekly limit
  moved less than 1%). The script prints both limits after each picture: mention them only when one
  is over 80% or the user asks.
- Exit 3 = Codex is not signed in: run `make_image.py --login`, give the user the link and the code,
  wait for them to say done, then check with `make_image.py --status`.
- Exit 4 = the plan's limit is reached: tell the user when it resets (printed).
- `make_image.py --status`: signed in? and the limits as of the last picture.
- The login is saved encrypted and Codex is installed again at every start, so the user signs in once.
  Don't run `codex` yourself and don't copy files out of `~/.codex`: the script does it.
