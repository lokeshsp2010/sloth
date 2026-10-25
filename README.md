# Sloth

**Lazy file chores with local open-weight models.** Describe a chore in plain English. A local model writes a tiny script, Sloth shows you the plan, and you press `y` once.

![Sloth demo: type a chore, preview the plan, press y, undo](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/r2ga6vjop26czkx5dlfc.gif)

Built for the Hacktoberfest Open-Source AI Challenge, Week 3: "Slow and Steady". Everything runs locally through [Ollama](https://ollama.com). No cloud, no API key, no per-run cost.

## How it works

1. You type a chore: `python sloth.py "sort everything in Downloads by file type"`
2. A local model writes a short script using a small set of helper functions.
3. The script runs inside a cage and only **records** what it wants to do. It never touches your disk.
4. Sloth shows a preview table. You press `y` to apply, or anything else to cancel.
5. Every applied run goes on an undo stack. `--undo` reverses it.

The idea: **the AI plans, dumb code applies.**

## Requirements

- Python 3.12 (tested on Windows)
- [Ollama](https://ollama.com) running locally with these models:

```
ollama pull llama3.1:8b-instruct-q4_K_M    # writes the script
ollama pull hermes3:3b                     # quick per-file questions
ollama pull nomic-embed-text               # matches files to folders by meaning
```

- Python packages:

```
pip install ollama RestrictedPython rich numpy pypdf docx2txt rapidocr-onnxruntime watchdog
```

Swap models with environment variables: `SLOTH_MODEL`, `SLOTH_ASK`, `SLOTH_EMBED`.

## Usage

```
# do a chore (preview first, press y to apply)
python sloth.py "move PDFs older than 7 days from Downloads into Archive by month"

# show the script the model wrote
python sloth.py "sort everything in Downloads by file type" --show

# save the script as a habit, then rerun it with no model and no typing
python sloth.py "sort everything in Downloads by file type" --save dl
python sloth.py --habit dl

# undo the last run (stackable)
python sloth.py --undo

# compare your local models on one chore (plans only, moves nothing)
python sloth.py --bench "sort everything in Downloads by file type"

# list saved habits
python sloth.py --list
```

`--watch FOLDER --habit NAME` runs a habit when new files land in a folder. It is experimental and not field-tested.

## Helpers the model's script can use

| Helper | What it does |
|---|---|
| `sloth.ls(folder, pattern)` | list files |
| `sloth.age_days(f)`, `sloth.size_mb(f)`, `sloth.month(f)`, `sloth.kind(f)` | file facts |
| `sloth.read(f)` | text inside PDF, Word, text files and images (OCR), all offline |
| `sloth.match(f, labels, other=)` | pick the best label by meaning, using embeddings |
| `sloth.ask(question)` | short answer from a small local model |
| `sloth.move(f, folder)`, `sloth.rename(f, name)`, `sloth.trash(f)` | record a move, rename or trash (nothing is deleted) |

## Safety model (honest version)

- The script runs in [RestrictedPython](https://github.com/zopefoundation/RestrictedPython) with no imports and no `open`.
- Path-writing methods are blocked. The script can only call the helpers above.
- Paths are locked to your home folder.
- The model's script only **plans**. Sloth's own code applies the moves, after you approve.
- Files are never deleted. `trash` moves them to `~/.sloth/trash`.
- Sloth warns if a plan leaves other files in the folder untouched.

This is guardrails, not a security boundary. Do not run untrusted prompts or scripts through it.

State lives in `~/.sloth` (habits, undo history, trash).

## Known limitations

- Small local models fail confidently. They can write partial or wrong scripts. **Always read the preview.**
- `--undo` puts files back but leaves the empty folders Sloth created.
- The "too close to call" threshold for `match()` is untuned. Adjust it with `SLOTH_MARGIN` (default `0.03`).
- `--bench` counts a model as OK if its script planned actions. It does not verify each destination.

## AI disclosure

Built with help from Claude (code and debugging). Testing and decisions were done on my own machine.
