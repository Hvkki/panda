---
name: panda-issue
description: "Report a problem with Panda itself (a broken skill or command, a tool that errors, a missing ability, a UI bug the user describes) to the owner's Developer tab, where they decide whether to fix it. Use instead of working around or editing Panda's code."
triggers: panda bug, report issue, report a problem, skill broken, tool error, panda is broken, missing feature, tell the developer
---

# Report a Panda issue

When Panda itself gets in the way of the task, file a short report. The owner sees it in
**Developer -> Issues** and can press **Accept**, which sends it to the Developer chat to fix.

```bash
panda-issue "PDF maker drops Ukrainian letters" \
  --details "make_doc.py --pdf on a Ukrainian file: every Cyrillic letter is a black box. Tried --docx: fine. Error: none, output wrong."
panda-issue "pvideo wait never returns" --details "..." --severity high
panda-issue --list
```

`panda-issue` is an installed command: call it as is. Never define a function or alias for it.

## When to report

- A Panda skill or command fails or gives wrong output (`pb`, `drive`, `pvideo`, `make_doc.py`, image tools).
- An ability the user wanted is missing, and a Panda change would add it.
- The user says something in the app is broken (a button, a screen, the chat bar).

Do not report: website errors, the user's own code, normal "no results", or something you fixed in
this turn. One report per problem; repeats within a day are skipped.

## How to write it

- Title: what is broken, in one line.
- Details: the command you ran, the exact error text, what you tried, and what the user wanted.
  Never include API keys, passwords, tokens or the user's private data.
- Still do the best you can for the user in this turn, and tell them in one line that you reported it.
- Never try to fix Panda's own code yourself outside the Developer chat.
