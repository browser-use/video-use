# Manim Video Skill

Production pipeline for mathematical and technical animations using [Manim Community Edition](https://www.manim.community/).

## What it does

Creates original semantic animated videos from text prompts. The agent handles
research, narration, the required visual plan, chapter authoring, targeted
preview, rendering, composition, and complete-video review.

## Use cases

- **Concept explainers** — "Explain how neural networks learn"
- **Equation derivations** — "Animate the proof of the Pythagorean theorem"
- **Algorithm visualizations** — "Show how quicksort works step by step"
- **Data stories** — "Animate our before/after performance metrics"
- **Architecture diagrams** — "Show our microservice architecture building up"

## Prerequisites

Python 3.10+, Manim CE (`pip install manim`), LaTeX, ffmpeg.

```bash
bash skills/manim-video/scripts/setup.sh
```

Original explainers use `assets/teaching.py` for persistent named objects,
linked values, attention choreography, and chapter continuity. The broad
`assets/domains/` kit supplies style-neutral math, AI, systems, physics, biology,
finance, and business components without constraining custom Manim code.
