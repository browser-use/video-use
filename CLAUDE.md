# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é este repositório

`video-use` é uma skill do Claude Code para editar vídeos por conversa. Este clone (`C:\Users\wallace\Developer\video-use`) está registrado como skill via junção do Windows em `~/.claude/skills/video-use`, então qualquer mudança aqui vale imediatamente para todas as sessões.

- `SKILL.md` — instruções de edição do dia a dia (as 12 Hard Rules, processo, formato do EDL). É o que o agente lê ao editar vídeos; leia antes de mexer nos helpers.
- `install.md` — instalação inicial / reconexão.
- `helpers/` — scripts Python invocados direto (`python helpers/<nome>.py`); não há pacote nem console scripts.
- `skills/manim-video/` — sub-skill vendorizada, usada só para slots de animação Manim.

## Ambiente (Windows)

- Python 3.10 do sistema (sem `uv`, sem venv). Deps: `python -m pip install -e .` (requests, librosa, matplotlib, pillow, numpy).
- `ffmpeg`/`ffprobe` 6.0 já estão no PATH.
- **Defina `PYTHONUTF8=1`** ao rodar helpers e testes: eles imprimem `→` e o console cp1252 do Windows lança `UnicodeEncodeError` sem isso.
- Chaves da ElevenLabs (`ELEVENLABS_API_KEY`) e do Gemini (`GEMINI_API_KEY`) em `.env` na raiz do repo (ignorado pelo git). `.env.example` é só o template versionado — nunca coloque chaves nele. `transcribe.load_api_key(name)` procura `<repo>/.env`, depois `./.env`, depois a variável de ambiente (padrão: `ELEVENLABS_API_KEY`).

## Comandos

```bash
# Todos os testes (unittest puro, sem pytest)
PYTHONUTF8=1 python -m unittest discover -s tests

# Um arquivo / uma classe / um teste
PYTHONUTF8=1 python -m unittest tests.test_render_fps
PYTHONUTF8=1 python -m unittest tests.test_render_fps.ParseFpsTests
PYTHONUTF8=1 python -m unittest tests.test_render_captions.ChunkWordsTests.test_real_take
```

Não há linter nem build configurados. Os testes carregam `helpers/render.py` via `importlib` (os helpers não são um pacote importável) e fazem mock de `subprocess`, então não chamam ffmpeg nem a API.

Transcrição (`transcribe.py`, `transcribe_batch.py`) gasta créditos pagos do Scribe, e `visual_notes.py` gasta créditos do Gemini — não rode para testar sem o usuário pedir.

## Arquitetura

Pipeline: `transcribe` → `pack_transcripts` → LLM decide cortes → `edl.json` → `render` → self-eval com `timeline_view` no vídeo renderizado.

- **Todas as saídas vão para `<videos_dir>/edit/`**, nunca para dentro deste repo (o `.gitignore` tem uma rede de segurança para esses nomes).
- `transcribe.py` extrai áudio mono 16 kHz com ffmpeg, envia ao Scribe (verbatim, diarização, eventos de áudio, timestamps por palavra) e cacheia o JSON bruto em `edit/transcripts/<stem>.json`; se o arquivo existe, pula. `transcribe_batch.py` importa `transcribe` como módulo irmão (por isso depende de rodar a partir de `helpers/` no `sys.path`) e roda 4 workers.
- `pack_transcripts.py` agrupa palavras em frases (quebra em silêncio ≥ 0,5 s ou troca de speaker) → `edit/takes_packed.md`, a principal visão que o LLM lê.
- `render.py` é o núcleo e codifica as Hard Rules de `SKILL.md`, nesta ordem: extrai cada segmento com grade + fades de áudio de 30 ms → concat sem perdas (`-c copy`) em `base.mp4` → um filtergraph com overlays deslocados por `setpts=PTS-STARTPTS+T/TB` e **legendas por último** → loudnorm em dois passos. Importa `get_preset`/`auto_grade_for_clip` de `grade.py` (mesmo diretório, com fallback se o import falhar). Detecta fonte HDR e retrato (incluindo rotação em side data) e resolve um único fps a partir da primeira fonte, salvo `--fps`. Flags: `--preview`, `--draft`, `--build-subtitles`, `--no-subtitles`, `--no-loudnorm`, `--fps`.
- O SRT mestre usa tempo da timeline de saída: `word.start - segment_start + segment_offset`. O chunking (`chunk_words`) e o estilo (`SUB_FORCE_STYLE`) ficam em `render.py`.
- `grade.py`: modo auto por padrão (correção sutil limitada a ±8%, sem look criativo), presets (`warm_cinematic`, `neutral_punch`, `none`) ou `--filter` com cadeia ffmpeg crua.
- `visual_notes.py` (opcional): gera proxy 360p / 1 fps com ffmpeg, divide em janelas (`--window`, padrão 900 s), sobe cada uma pela Files API do Gemini (REST via `requests`, sem SDK), pede JSON estruturado (resumo + notas `MM:SS` relativas à janela), desloca para o tempo da fonte e cacheia em `edit/visual/<stem>.json`; toda execução reconstrói `edit/visual_notes.md` a partir do cache. Importa `pack_transcripts`, `transcribe` e `transcribe_batch` como módulos irmãos. Tempos valem ±1 s: servem para achar momentos, nunca como borda de corte.
- `timeline_view.py`: PNG com filmstrip + waveform + palavras + silêncios para um intervalo; é ferramenta de inspeção pontual, não de varredura.

Ao mudar o comportamento de render, mantenha `SKILL.md` coerente com o código: o agente de edição segue o documento, não o código.
