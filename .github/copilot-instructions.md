# Cost-Saving Instructions for Copilot

## Directories to ignore (build artifacts, not source code)
- Do NOT read, explore, or include files from `VolumeMixer.build/` or its subdirectories.
- Do NOT read, explore, or include files from `VolumeMixer.dist/` or its subdirectories.
- Do NOT read, explore, or include files from `__pycache__/` directories.
- Do NOT read, explore, or include files from `Fixes/` directories.

## Source files only
Focus only on the following kinds of files when planning or analyzing the project:
- `*.py` files in the root folder
- `*.md` files
- `*.txt` configuration files

## Prompting guidelines
- When the user asks a simple question, do NOT trigger an expensive workspace-wide read.
- Prefer to read only the specific files or lines the user references.
