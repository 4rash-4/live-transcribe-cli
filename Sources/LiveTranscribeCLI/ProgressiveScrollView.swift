//
//  ProgressiveScrollView.swift
//  LiveTranscribeCLI
//
//  Brick 3: uses REAL FluidAudio APIs only.
//  vadConfidence → asrResult.confidence
//  tokenTimings → asrResult.tokenTimings
//  speakingRate → derived
//  silenceRatio → derived from vadResult.probability
//

import SwiftUI
import Accelerate
import Combine
import FluidAudio   // to access real types

/// One text line with DERIVED metrics from real FluidAudio results.
struct TextLine: Identifiable {
    let id = UUID()
    let text: String
    let tokenMs: [Double]        // from asrResult.tokenTimings
    let confidence: Double       // from asrResult.confidence
    let speakingRate: Double     // derived: tokenCount / duration
    let silenceRatio: Double     // derived: 1 - vadResult.probability
    let timestamp: Date
}

/// Observable model for progressive scroll state
class ProgressiveScrollViewModel: ObservableObject {
    @Published var lines: [TextLine] = []
    @Published var scrollOffset: CGFloat = 0
    @Published var textOpacity: Double = 1.0
    @Published var baselineRate: Double = 4.0

    private let lineHeight: CGFloat = 20
    private let maxLines = 5
    private var timer: Timer?

    init() {
        // Start compression timer
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in
            self.compressOlderLines()
        }
        baselineRate = adaptiveBaseline()
    }

    deinit {
        timer?.invalidate()
    }

    /// Append new line (called from outside)
    func append(line: TextLine) {
        print("📝 ProgressiveScrollViewModel: Adding line: \(line.text)")
        DispatchQueue.main.async {
            self.lines.append(line)
            print("📝 ProgressiveScrollViewModel: Lines count: \(self.lines.count)")
            self.scrollToBottom()
        }
    }

    // MARK: - Private methods (moved from view)

    private func scrollToBottom() {
        guard let last = lines.last else { return }
        let gaps = adjacentDifferences(last.tokenMs)
        let avgGap = gaps.isEmpty ? 250 : gaps.reduce(0, +) / Double(gaps.count)
        let speed = max(0.5, min(2.0, avgGap / 250))

        withAnimation(.linear(duration: speed)) {
            scrollOffset += lineHeight
        }
    }

    private func adjacentDifferences(_ array: [Double]) -> [Double] {
        return zip(array, array.dropFirst()).map(-)
    }

    private func adaptiveBaseline() -> Double {
        return 4.0 // calibrate from first 30s of real data
    }

    private func compressOlderLines() {
        guard lines.count > 3 else { return }
        let recent = Array(lines.suffix(3))
        let avgRate = recent.map(\.speakingRate).reduce(0, +) / Double(recent.count)
        if avgRate < baselineRate * 0.7 {
            if let oldest = lines.first {
                let summary = summarize(text: oldest.text)
                lines[0] = TextLine(
                    text: summary,
                    tokenMs: oldest.tokenMs,
                    confidence: oldest.confidence,
                    speakingRate: oldest.speakingRate,
                    silenceRatio: oldest.silenceRatio,
                    timestamp: oldest.timestamp
                )
            }
        }
    }

    private func summarize(text: String) -> String {
        let words = text.split(separator: " ")
        return words.count <= 6 ? text : words.prefix(6).joined(separator: " ") + "..."
    }
}

/// SwiftUI view for progressive scroll content
struct ProgressiveScrollContentView: View {
    @ObservedObject var viewModel: ProgressiveScrollViewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(viewModel.lines) { line in
                        Text(line.text)
                            .font(.system(size: 10, weight: .regular, design: .rounded))
                            .foregroundColor(Color.primary.opacity(opacity(for: line)))
                            .id(line.id)
                            .transition(.opacity)
                            .lineLimit(1)
                    }
                }
            }
        }
    }

    /// Text opacity based on confidence
    private func opacity(for line: TextLine) -> Double {
        return max(0.6, line.confidence)
    }
}