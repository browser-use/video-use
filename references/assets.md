# Still assets and webpage evidence

These helpers acquire still images, capture webpages and prepare transparent
image cards. They do not compose a timeline or bundle third-party media, logos,
emoji artwork or fonts. Downloading or rendering an asset does not establish
permission to use it.

## Acquire an image

```sh
python helpers/fetch_asset.py image https://example.com/photo.png \
  -o edit/assets/photo.png --max-width 1600 --rights "project supplied declaration" --credit "source credit"
python helpers/fetch_asset.py logo github -o edit/assets/logo.png --color 181717 --size 512
python helpers/fetch_asset.py emoji "👉" -o edit/assets/point.png --size 240
```

Image requests accept HTTP or HTTPS URLs. Downloads stop at 40 MiB and must
successfully decode as a still image. Animated images are rejected. PNG, JPEG
and WebP outputs are re-encoded to match the requested extension and can be
scaled down. This can change compression, metadata and color handling; it is
not an archival copy of the original file.

Logo requests use the Simple Icons CDN by slug, with a 2 MiB download limit.
SVG output retains the downloaded vector; PNG output also retains its SVG source.
PNG rasterization requires CairoSVG or a local Chrome/Chromium browser. The CDN
is not pinned; record and review the fetched content for each project.

Emoji rendering uses an installed platform font, or --font with a supplied file.
Glyph coverage and complex sequence shaping depend on the local font and Pillow
build. Inspect the output. No font is downloaded, bundled or licensed by this helper.

Each asset has a neighboring .json sidecar with its source, creation time,
content hash and relevant dimensions. Image downloads retain requested and final
URLs. Rights notes are declarations, never verification: rights_verified is false.
Review the specific source, font and trademark terms for the intended use.

## Capture a webpage

```sh
python helpers/web_shot.py capture https://example.com \
  -o edit/assets/page.png --width 1280 --height 800 --scale 2
python helpers/web_shot.py capture https://example.com \
  -o edit/assets/article.png --selector article
```

Capture accepts HTTP, HTTPS and local file URLs and writes PNG. It selects
Playwright when installed, otherwise a local Chrome-family browser. --chrome
forces the CLI backend; VIDEO_USE_CHROME selects a browser executable. Playwright
requires its Chromium installation. No browser dependency is installed implicitly.

--selector and --full-page require Playwright. The Chrome CLI captures only the
viewport and rejects those options. A selector chooses the first matching element;
when both options are present the element screenshot takes precedence. Chrome
uses a temporary browser profile, not the user's signed-in session.

The sidecar records backend, requested viewport, actual image dimensions, time,
source URL and hash. Playwright additionally records page title and final URL.
Capture is a point-in-time view: animation timing, lazy loading, cookie dialogs,
network failures and font loading may affect results. Inspect the actual image.
The standalone browser is not a sandbox for untrusted URLs or local HTML.

## Prepare a card

```sh
python helpers/web_shot.py card edit/assets/page.png \
  -o edit/assets/page_card.png --crop 0,0,1000,600 \
  --max-width 800 --radius 24 --shadow --rotate -4
```

Crop coordinates are x,y,width,height in source pixels, or fractions when every
value is between zero and one. Treatment order is crop, optional uniform-border
trim, downscale, corner rounding, border, shadow and rotation. Shadow and rotation
expand the final canvas beyond the requested content size. Output is RGBA PNG.
The sidecar records source hash, treatment settings and any source provenance.
Malformed source provenance is rejected rather than silently discarded.

## Output protection and limits

Use new filenames. Existing assets, sidecars, SVG companions and symbolic links
are rejected before acquisition. Work is staged in a temporary directory and only
published after the asset and metadata are ready. Publication uses exclusive file
creation, so a destination created during acquisition is not overwritten. Normal
publication errors remove files created by that attempt.

The multi-file publication is not one atomic filesystem transaction; interruption
or disk failure may leave an incomplete set. Inspect it and retry with a fresh name.
No automatic caching or resumption is provided. Stored URLs may contain query
parameters; inspect project metadata before sharing it.

Requests and Pillow are existing dependencies. Browser capture, SVG rasterization
and emoji rendering require their optional local tools. This PR does not change
video sourcing, source evidence tracking or timeline effect helpers. Tests use
synthetic images and mocked downloads; a real Chrome capture of an original local
page was also checked. Live external downloads and Playwright capture remain
separate integration checks.
