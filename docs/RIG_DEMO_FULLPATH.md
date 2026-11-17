# MAG: beschädigtes Mesh → Rekonstruktion → Rig → animiertes GLB

**Status: isolierter Proof of Concept, nicht produktiv aktiviert.** Diese Demo
ist an die vorhandene MAG-`scene.py` angebunden. Sie hat *keine* Schreibzugriffe
auf den Live-Chat, keinen Zugriff auf die MI50, keine Downloads und installiert
keine Modelle. Blender nutzt ausschließlich MAGs CPU-Kerne 28–55. `--film`
ist optional, weil der echte MAG-Cycles-Renderer erheblichen CPU-Aufwand hat.

## Start auf MAG

Von der Wurzel des `mag-portable-stack`-Repositories:

```bash
taskset -c 28-55 python3 ops/scene/demo_fullpath.py --out /tmp/mag-rig-demo
# Optional mit fertigem Film (Original-MAG-Renderer):
taskset -c 28-55 python3 ops/scene/demo_fullpath.py --out /tmp/mag-rig-demo --film
# Alternativ mit eigenem, grundsätzlich aufrechten, menschenähnlichen GLB:
taskset -c 28-55 python3 ops/scene/demo_fullpath.py --input /pfad/figur.glb --out /tmp/mein-rig-test
```

`--blender /pfad/zum/blender` überschreibt das in MAG dokumentierte
`/opt/mag-scene/blender/blender` (oder `MAG_SCENE_BLENDER`).

## Was genau durchlaufen wird

1. **Defekt erzeugen oder importieren:** Die Standarddemo erzeugt eine Figur
   mit getrennten Mesh-Inseln, einer offenen Hinterkopfoberfläche, fehlendem
   rechten Schienbein und ohne Finger, Mundöffnung oder Augenlider. Sie wird
   unverändert als `damaged.glb` exportiert.
2. **Begrenzte Reparatur:** Für diesen menschenähnlichen frontalen Test wird
   gültige linke Geometrie geometrisch gespiegelt und das Gesamtergebnis zu
   einem geschlossenen Voxel-Proxy verschmolzen. Dies ist eine **Annahme**
   (Symmetrie), keine Erkennung der fehlenden rechten Geometrie.
   `repaired.glb` zeigt genau diese inferierte Ersatzoberfläche.
3. **Original-MAG:** Das unveränderte `ops/scene/scene.py` übernimmt das
   reparierte GLB, ruft `rig.analyse` und `face.find`/`face.rig` auf, erstellt
   Armature/Weights, Gesicht/Phoneme und Körperbewegungen. `scene.glb`,
   `scene.blend` und `scene.json` werden mit MAGs bestehender Pipeline erzeugt.
4. **Anschau-Overlays:** Die Demo ergänzt 10 einfache animierte Finger sowie
   künstliche Lippen-/Lid-Oberflächen, die an die in Schritt 3 erkannten
   Kopf- und Handknochen gebunden werden. Die Lippen, Blinzeln und Finger
   bekommen echte glTF-Morph-Targets und Keyframes. Das ist ausdrücklich
   **keine** anatomische Innenmund-Rekonstruktion und **kein** hochwertiges
   Finger-IK-Skinning.
5. **Export-/Abnahmetest:** `scene.glb` wird erneut geschrieben. Sein *echter*
   GLB-JSON-Block wird auf Skeleton/Skin, Gesicht-/Hand-Morph-Targets und
   Animationskanäle geprüft. Wenn MAG keine beiden Hände erkennt oder kein
   animiertes Rig exportiert wird, schlägt die Demo fehl statt Erfolg zu melden.
6. **Film optional:** `--film` ruft `scene.py render` auf; das Ergebnis wird
   `result/scene.mp4`. Ohne `--film` sind GLB und BLEND bereits animiert.

## Ergebnisdateien

```text
/tmp/mag-rig-demo/
  damaged.glb          # absichtlich unvollständiges Ausgangs-Mesh
  repaired.glb         # durch Symmetrie/Voxel rekonstruierter Proxy
  spec.json            # tatsächliche MAG-Szenenbeschreibung
  result/
    scene.glb          # animiertes, in Blender importierbares Rig
    scene.blend        # editierbare Datei mit Knochen/Keyframes
    scene.json         # MAG-Darsteller / Sprache / Laufzeit
    demo_report.json   # verifizierte/angenommene Aspekte und Grenzen
    scene.mp4          # nur mit --film
```

## Was damit *nicht* bewiesen ist

- Keine automatische anatomische Klassifikation beliebiger kaputter Figuren.
- Keine plausible Rekonstruktion frei erfundener fehlender Gliedmaßen aus
  einem einzelnen unbekannten Mesh; Spiegelung funktioniert nur bei passender
  Frontalfigur und kann Asymmetrie beschädigen.
- Kein Retopology-/UV-/Texture-Preservation-Test für hochaufgelöste Texturen.
- Augenlider sind künstliche Oberflächen; das Modell hat weder Augapfel-
  Kavitäten noch eine echte Innenmund-Topologie. Finger nutzen einfache Morphs,
  keine einzeln gelernte Fingeranatomie.
- Kein Qualitätsbeweis ohne tatsächlichen Blender-Lauf und Sichtabnahme von
  `scene.glb`/`scene.mp4`. Die statischen Python-Tests prüfen *nur* Syntax,
  Dateipfade und den strukturellen GLB-Abnahmeprüfer.

**Produktionsentscheidung:** Erst anhand mehrerer echter, schwieriger MAG-
GLBs visuell prüfen. Bis dahin kein Live-Deployment und keine Automatik im Chat.
