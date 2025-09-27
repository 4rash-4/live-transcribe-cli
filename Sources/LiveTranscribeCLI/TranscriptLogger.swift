//
//  TranscriptLogger.swift
//  LiveTranscribeCLI
//
//  Brick 2: append-only, millisecond-timestamped, searchable log.
//  Zero UI, zero AI, just immortal storage + FluidAudio timing.
//

import Foundation
import os.log

/// One log entry = one utterance or one 30-s chunk.
/// Includes FluidAudio native timings for local drift calculation.
struct LogEntry: Codable {
    let ts: Date                      // millisecond timestamp
    let driftHue: Double              // 0-1, cached for quick visual lookup
    let transcript: String            // verbatim text
    let tokenMs: [Double]             // FluidAudio: ms between tokens
    let pauseMs: Double               // FluidAudio: ms silence before this chunk
    let vadConfidence: Double         // FluidAudio: 0-1 voice probability
    let wordCount: Int                // quick local metric
}

/// Append-only, immortal, time-searchable store.
final class TranscriptLogger {

    static let shared: TranscriptLogger = {
        do {
            return try TranscriptLogger()
        } catch {
            fatalError("Failed to initialize TranscriptLogger: \(error)")
        }
    }()

    private let fileHandle: FileHandle
    private let fileURL: URL
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let queue = DispatchQueue(label: "logger", qos: .utility)

    /// Opens (or creates) ~/ADHD-HUD/YYYY-MM-DD.log.ndjson
    init() throws {
        let dir = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("ADHD-HUD", isDirectory: true)

        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let date = ISO8601DateFormatter().string(from: Date()).prefix(10)
        fileURL = dir.appendingPathComponent("\(date).log.ndjson")

        if !FileManager.default.fileExists(atPath: fileURL.path) {
            FileManager.default.createFile(atPath: fileURL.path, contents: nil)
        }

        fileHandle = try FileHandle(forWritingTo: fileURL)
        fileHandle.seekToEndOfFile()

        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
    }

    /// Append one entry (thread-safe, millisecond-timestamped).
    func append(entry: LogEntry) {
        queue.async { [weak self] in
            guard let self = self else { return }
            do {
                let data = try self.encoder.encode(entry)
                self.fileHandle.write(data)
                self.fileHandle.write("\n".data(using: .utf8)!)
            } catch {
                os_log("Logger append failed: %{public}@", log: .default, type: .error, error.localizedDescription)
            }
        }
    }

    /// Search by time range (inclusive, millisecond precision).
    func search(from: Date, to: Date) -> [LogEntry] {
        queue.sync {
            do {
                let raw = try Data(contentsOf: fileURL)
                let lines = raw.split(separator: 0x0A) // newline
                return lines.compactMap { line -> LogEntry? in
                    guard let entry = try? decoder.decode(LogEntry.self, from: Data(line)) else { return nil }
                    return (entry.ts >= from && entry.ts <= to) ? entry : nil
                }
            } catch {
                os_log("Logger search failed: %{public}@", log: .default, type: .error, error.localizedDescription)
                return []
            }
        }
    }

    /// Latest entry (for quick UI lookup).
    func latest() -> LogEntry? {
        queue.sync {
            do {
                let raw = try Data(contentsOf: fileURL)
                guard let lastLine = raw.split(separator: 0x0A).last else { return nil }
                return try? decoder.decode(LogEntry.self, from: Data(lastLine))
            } catch {
                return nil
            }
        }
    }

    /// Append crystallized thought (z.ai integration).
    func appendCrystallized(_ crystal: String) {
        let entry = LogEntry(
            ts: Date(),
            driftHue: 0.58, // neutral
            transcript: crystal,
            tokenMs: [],
            pauseMs: 0,
            vadConfidence: 1.0, // crystallized = high confidence
            wordCount: crystal.split(separator: " ").count
        )
        append(entry: entry)
    }

    deinit {
        try? fileHandle.close()
    }
}