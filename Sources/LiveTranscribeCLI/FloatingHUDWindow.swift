//
//  FloatingHUDWindow.swift
//  LiveTranscribeCLI
//
//  Brick 1+3+4: Integrated floating window with progressive scroll and drift band.
//  Now includes real transcript display and drift indication.
//

import SwiftUI
import Combine
import FluidAudio

/// 360 × 64 pt floating window that stays above all apps.
/// Ultra-thin material with progressive text scroll and drift indication.
struct FloatingHUDWindow: View {

    // MARK: - Dimensions (fixed, small, non-intrusive)
    private let width:  CGFloat = 360
    private let height: CGFloat = 64

    // MARK: - Components
    @ObservedObject var scrollViewModel: ProgressiveScrollViewModel
    @ObservedObject var driftViewModel: DriftPixelBandViewModel

    init(scrollViewModel: ProgressiveScrollViewModel, driftViewModel: DriftPixelBandViewModel) {
        self.scrollViewModel = scrollViewModel
        self.driftViewModel = driftViewModel
    }

    var body: some View {
        ZStack(alignment: .leading) {
            // Ultra-thin material → Liquid Glass aesthetic
            Rectangle()
                .fill(Color.clear)
                .background(.ultraThinMaterial)
                .cornerRadius(8)

            // Left-edge drift pixel band (dynamic color)
            DriftPixelBandView(viewModel: driftViewModel)

            // Progressive scroll text area
            ProgressiveScrollContentView(viewModel: scrollViewModel)
                .padding(.leading, 8) // space for pixel band
                .padding(.trailing, 8)
                .padding(.vertical, 4)
        }
        .frame(width: width, height: height)
        .shadow(color: Color.black.opacity(0.1), radius: 4, x: 0, y: 2)
    }
}

/// Window controller that keeps the HUD floating above all apps.
final class FloatingHUDController {

    private var window: NSWindow!
    private var scrollViewModel: ProgressiveScrollViewModel
    private var driftViewModel: DriftPixelBandViewModel
    private var hostingController: NSHostingController<FloatingHUDWindow>

    init() {
        scrollViewModel = ProgressiveScrollViewModel()
        driftViewModel = DriftPixelBandViewModel()

        let hudView = FloatingHUDWindow(
            scrollViewModel: scrollViewModel,
            driftViewModel: driftViewModel
        )
        hostingController = NSHostingController(rootView: hudView)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 64),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )

        window.contentViewController = hostingController
        window.isReleasedWhenClosed = false
        window.level = .floating              // stays above all apps
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.backgroundColor = .clear
        window.isOpaque = false
        window.hasShadow = true
        window.center()                       // start centered

        // Make it ignore mouse clicks (pass-through) so it never steals focus
        window.ignoresMouseEvents = true
    }

    func show() {
        window.orderFrontRegardless()
    }

    func hide() {
        window.orderOut(nil)
    }

    func close() {
        window.close()
    }

    // MARK: - Data Feeding Methods

    /// Feed new transcript line to progressive scroll
    func appendTranscript(_ text: String, confidence: Double, silenceRatio: Double, speakingRate: Double) {
        let line = TextLine(
            text: text,
            tokenMs: [], // will be filled from real data
            confidence: confidence,
            speakingRate: speakingRate,
            silenceRatio: silenceRatio,
            timestamp: Date()
        )

        // Update both components (ViewModels handle main thread dispatch)
        self.scrollViewModel.append(line: line)
        self.driftViewModel.update(silenceRatio: silenceRatio)
    }

    /// Update drift pixel band
    func updateDrift(silenceRatio: Double) {
        self.driftViewModel.update(silenceRatio: silenceRatio)
    }
}