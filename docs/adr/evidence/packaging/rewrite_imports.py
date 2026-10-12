"""Prototype: rewrite in-package absolute imports (app.modules.* / modules.*) to relative imports.

Only `from <abs> import ...` statements inside app/ are rewritten; the text after `import`
is left byte-identical. Prints a summary and exits non-zero on any unhandled form.
"""
import ast, re, sys
from pathlib import Path

root = Path(sys.argv[1])
app = root / "app"
changed = 0
for path in sorted(app.rglob("*.py")):
    src = path.read_text()
    tree = ast.parse(src)
    pkg = list(path.relative_to(root).parent.parts)  # e.g. ['app','modules','f2w_agent']
    if pkg[:2] != ["app", "modules"]:
        print(f"skip (not in app.modules package): {path.relative_to(root)}")
        continue
    lines = src.splitlines(keepends=True)
    edits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(("app.modules", "modules.")):
                    sys.exit(f"unhandled plain import {path}:{node.lineno}")
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        mod = node.module
        if mod == "app.modules" or mod.startswith("app.modules."):
            target = ["app"] + mod.split(".")[1:]
        elif mod.startswith("modules."):
            target = ["app"] + mod.split(".")
        else:
            continue
        # relative form: climb from current package to common prefix
        common = 0
        while common < min(len(pkg), len(target)) and pkg[common] == target[common]:
            common += 1
        if common < 2:  # must stay inside app.modules
            sys.exit(f"import leaves app.modules: {path}:{node.lineno} {mod}")
        dots = "." * (len(pkg) - common + 1)
        rel = dots + ".".join(target[common:])
        edits.append((node.lineno, mod, rel))
    for lineno, mod, rel in edits:
        line = lines[lineno - 1]
        new, n = re.subn(rf"\bfrom {re.escape(mod)} import\b", f"from {rel} import", line, count=1)
        if n != 1:
            sys.exit(f"could not rewrite {path}:{lineno}: {line!r}")
        lines[lineno - 1] = new
    if edits:
        path.write_text("".join(lines))
        changed += len(edits)
        print(f"{path.relative_to(root)}: {len(edits)}")
print("rewritten:", changed)
