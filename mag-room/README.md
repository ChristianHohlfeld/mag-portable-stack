# MAG Room

The production room is native C++17. See the [stack README](../README.md).

- `native/`: room, CDP browser and VM lifecycle binaries.
- `web/`: default frontend.
- `static/`: locally served Markdown/rendering assets.
- `tests/`: isolated native regression tests.

Build/deploy once from the repository root through `CMakeLists.txt` and `ops/`.
There is no separate Python server or alternate room installer.
