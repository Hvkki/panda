---
name: panda-jobs
description: "Run long commands (renders, GPU/Modal jobs, installs, downloads, transcription, builds, big scripts) in the background with pjob, so you can work in parallel or wait in short checks instead of one blocking step. Use for any command that may take more than about a minute."
triggers: long command, background, run in background, parallel, wait for, render, install, download, modal run, ffmpeg, training, transcribe, build, takes a while
---

# Background jobs with pjob

A step that blocks for 20 minutes looks dead to the user and can't be checked or stopped.
Start long work as a **job**, then either work on something else or wait in short slices.

```bash
pjob start render -- modal run h3_batch.py::batch --jobs jobs.json
pjob start vo --sh 'cd ~/work/tts && python3 make_vo.py --takes 4'
pjob status render            # running / done / failed (exit code) / stopped / lost, plus the last log lines
pjob wait render --max 240    # blocks at most 240 s (cap 1500), then prints the status
pjob wait-any vo render       # returns when the first one ends
pjob log render -n 60
pjob stop render
pjob list
```

`pjob` is an installed command: call it as is. Never define a function or alias for it.

## When to use it

- Anything that may run longer than about a minute: model downloads, GPU/Modal runs, ffmpeg on long
  media, pip/npm installs, transcription, big scrapes, test suites, training.
- Two or more independent long commands: start each as its own job so they run at the same time.

Quick commands (under ~30 s) stay normal steps.

## Two ways to use the time

1. **Work in parallel.** Start the job, then do the independent parts (write the script, prepare the
   next prompt, research, draft the answer). Check with `pjob status NAME` between steps.
2. **Sleep with it.** When nothing else can be done, `pjob wait NAME --max 240` in a loop. Every
   check is a fresh step, so the user sees progress, and you read the log tail each time: if it shows an
   error or is stuck (no new lines for several checks), stop it and fix the cause instead of waiting on.

Pick `--max` to fit the job: short jobs 60-120 s, long renders 240-600 s.

## Rules

- Write output files and progress into the log as the job goes (print progress, save results step by
  step), so a long job can be checked and a cut-off one can resume.
- Before you answer, every job you started must be done, stopped, or clearly handed over ("the render
  is still running; I'll check it in my next message"). Never leave a silent job behind.
- After Panda restarts, running jobs show as **lost**: their process is gone, but their logs and the
  files they wrote are still there. Check what finished, then start only the missing part again.
- Jobs share the machine: don't start more than 3 heavy ones at once.
