---
name: panda-drive
description: "Save, find and load big files in the user's Google Drive (zips, videos, image sets, datasets, backups). Use when a file is large, must be kept long-term, or the user mentions Drive."
triggers: google drive, drive, upload to drive, save to drive, backup, zip, large file, big file, dataset, archive
---

# Panda Drive

The user's own Google Drive, under the folder **Panda**. Panda can only see files it created there.

```bash
drive put ~/outputs/photos.zip projects/tuscany   # any size; prints the Drive link
drive ls  projects/tuscany
drive find tuscany
drive get photos.zip                              # back into ~/outputs
drive link photos.zip
drive usage
```

`drive` is an installed command: call it as is. Never define a `drive()` shell function or alias —
that command can't be auto-approved and the step hangs.


- Files in `~/outputs/` bigger than 45 MB are copied to Drive automatically (folder `Panda/outputs`)
  and shown to the user as Drive cards; you don't need to upload those yourself.
- Use Drive for anything the user wants to keep, anything large, and anything that must survive
  after the worker stops. Small deliverables (PDF, DOCX, images) can just stay in `~/outputs/`.
- Give the user the Drive link from the output; never paste credentials.
