# Penrose diagrams

Penrose can render constraint-based mathematical diagrams from a domain, style
and substance specification. Use it when the diagram benefits from explicit
relationships and automatic constraint solving.

Author your own specification or supply one with suitable reuse permission.
No third-party diagram example is bundled with video-use. Keep attribution and
license notices with any externally sourced diagram files.

```bash
python helpers/render_illustration.py penrose edit/diagram/figure.trio.json -o edit/diagram/figure.svg
```

Inspect the resulting SVG for layout and correctness, then use it as an asset in
the chosen video workflow. The helper checks inputs and invokes the optional
Penrose tooling; it does not establish mathematical correctness or reuse rights.

Official documentation: https://penrose.cs.cmu.edu/docs/ref/using
