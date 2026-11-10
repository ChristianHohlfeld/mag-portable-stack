#!/usr/bin/env python3
"""The photo check in front of the GPU stage (runner.check_photo): a whole
photo passes; one that lost a few rows at the bottom is tolerated; one that
ends in its middle is refused in words before 20 minutes are spent on it."""
import contextlib
import io
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from PIL import Image, ImageFile
except ImportError:  # the check itself runs in the Idol venv, which has PIL
    print("photo check skipped: no PIL here")
    sys.exit(0)
import runner  # noqa: E402


def refused(path):
    """What check_photo says when it ends the stage, or None when it lets the photo pass."""
    said = io.StringIO()
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with contextlib.redirect_stdout(said):
            runner.check_photo(str(path))
    except SystemExit as stop:
        assert stop.code == 2, stop.code
        lines = [line for line in said.getvalue().splitlines() if line.startswith("MAG-ERROR ")]
        assert len(lines) == 1, said.getvalue()
        return lines[0][len("MAG-ERROR "):]
    return None


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="photo-"))
    whole = tmp / "ganz.jpg"
    image = Image.new("RGB", (640, 480))
    image.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256) for y in range(480) for x in range(640)])
    image.save(whole, "JPEG", quality=92)
    data = whole.read_bytes()
    assert refused(whole) is None and ImageFile.LOAD_TRUNCATED_IMAGES is False       # nothing to tolerate
    (tmp / "ende.jpg").write_bytes(data[:-2])                                        # only the end mark is missing
    assert refused(tmp / "ende.jpg") is None and ImageFile.LOAD_TRUNCATED_IMAGES is True
    (tmp / "halb.jpg").write_bytes(data[:len(data) // 2])
    why = refused(tmp / "halb.jpg")
    assert why and why.startswith("Das Foto ist unvollständig angekommen: unten fehlen etwa ") and "%" in why, why
    (tmp / "kein.jpg").write_bytes(b"das ist kein Bild")
    assert refused(tmp / "kein.jpg") == "Das ist kein Bild, das sich lesen lässt (JPEG, PNG oder WebP)."
    print("photo check ok")


if __name__ == "__main__":
    main()
