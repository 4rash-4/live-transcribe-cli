//
//  DriftPixelBand.swift
//  LiveTranscribeCLI
//
//  Brick 4: single-pixel drift band using REAL FluidAudio APIs.
//  silenceRatio → derived from vadResult.probability
//  z.ai gate off by default.
//

import SwiftUI
import Combine
import AppKit
import FluidAudio   // to access real VadResult
import os.log

/// Observable model for drift pixel band state
class DriftPixelBandViewModel: ObservableObject {
    @Published var driftHue: Double = 0.58          // teal calm
    @Published var silenceRatio: Double = 0.0       // derived
    @AppStorage("useZai") var useZai = false    // off by default

    private var timer: Timer?

    init() {
        // Update pixel color every second
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            self.updatePixelFromLocalMetrics()
        }
    }

    deinit {
        timer?.invalidate()
    }

    /// Update pixel from DERIVED metrics (no AI, reversible).
    private func updatePixelFromLocalMetrics() {
        let hue = 0.58 - (silenceRatio * 0.25)        // silence → rose
        driftHue = max(0.0, min(1.0, hue))
    }

    /// Feed DERIVED metrics (called from outside).
    func update(silenceRatio: Double) {
        print("🎨 DriftPixelBandViewModel: Updating silenceRatio: \(silenceRatio)")
        DispatchQueue.main.async {
            self.silenceRatio = silenceRatio
            print("🎨 DriftPixelBandViewModel: Updated to ratio: \(self.silenceRatio), hue will be: \(0.58 - (silenceRatio * 0.25))")
        }
    }

    /// Optional z.ai crystallisation (gate off by default).
    func maybeCrystallize(transcript: String, silenceRatio: Double, speakingRate: Double) async {
        guard useZai else { return }
        do {
            let crystal = try await ZaiClient.shared.crystallize(
                transcript: transcript,
                silenceRatio: silenceRatio,
                speakingRate: speakingRate
            )
            // append to existing TranscriptLogger
            TranscriptLogger.shared.appendCrystallized(crystal)
        } catch {
            os_log("z.ai crystallise failed: %{public}@", log: .default, type: .error, error.localizedDescription)
        }
    }
}

/// SwiftUI view for drift pixel band
struct DriftPixelBandView: View {
    @ObservedObject var viewModel: DriftPixelBandViewModel

    private let pixelWidth: CGFloat = 1

    var body: some View {
        Rectangle()
            .fill(Color(hue: viewModel.driftHue, saturation: 0.3, brightness: 0.8, opacity: 0.9))
            .frame(width: pixelWidth)
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}