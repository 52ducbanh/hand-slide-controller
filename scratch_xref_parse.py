"""Parse PyInstaller xref to find what imports matplotlib, sounddevice, PIL."""
import re

with open(r"build\HandSlideController\xref-HandSlideController.html", encoding="utf-8", errors="replace") as f:
    content = f.read()

# Find importer-of entries for each suspect package
targets = ["matplotlib", "sounddevice", "PIL", "pillow", "_sounddevice"]
# Simple text scan - find anchor name sections
lines = content.split("\n")

for target in targets:
    # Find all lines mentioning the target and extract context
    found = []
    for i, line in enumerate(lines):
        if target.lower() in line.lower():
            found.append(i)
    if found:
        print(f"\n=== {target} found in {len(found)} lines ===")
        # Show first few contexts
        for idx in found[:8]:
            ctx = " ".join(lines[max(0,idx-1):idx+2]).strip()
            if len(ctx) > 200:
                ctx = ctx[:200]
            print(f"  L{idx}: {ctx}")
    else:
        print(f"\n=== {target}: NOT FOUND in xref ===")
