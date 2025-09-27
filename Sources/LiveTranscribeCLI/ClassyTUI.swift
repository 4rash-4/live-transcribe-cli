//
//  ClassyTUI.swift
//  LiveTranscribeCLI
//
//  Calm, classy Unicode UI: box-drawing + braille + math symbols.
//  60 fps waterfall, 1 Hz status bar, zero emoji pollution.
//

import Foundation
import AVFoundation
import Accelerate

/// Terminal size (updated on SIGWINCH).
private struct TermSize {
    static var width  = 80
    static var height = 24
    static func update() {
        var w = winsize()
        if ioctl(STDOUT_FILENO, TIOCGWINSZ, &w) == 0 {
            width  = Int(w.ws_col)
            height = Int(w.ws_row)
        }
    }
}

/// Muted pastel palette (dark first).
private struct Palette {
    static let bg          = "\u{1B}[48;2;20;20;20m"
    static let grid        = "\u{1B}[38;2;90;90;90m"
    static let spectroWhisper = "\u{1B}[38;2;120;180;120m"   // soft green
    static let spectroNormal = "\u{1B}[38;2;180;180;180m"   // soft grey
    static let statusLabel = "\u{1B}[38;2;140;140;140m"
    static let statusValue = "\u{1B}[38;2;200;200;200m"
    static let reset       = "\u{1B}[0m"
}

/// Classy Unicode renderer.
final class ClassyTUI {

    private let waterfallRows: Int
    private let spectroCols: Int
    private let statusRow: Int

    private var waterfall = [String]()  // row cache
    private var lastLevel: Float = -60
    private var lastVoice: String = "∅"
    private var lastMem: String = ""
    private var lastTemp: String = ""

    private var transcriptLines: [String] = []
    private var transcriptCol: Int = 0

    init() {
        TermSize.update()
        waterfallRows = max(12, TermSize.height - 6)   // leave 4 for meter + status
        spectroCols   = TermSize.width
        statusRow     = TermSize.height
        waterfall     = Array(repeating: "", count: waterfallRows)
        clearScreen()
        // drawFrame() - DISABLED (was drawing waterfall grid)
    }

    // MARK: - Public API

    /// Call from microphone tap @ 60 fps.
    func render(spectro: [Float], level: Float, voice: String, memMB: Double, tempC: Double) {
        // 1. Waterfall (DISABLED - was causing memory corruption)
        // let newRow = spectroRow(spectro)
        // waterfall.removeFirst()
        // waterfall.append(newRow)
        // drawWaterfall()

        // 2. Level meter (every frame) - KEEP THIS
        drawLevelMeter(level: level)

        // 3. Status bar (1 Hz) - KEEP THIS
        if shouldUpdateStatus(level: level, voice: voice, mem: memMB, temp: tempC) {
            drawStatusBar(level: level, voice: voice, mem: memMB, temp: tempC)
        }

        // 4. Transcription ticker (call separately)
        // drawTranscription(text)  → added later
    }

    /// Append new sentence (call from ASR callback).
    func appendTranscription(_ text: String) {
        transcriptLines.append("⟨ \(text) ⟩")
        if transcriptLines.count > 3 { transcriptLines.removeFirst() }
        drawTranscription()
    }

    private func drawTranscription() {
        let baseRow = statusRow - 4
        for (idx, line) in transcriptLines.enumerated() {
            let row = baseRow + idx
            let visible = String(line.suffix(spectroCols - 4))
            print("\u{1B}[\(row);2H" + Palette.statusValue + visible + Palette.reset, terminator: "")
        }
        fflush(stdout)
    }

    // MARK: - Private draw helpers

    private func clearScreen() {
        print("\u{1B}[2J\u{1B}[H", terminator: "")
        fflush(stdout)
    }

    private func drawFrame() {
        let top = "┌─⟨ Spectrogram ⟩" + String(repeating: "─", count: TermSize.width - 17) + "┐"
        let bot = "└─⟨ Status ⟩" + String(repeating: "─", count: TermSize.width - 14) + "┘"
        print(Palette.grid + top + Palette.reset)
        for _ in 0..<waterfallRows { print(Palette.grid + "│" + String(repeating: " ", count: TermSize.width - 2) + "│" + Palette.reset) }
        print(Palette.grid + bot + Palette.reset)
    }

    private func spectroRow(_ bins: [Float]) -> String {
        let cols = min(bins.count, spectroCols - 4)
        var row = ""
        for i in 0..<cols {
            let mag = bins[i]
            let char = spectroChar(mag: mag)
            let colour = mag > -40 ? Palette.spectroWhisper : Palette.spectroNormal
            row += colour + char
        }
        return row + Palette.reset
    }

    private func spectroChar(mag: Float) -> String {
        let norm = max(0, min(1, (mag + 60) / 60))   // -60 … 0  → 0 … 1
        let idx = Int(norm * 7)                       // 8 levels
        return ["▁","▂","▃","▄","▅","▆","▇","█"][idx]
    }

    private func drawWaterfall() {
        for (offset, row) in waterfall.enumerated() {
            print("\u{1B}[\(offset + 2);2H" + row, terminator: "")
        }
        fflush(stdout)
    }

    private func drawLevelMeter(level: Float) {
        let row = waterfallRows + 2
        let width = TermSize.width - 4
        let filled = Int((level + 60) / 60 * Float(width))
        let bar = String(repeating: "█", count: max(0, filled)) + String(repeating: "░", count: max(0, width - filled))
        let text = " ∿  \(Int(level)) dB "
        print("\u{1B}[\(row);2H" + Palette.statusValue + bar + Palette.reset + "\u{1B}[\(row);\(width - text.count + 2)H" + Palette.statusLabel + text + Palette.reset, terminator: "")
        fflush(stdout)
    }

    private func shouldUpdateStatus(level: Float, voice: String, mem: Double, temp: Double) -> Bool {
        let memStr = String(format: "%.1f", mem)
        let tempStr = String(format: "%.0f", temp)
        let changed = level != lastLevel || voice != lastVoice || memStr != lastMem || tempStr != lastTemp
        if changed {
            lastLevel = level; lastVoice = voice; lastMem = memStr; lastTemp = tempStr
        }
        return changed
    }

    private func drawStatusBar(level: Float, voice: String, mem: Double, temp: Double) {
        let row = statusRow
        let memStr = String(format: "%.1f", mem)
        let tempStr = String(format: "%.0f", temp)
        let levelStr = String(format: "%.0f", level)
        let line = "│  ∿ 16 kHz │ ⟨ \(voice) ⟩ │ \(levelStr) dB │ \(memStr) GB │ \(tempStr) °C │"
        print("\u{1B}[\(row);1H" + Palette.grid + line + Palette.reset, terminator: "")
        fflush(stdout)
    }
}

/// Swift-friendly ioctl
private func ioctl(_ fd: Int32, _ request: UInt, _ arg: UnsafeMutableRawPointer) -> Int32 {
    return Darwin.ioctl(fd, request, arg)
}
private let STDOUT_FILENO = Int32(1)
private let TIOCGWINSZ    = UInt(0x40087468)   // macOS value for TIOCGWINSZ