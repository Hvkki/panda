---
name: panda-video
description: Make AI videos (MiniMax H3) on the owner's Modal GPU, right in chat: short clips from an idea or a photo, start-to-end-frame clips, 360° orbits of a photo. Use when the user asks to make, generate or animate a video, clip, reel, short, animation or "make this photo move". Gives the `pvideo` command and a Python API (auto, make, estimate, write, budget, list, cancel, wait). Costs real money on the owner's Modal account.
---

# Panda video

Videos render on the owner's Modal app `panda-h3` (one RTX PRO 6000, ~$3.60/h: about
**$0.04-0.10 a draft, $0.20-0.35 a 5 s final**). Every job, from any chat or the Video tab, goes into
one queue on one GPU and also shows in the **Video tab**. Panda must be on (the engine lives in its worker).

Run `pvideo` as-is (a real command). Never define a `pvideo()` function or alias.

## Pick the path

| User says | Do |
|---|---|
| "make me a video of …" (no settings) | `pvideo auto --idea "<their words>" --wait` |
| a set length / ratio / style / script | write the prompt, `estimate`, then `make --wait` |
| "animate this photo" | `auto --first <photo>` or `make --mode clip --first <photo>` |
| "spin around this object" | `make --mode orbit --first <photo> --quality final` |
| "from this frame to that frame" | `make --mode frames --first a.png --last b.png` |
| "how much credit is left?" | `pvideo budget` |
| "stop it" | `pvideo cancel <id>` |

## Tools (`pvideo`)

```bash
pvideo auto --idea "a pelican riding a bicycle along the sea" --wait   # direct, draft, check, final
pvideo auto --idea "..." --plan quick --wait                           # draft + check only (cheapest)
pvideo auto --idea "..." --first ~/uploads/photo.jpg --wait            # starts on a picture
pvideo write --idea "..." --secs 6 --ratio 16:9 --out ~/work/clip.txt  # Panda's writer: idea -> H3 prompt
pvideo estimate --prompt ~/work/clip.txt --quality draft --ratio 16:9 --secs 6
pvideo make --prompt ~/work/clip.txt --quality draft --ratio 16:9 --secs 6 --title "pelican" --wait
pvideo make --prompt ~/work/clip.txt --quality final --ratio 16:9 --secs 6 --seed <draft seed> --wait
pvideo budget            # Modal this month: spent, booked, cap, left
pvideo list -n 8         # newest clips and autopilot films with status, % and cost
pvideo wait <id> [<id>]  # wait for something started earlier (ids starting with r are films)
pvideo cancel <id>       # stop a clip, or a whole film (r…)
pvideo status [<id>]     # quick progress line
```

Add `--json` before the subcommand (`pvideo --json auto --idea … --wait`) to get one JSON object:
`{"ok": true, "file": "...mp4", "cost": 0.31, ...}` or `{"ok": false, "error": "..."}`.

- `--wait` prints each step and copies the finished clip to `~/outputs/`. **Name that file in your
  reply**: it shows as a playable card in the chat. Without `--wait`, say it is rendering in the
  Video tab and use `pvideo wait <id>` later.
- Autopilot's director picks shape, length, style and sound, writes the script, films a draft,
  checks it (dark or frozen gets one retake), then films the final on the same seed. Pass the
  user's own words as `--idea`, any language.

## Python API (same calls, for scripts and loops)

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.kiro/crew/skills/panda-video")); import video
video.budget()                                    # {"now", "booked", "cap", "left", ...}
rid = video.auto("a red panda chef at sunrise", plan="full")
film = video.wait_run(rid, on_step=print)         # {"status", "file", "title", "cost", "choice", "why", "error"}
p = video.write("a paper boat in the rain", secs=6, ratio="9:16")["prompt"]
e = video.estimate(prompt=p, quality="draft", ratio="9:16", secs=6)   # total_usd, total_high, left
ids = video.make(p, quality="draft", ratio="9:16", secs=6, title="boat")["jobs"]
clips = video.wait_jobs(ids)                      # [{"id", "status", "file", "cost", "error"}]
video.recent(5); video.cancel(ids[0])
```

Errors (engine off, over credit, bad input) raise `RuntimeError` with a sentence you can tell the user.

## Writing a prompt yourself (English, always this layout)

```
integrated_multimodal_description: [Shot 1] <subject, look, setting, light, lens>. <action with times, e.g. "at 00:01.500 she turns her head at fast speed">; the camera <one move + size + speed, e.g. "pushes in with medium amplitude at slow speed">.
overall_soundscape: <the real sounds of the scene>
non_diegetic_music: <music description, or none>
```

- Over 5 s: split into shots, `[Shot 2] At 00:03.500 …`. One camera move per shot.
- No "no X" lists (H3 has no negative prompt). Say what you want instead.
- On-screen text goes in "double quotes", one string at a time.
- With a start picture, spend the words on motion and camera, not looks.
- Save it to `~/work/<name>.txt` and pass the path. Or let `pvideo write` do it.

## Money rules

- Start with a **draft** unless the user asked for the final; offer the final after they like it.
  `--seeds 2` gives two drafts to pick from (the second is cheaper, warm GPU).
- Ask first when `total_high` is above $0.50, or when the user did not ask for a video in this message.
- The owner's Modal stop line is enforced by the engine. If a call refuses, tell the user the reason.
- Never retry a failed job more than once, never loop, never run many films in parallel unasked.

## Other rules

- H3 outputs may not be used in the EU, US, UK or South Korea without MiniMax's permission. Say it
  once if the user plans to publish.
- Real people's faces or voices: only with their consent. Ask.
