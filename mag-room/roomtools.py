#!/usr/bin/env python3
"""Host-side helpers for the MAG room: text-to-speech and image OCR.

Run with the room tools virtualenv python:
  roomtools.py tts "<text>" /path/out.mp3
  roomtools.py ocr /path/image.png     # writes recognised text to stdout
"""
import sys


def do_tts(text, out):
    from gtts import gTTS
    text = (text or '').strip()
    if not text:
        raise SystemExit('leerer Text')
    gTTS(text=text, lang='de').save(out)


def do_ocr(path):
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()
    result, _ = engine(path)
    lines = [row[1] for row in (result or []) if len(row) > 1 and row[1]]
    sys.stdout.write('\n'.join(lines))


def main(argv):
    if len(argv) < 2:
        raise SystemExit('usage: roomtools.py tts|ocr ...')
    action = argv[1]
    if action == 'tts':
        do_tts(argv[2], argv[3])
    elif action == 'ocr':
        do_ocr(argv[2])
    else:
        raise SystemExit('unbekannte Aktion: %s' % action)


if __name__ == '__main__':
    main(sys.argv)
