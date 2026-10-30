---
name: panda-keys
description: "Save API keys from a file the user uploaded (env.txt, key.json, a token file) into the user's vault, Customize -> API keys, so they survive every restart and reach every chat. Use instead of writing keys into ~/.panda/secrets.env or a private file in ~/work."
triggers: save api key, store key, add key to vault, uploaded key file, env file, api keys, secrets
---

# Save API keys to the vault

`~/.panda/secrets.env` is rewritten from the vault at every start, so a key written there by hand is
gone after the next restart. `panda-key` puts it in the vault itself (Customize -> API keys). It is an
installed command: call it as is, never define a function or alias for it.

```bash
panda-key fields ~/uploads/env.txt                       # which NAMES the file holds (never values)
panda-key add MODAL_TARASLOMBA_TOKEN_ID --from ~/uploads/env.txt --field MODAL_TOKEN_ID
panda-key import ~/uploads/env.txt --only GROQ_API_KEY,HF_TOKEN --delete-source
panda-key add OPENROUTER_API_KEY --from ~/uploads/openrouter.txt --replace   # a file holding only the key
panda-key list                                            # names already in the vault
```

- Then use it as `$NAME` inside the command that needs it: `source ~/.panda/secrets.env && ...`.
- Values are never printed; the owner gets a bell note naming the keys that were saved.
- An existing key is kept unless you pass `--replace` (say so to the user when you replace one).
- Take only the keys the task needs, and pass `--delete-source` (or delete the upload) afterwards.
- Names: A-Z, 0-9 and `_`, starting with a letter. Developer variables (`DEV__...`) cannot be set here.
- "did not pick this up": Panda needs one Stop and Start to know this command.
