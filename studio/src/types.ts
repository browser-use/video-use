/** A single cut segment: a slice of one source, placed in output order. */
export interface Range {
  source: string;
  start: number;
  end: number;
  kind?: string; // "video" (default) | "photo" — real EDLs tag photo stills
  beat?: string;
  quote?: string;
  reason?: string;
}

export interface Overlay {
  file: string;
  start_in_output: number;
  duration: number;
}

export interface Edl {
  version: number;
  sources: Record<string, string>;
  ranges: Range[];
  grade?: string;
  overlays?: Overlay[];
  subtitles?: string;
  total_duration_s?: number;
}

/** ElevenLabs Scribe word entry (type: "word" | "spacing" | "audio_event"). */
export interface Word {
  text: string;
  start: number;
  end: number;
  speaker_id?: string;
  type?: string;
}

export interface Transcript {
  words: Word[];
}

/** One appended line of edit_log.jsonl. Ops are open vocabulary. */
export interface EditLogEntry {
  ts: string;
  op: string;
  segment?: number;
  [key: string]: unknown;
}
