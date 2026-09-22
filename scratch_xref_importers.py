"""Check who imports matplotlib and sounddevice via xref."""
from html.parser import HTMLParser
import re

class XrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {}
        self.current_name = None
        self.current_importers = []
        self.in_importer_section = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("class") == "node":
            if self.current_name:
                self.nodes[self.current_name] = self.current_importers[:]
            self.current_name = None
            self.current_importers = []
            self.in_importer_section = False
        if tag == "a" and attrs.get("name"):
            self.current_name = attrs["name"]
        if tag == "a" and attrs.get("href", "").startswith("#"):
            if self.in_importer_section:
                self.current_importers.append(attrs["href"][1:])

    def handle_data(self, data):
        if "imported by" in data:
            self.in_importer_section = True


with open(r"build\HandSlideController\xref-HandSlideController.html", encoding="utf-8", errors="replace") as f:
    content = f.read()

# Simple approach: find "imported by:" lines after each module anchor
# Find importers of matplotlib, sounddevice

def find_importers(content, module_name):
    """Find which modules import the given module."""
    # Pattern: anchor for module_name, then look for "imported by" and the subsequent hrefs
    # Find the div node section for this module
    pattern = rf'<a name="{re.escape(module_name)}"><\/a>.*?imported by:(.*?)(?=<div class="node"|$)'
    matches = re.findall(pattern, content, re.DOTALL)
    importers = []
    for m in matches:
        # Extract href links
        links = re.findall(r'href="#([^"]+)"', m)
        importers.extend(links)
    return importers

targets = ["matplotlib", "sounddevice", "_sounddevice", "PIL", "mouseinfo", "pyscreeze"]
for t in targets:
    importers = find_importers(content, t)
    print(f"\n{t} imported by:")
    for imp in importers[:10]:
        print(f"  <- {imp}")
    if not importers:
        print("  (no importers found or not present)")
