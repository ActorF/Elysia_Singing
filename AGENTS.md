# Elysia Singing source documentation rules

These rules apply to every new or modified human-authored source file.

- Begin each source module with an accurate description of its purpose. Python uses a module docstring; JavaScript/TypeScript use a leading `/** ... */` block; CSS uses a leading comment; HTML places the purpose comment immediately after its doctype. PowerShell uses block-form comment-based help with a non-empty `.SYNOPSIS` after only an optional BOM, shebang, or `#Requires` lines.
- Document every public class, function, and method with a docstring or JSDoc explaining its contract. Python names without a leading underscore are public, including test entry points and test-double methods. JavaScript/TypeScript exported declarations and methods or function-valued properties on exported classes/interfaces are public; underscore prefixes do not make class members private. PowerShell functions/filters are public unless prefixed with `_` or explicitly scoped `private:`; public functions require adjacent block-form help with a non-empty `.SYNOPSIS` immediately before the definition or first in the body.
- Explain how complex algorithms work and why they were chosen. Document non-obvious architecture, security, concurrency, persistence, compatibility, rollback, and edge-case decisions. Private helpers are included in this requirement.
- Use `TODO` and `FIXME` only for concrete missing behavior or defects, with the condition for resolution. Do not narrate ordinary assignments, branches, loops, or direct calls.
- Keep explanations accurate for this standalone CMD application. Historical private helpers retained from Elysia_AI must be identified as inherited where their original architecture differs from the active source-conversion pipeline.

The scope includes Python (`.py`, `.pyi`, `.pyw`), JavaScript-family files including JSX, TypeScript/TSX, PowerShell scripts/modules, CSS, and HTML/HTM. Markdown is already documentation. JSON, lockfiles, schemas, binaries, vendored dependencies, generated output, caches, model/runtime assets, and runtime user data are exempt. Local assets under `models/cache/` and generated data under `workspace/` are not maintained source.

Run these checks from the project root before completing a change, using development Python 3.12 or newer:

```text
python scripts/check_python_documentation.py
python -m unittest discover -s tests -v
```

This project has no desktop JavaScript application, so the original repository's `desktop/npm run docs:check` does not apply here. The documentation audit checks structure; also review explanations for accuracy and usefulness. Tests do not establish generated audio quality or replace a real runtime conversion when inference behavior changes.
