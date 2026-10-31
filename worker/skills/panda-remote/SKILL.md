---
name: panda-remote
description: "Use remote Panda, up to 5 extra Kaggle machines (each with its own 4 CPUs, ~30 GB), for heavy commands and for whole agent tasks that should not load this machine: full renders, many headless browsers, big ffmpeg/data batches, or a self-contained helper task with its own files. Covers pjob --remote and pjob --remote --agent, what comes back, costs and limits."
triggers: remote panda, remote, second machine, another notebook, offload, heavy render, out of memory, too heavy, parallel machine, remote agent, remote helper, more cpu, more memory
---

# Remote Panda

Remote Panda is a pool of up to 5 extra Kaggle notebooks of this account (r1-r5). Each runs 2 jobs at
once. Panda puts a job on a running notebook with a free slot, or starts a new one (about 1 minute) when
they are full, so up to 10 remote jobs run at the same time; more wait for a free slot. Each notebook
stops itself after 20 minutes with nothing to do, or when the main Panda goes offline. To spread work,
start each independent piece as its own job (say, 5 render chunks as 5 jobs); never wait for one to end
before starting the next. It
has Python, ffmpeg, node, npm and git, but none of the chats, the browser, Kiro Crew tools or the user's
logins. The user sees it in Settings -> Remote Panda, with a Stop button.

Everything goes through `pjob` (read the `panda-jobs` skill first), so `--notify`, the "Haiku steered"
note, `pjob log` and `pjob stop` work as for local jobs. Always use `--notify` and end your turn instead of
polling.

## 1. A heavy command (no credits)

```bash
pjob start fullrender --remote --files ~/work/thinks/full --notify --want "frames done, output path, errors" \
  --sh 'cd ~/work/thinks/full && node render.mjs'
```

- `--files`: what the command needs, under `~`; it lands at the same paths there. Limit 3 GB packed;
  send only what is needed (no node_modules if `--setup` can install them).
- `--out`: what to bring back (default: the `--files` paths). Only files created or changed come back.
- `--setup 'pip install -q x'`: an install step, run once per remote start.

## 2. A whole agent task (uses Kiro credits)

```bash
pjob start research-x --remote --agent --model claude-sonnet-5.5 --effort medium \
  --files ~/work/proj/data --notify --want "the result and the files it made" \
  --sh 'Read the CSVs in ~/work/proj/data, compute monthly totals per region, plot them, save PNGs.'
```

- An agent (kiro-cli, headless, all tools trusted) does the task there. It cannot ask questions, so the
  task text must be complete: inputs, the exact result wanted, file names.
- Its results come back in `~/work/remote/NAME/`, with `REPORT.md` (what it did, files made, failures),
  plus any changed files under `--files` / `--out`. Read `REPORT.md` first and check the result yourself.
- Pick the model for the job: `claude-haiku-5.5` + `low` for simple mechanical work, `claude-sonnet-5.5`
  + `medium` (default) for normal work, Opus only when the task needs hard reasoning. Credits come from the
  same plan as this chat.
- **Cost:** a remote agent costs no more than a local helper on the same model and effort (measured: the
  same task took 0.32 credits remote, 0.66 as a local helper, Sonnet / medium). What makes a run expensive
  is the model and effort you pick (Opus helpers at `xhigh`/`max` used 74-216 credits each on 31 Oct,
  Sonnet helpers 6-27). Use Opus `xhigh`/`max` only for the hardest part, never as the default for a batch. The job log
  ends with `[remote] agent used N credits`.
- **Files that come later:** `pjob send NAME PATH...` sends more files while the agent works (same
  paths there). Say in the task text which file will come and that it should wait for it, for example
  "the narration arrives later at ~/work/x/voice.wav: wait for it, then mix it in".
- **Follow-ups:** when the agent has finished, `pjob continue NAME --notify --want '...' -- 'NEXT STEP'`
  gives it the next step. It keeps its whole conversation (saved in `~/work/remote/NAME/.session/`) and
  its earlier results, on whichever notebook is free; the `--files` it had are sent again. Use this
  instead of starting a new remote agent that has to learn everything again.
- For a helper that needs this chat's context, the browser or Kiro Crew tools, use `spawn_run` instead.

## When to use it

- Use it when a step is heavy for minutes (CPU or memory), when this machine is busy (`free -m`, load),
  or after Panda's guard stopped a command for memory. Two heavy jobs run on each notebook at once, on up to 5 notebooks.
- Keep quick commands local: sending files takes time (about 1-2 min per 100 MB there and back).
- If the remote can't start, the job ends with a `[remote]` line saying why; run it locally in smaller
  pieces and tell the user.
