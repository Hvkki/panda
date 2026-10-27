---
name: panda-video
description: Make AI videos (MiniMax H3) on the owner's Modal GPU: short clips from an idea or a photo, start-to-end-frame clips, 360° orbits of a photo. Use when the user asks to make, generate or animate a video, clip, reel, animation or "make this photo move". Costs real money on the owner's Modal account: estimate first.
---

# Panda video

Videos render on the owner's Modal app `panda-h3` (one RTX PRO 6000, about $3.60 an hour, so
roughly **$0.04-0.10 for a draft, $0.20-0.35 for a 5 s final**). Every job, from any chat or from
the Video tab, goes into one queue on one GPU. Finished clips also appear in the **Video tab**.

Run `pvideo` as-is (it is a real command). Never define a `pvideo()` function or alias.

## 0. Autopilot (default for "make me a video of …")

```bash
pvideo auto --idea "a pelican riding a bicycle along the sea" --wait            # draft, check, then final
pvideo auto --idea "..." --plan quick --wait                                    # draft only, cheapest
pvideo auto --idea "..." --first ~/uploads/photo.jpg --wait                     # starts on a picture
```

Panda's director picks the shape, length, style and sound, writes the script, films a draft, checks
it (dark or frozen gets one new take), then films the final with the same seed. A failure is retried
once. `--wait` prints each step and copies the best take to `~/outputs/`; name that file in your reply.
Pass the user's own words as `--idea`, in any language. It refuses when the plan would go past the
owner's Modal credit ($30 a month, read from Modal's bill): tell the user the reason it printed.
Use the manual steps below only when the user wants exact control (a set length, ratio or script).

## 1. Write the prompt (always English, always this layout)

```
integrated_multimodal_description: [Shot 1] <subject, look, setting, light, lens>. <action with times, e.g. "at 00:01.500 she turns her head at fast speed">; the camera <one move + size + speed, e.g. "pushes in with medium amplitude at slow speed">.
overall_soundscape: <the real sounds of the scene>
non_diegetic_music: <music description, or none>
```

- Clips over 5 s: split into shots, `[Shot 2] At 00:03.500 …`. One camera move per shot.
- Never write "no X" lists (H3 has no negative prompt). Say what you want instead.
- Text on screen goes in "double quotes", one string at a time.
- With a start picture, spend the words on motion and camera, not on looks.
- Save the prompt to a file in `~/work/` and pass the file path.

## 2. Estimate, then make

```bash
pvideo estimate --prompt ~/work/clip.txt --quality draft --ratio 9:16 --secs 5
pvideo make --prompt ~/work/clip.txt --quality draft --ratio 9:16 --secs 5 --title "pelican ride" --wait
```

- Start with a **draft** (`--quality draft`, turbo, cheap) unless the user asked for the final.
  Offer the final after they like the draft.
- `--seeds 2` makes two versions of the draft to pick from (the second one is cheaper: warm GPU).
- Ask before running when the estimate's `usd_high` is above $0.50, or when the user did not ask
  for a video in this message. The owner's Modal credit stop line is enforced: if `make` refuses,
  tell the user the reason it printed.
- Modes: `--mode clip` (text, optional `--first start.png`), `--mode frames --first a.png --last b.png`,
  `--mode orbit --first photo.png` (3 s, 768x768, full 360°).
- `--wait` blocks until done and copies each clip to `~/outputs/<title>-draft-xxxx.mp4`. Name that
  file in your reply so it shows as a card. Without `--wait`, tell the user it is rendering in the
  Video tab, and `pvideo status` shows progress.

## Rules

- H3 outputs may not be used in the EU, US, UK or South Korea without MiniMax's permission. Say it
  once if the user plans to publish.
- Real people's faces or voices: only with their consent. Ask.
- Never spend over the budget, never retry a failed job more than once, never loop.
