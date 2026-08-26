import Foundation

// EDL data model — mirrors SKILL.md "EDL format" and studio/src/types.ts. Real EDLs carry
// undocumented keys (e.g. range "kind", agent-added fields, future top-level keys); every
// struct keeps an `extra` catch-all so a load -> edit -> save round-trip never strips them.

/// A JSON value of unknown shape — used to round-trip fields we don't model.
enum JSONValue: Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])
}

extension JSONValue: Codable {
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([JSONValue].self) {
            self = .array(a)
        } else if let o = try? c.decode([String: JSONValue].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null:            try c.encodeNil()
        case .bool(let b):     try c.encode(b)
        case .number(let n):   try c.encode(n)
        case .string(let s):   try c.encode(s)
        case .array(let a):    try c.encode(a)
        case .object(let o):   try c.encode(o)
        }
    }
}

/// A CodingKey that accepts any string, so we can read/write unmodeled keys.
struct DynamicKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ s: String) { stringValue = s }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

/// One cut segment: a slice of a single source, placed in output order.
struct Range: Equatable {
    var source: String
    var start: Double
    var end: Double
    var kind: String?     // "video" (default) | "photo"
    var beat: String?
    var quote: String?
    var reason: String?
    var extra: [String: JSONValue] = [:]

    var duration: Double { max(0, end - start) }
}

extension Range: Codable {
    private static let known: Set<String> = ["source", "start", "end", "kind", "beat", "quote", "reason"]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DynamicKey.self)
        source = try c.decode(String.self, forKey: DynamicKey("source"))
        start = try c.decode(Double.self, forKey: DynamicKey("start"))
        end = try c.decode(Double.self, forKey: DynamicKey("end"))
        kind = try c.decodeIfPresent(String.self, forKey: DynamicKey("kind"))
        beat = try c.decodeIfPresent(String.self, forKey: DynamicKey("beat"))
        quote = try c.decodeIfPresent(String.self, forKey: DynamicKey("quote"))
        reason = try c.decodeIfPresent(String.self, forKey: DynamicKey("reason"))
        extra = try decodeExtra(c, known: Self.known)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: DynamicKey.self)
        try c.encode(source, forKey: DynamicKey("source"))
        try c.encode(start, forKey: DynamicKey("start"))
        try c.encode(end, forKey: DynamicKey("end"))
        try c.encodeIfPresent(kind, forKey: DynamicKey("kind"))
        try c.encodeIfPresent(beat, forKey: DynamicKey("beat"))
        try c.encodeIfPresent(quote, forKey: DynamicKey("quote"))
        try c.encodeIfPresent(reason, forKey: DynamicKey("reason"))
        try encodeExtra(extra, into: &c)
    }
}

/// A rendered animation clip placed on the output timeline.
struct Overlay: Equatable {
    var file: String
    var start_in_output: Double
    var duration: Double
    var extra: [String: JSONValue] = [:]
}

extension Overlay: Codable {
    private static let known: Set<String> = ["file", "start_in_output", "duration"]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DynamicKey.self)
        file = try c.decode(String.self, forKey: DynamicKey("file"))
        start_in_output = try c.decode(Double.self, forKey: DynamicKey("start_in_output"))
        duration = try c.decode(Double.self, forKey: DynamicKey("duration"))
        extra = try decodeExtra(c, known: Self.known)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: DynamicKey.self)
        try c.encode(file, forKey: DynamicKey("file"))
        try c.encode(start_in_output, forKey: DynamicKey("start_in_output"))
        try c.encode(duration, forKey: DynamicKey("duration"))
        try encodeExtra(extra, into: &c)
    }
}

/// Live subtitle style. Persisted as top-level "subtitle_style"; render.py honors the same
/// object at export, so these key names (enabled/size/margin_v/uppercase/chunk_words) are load-bearing.
struct SubtitleStyle: Equatable {
    var enabled: Bool
    var size: Double
    var margin_v: Double
    var uppercase: Bool
    var chunk_words: Int

    static let `default` = SubtitleStyle(enabled: true, size: 18, margin_v: 35, uppercase: true, chunk_words: 2)
}

extension SubtitleStyle: Codable {
    enum CodingKeys: String, CodingKey { case enabled, size, margin_v, uppercase, chunk_words }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = SubtitleStyle.default
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? d.enabled
        size = try c.decodeIfPresent(Double.self, forKey: .size) ?? d.size
        margin_v = try c.decodeIfPresent(Double.self, forKey: .margin_v) ?? d.margin_v
        uppercase = try c.decodeIfPresent(Bool.self, forKey: .uppercase) ?? d.uppercase
        chunk_words = try c.decodeIfPresent(Int.self, forKey: .chunk_words) ?? d.chunk_words
    }
}

struct Edl: Equatable {
    var version: Int
    var sources: [String: String]
    var ranges: [Range]
    var grade: String?
    var overlays: [Overlay]?
    var subtitles: String?
    var subtitle_style: SubtitleStyle?
    var total_duration_s: Double?
    var extra: [String: JSONValue] = [:]
}

extension Edl: Codable {
    private static let known: Set<String> = ["version", "sources", "ranges", "grade", "overlays", "subtitles", "subtitle_style", "total_duration_s"]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DynamicKey.self)
        version = try c.decode(Int.self, forKey: DynamicKey("version"))
        sources = try c.decode([String: String].self, forKey: DynamicKey("sources"))
        ranges = try c.decode([Range].self, forKey: DynamicKey("ranges"))
        grade = try c.decodeIfPresent(String.self, forKey: DynamicKey("grade"))
        overlays = try c.decodeIfPresent([Overlay].self, forKey: DynamicKey("overlays"))
        subtitles = try c.decodeIfPresent(String.self, forKey: DynamicKey("subtitles"))
        subtitle_style = try c.decodeIfPresent(SubtitleStyle.self, forKey: DynamicKey("subtitle_style"))
        total_duration_s = try c.decodeIfPresent(Double.self, forKey: DynamicKey("total_duration_s"))
        extra = try decodeExtra(c, known: Self.known)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: DynamicKey.self)
        try c.encode(version, forKey: DynamicKey("version"))
        try c.encode(sources, forKey: DynamicKey("sources"))
        try c.encode(ranges, forKey: DynamicKey("ranges"))
        try c.encodeIfPresent(grade, forKey: DynamicKey("grade"))
        try c.encodeIfPresent(overlays, forKey: DynamicKey("overlays"))
        try c.encodeIfPresent(subtitles, forKey: DynamicKey("subtitles"))
        try c.encodeIfPresent(subtitle_style, forKey: DynamicKey("subtitle_style"))
        try c.encodeIfPresent(total_duration_s, forKey: DynamicKey("total_duration_s"))
        try encodeExtra(extra, into: &c)
    }
}

/// Read every key not in `known` into an unknown-key map.
private func decodeExtra(_ c: KeyedDecodingContainer<DynamicKey>, known: Set<String>) throws -> [String: JSONValue] {
    var extra: [String: JSONValue] = [:]
    for key in c.allKeys where !known.contains(key.stringValue) {
        extra[key.stringValue] = try c.decode(JSONValue.self, forKey: key)
    }
    return extra
}

/// Write the unknown-key map back out alongside the modeled fields.
private func encodeExtra(_ extra: [String: JSONValue], into c: inout KeyedEncodingContainer<DynamicKey>) throws {
    for (k, v) in extra { try c.encode(v, forKey: DynamicKey(k)) }
}

/// ElevenLabs Scribe word entry (type: "word" | "spacing" | "audio_event").
struct Word: Codable, Equatable {
    var text: String
    var start: Double
    var end: Double
    var speaker_id: String?
    var type: String?
}

struct Transcript: Codable {
    var words: [Word]
}

extension Edl {
    /// Recompute total_duration_s from the ranges (rounded to 0.01s), like withTotal() in project.ts.
    func withRecomputedTotal() -> Edl {
        var copy = self
        let total = ranges.reduce(0.0) { $0 + $1.duration }
        copy.total_duration_s = (total * 100).rounded() / 100
        return copy
    }

    func encoded() -> Data {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return (try? enc.encode(self)) ?? Data()
    }
}
