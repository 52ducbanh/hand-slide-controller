"""Trace why certain packages end up in the dist."""
import importlib.util, ast, sys

TARGETS = {"PIL", "matplotlib", "tkinter", "sounddevice", "certifi", "ssl"}

def get_top_imports(pkg_name):
    try:
        spec = importlib.util.find_spec(pkg_name)
        if not spec or not spec.origin:
            return []
        src = open(spec.origin).read()
        tree = ast.parse(src)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return sorted(i for i in imports if i in TARGETS)
    except Exception as e:
        return [f"ERROR:{e}"]

pkgs = ["pyautogui", "pyscreeze", "mouseinfo", "pygetwindow", "pymsgbox", "pyperclip", "pytweening"]
for pkg in pkgs:
    hits = get_top_imports(pkg)
    print(f"{pkg}: {hits if hits else 'NONE'}")
