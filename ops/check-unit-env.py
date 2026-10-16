#!/usr/bin/env python3
"""Prüft alle Environment=-Zeilen in Units/Drop-ins unter einem Verzeichnis (Standard: ops/).
Werte mit Leerzeichen müssen als Environment="NAME=Wert mit Leerzeichen" stehen."""
import re,shlex,sys,pathlib
bad=0
for p in pathlib.Path(sys.argv[1] if len(sys.argv)>1 else "ops").rglob('*'):
    if p.suffix not in ('.conf','.service','.timer','.socket') or not p.is_file(): continue
    for i,l in enumerate(p.read_text(errors='replace').splitlines(),1):
        if not l.startswith('Environment='): continue
        for a in shlex.split(l[len('Environment='):]):
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',a): print(f'{p}:{i}: ungültige Zuweisung {a!r}'); bad=1
sys.exit(bad)
