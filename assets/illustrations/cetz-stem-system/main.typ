#import "@preview/cetz:0.5.2": canvas, draw

#let background = rgb("#050914")
#let panel = rgb("#101827")
#let border = rgb("#34415C")
#let cyan = rgb("#43C7F4")
#let mint = rgb("#48E0A4")
#let amber = rgb("#FFC857")
#let white = rgb("#F5F7FB")

#set page(width: 16cm, height: 9cm, margin: 0cm, fill: background)
#set text(fill: white)

#align(center + horizon)[
  #canvas(length: 1.25cm, {
    import draw: *

    let node(x, label, accent) = {
      rect(
        (x - 1.15, -0.62),
        (x + 1.15, 0.62),
        radius: 0.16,
        fill: panel,
        stroke: (paint: accent, thickness: 1.2pt),
      )
      content(
        (x, 0),
        text(size: 16pt, weight: "bold", fill: accent)[#label],
      )
    }

    node(0, [INPUT], cyan)
    node(3.5, [SYSTEM], amber)
    node(7, [OUTPUT], mint)

    line(
      (1.2, 0),
      (2.25, 0),
      stroke: (paint: cyan, thickness: 1.8pt),
      mark: (end: "stealth", fill: cyan),
    )
    line(
      (4.7, 0),
      (5.75, 0),
      stroke: (paint: mint, thickness: 1.8pt),
      mark: (end: "stealth", fill: mint),
    )

    // A compact response curve: replace it with the STEM-specific relationship.
    line((-0.8, -2.0), (7.8, -2.0), stroke: (paint: border, thickness: 0.8pt))
    line((-0.8, -2.0), (-0.8, -0.9), stroke: (paint: border, thickness: 0.8pt))
    bezier(
      (-0.6, -1.9),
      (7.5, -1.05),
      (2.2, -1.82),
      (3.9, -1.1),
      stroke: (paint: amber, thickness: 2pt),
    )
    circle((3.9, -1.37), radius: 0.10, fill: amber, stroke: none)
  })
]
