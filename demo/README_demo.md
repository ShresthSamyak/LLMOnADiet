## How to record the demo GIF

**Prerequisites**

- Python 3.11+
- `pip install context-engine`
- fastapi repo cloned: `git clone https://github.com/tiangolo/fastapi.git /tmp/fastapi-bench`

**Record**

1. Open a terminal, set it to ~100×30 columns, dark theme, font size 16.
2. Start your screen recorder (QuickTime, OBS, Kap, asciinema, etc.).
3. Run: `bash demo/record_demo.sh`
4. Stop recording when the script exits.
5. Trim, export as GIF, save to `demo/demo.gif`.

**Tips**

- [asciinema](https://asciinema.org/) + [agg](https://github.com/asciinema/agg) gives the cleanest GIF with zero setup:
  ```bash
  asciinema rec demo/demo.cast -c "bash demo/record_demo.sh"
  agg demo/demo.cast demo/demo.gif
  ```
- Keep the GIF under 3 MB so GitHub renders it inline without clicking.
