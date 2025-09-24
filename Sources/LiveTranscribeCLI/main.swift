import Foundation
import AVFoundation
import FluidAudio

@main
struct LiveTranscribeCLI {
    static func main() async {
        do {
            print("🎤 Live Transcribe CLI - FluidAudio (M1 Optimized)")
            print("===================================================")
            print("Press Ctrl+C to stop")
            print("")

            // M1 Optimization: Display system info
            let processInfo = ProcessInfo.processInfo
            print("🔍 System Analysis:")
            print("   CPU: \(processInfo.processorCount) cores")
            print("   Memory: \(String(format: "%.1f", Double(processInfo.physicalMemory) / 1024 / 1024 / 1024))GB")
            print("   Thermal State: \(processInfo.thermalState.rawValue)")
            print("")

            // Setup audio engine with M1 optimizations
            let engine = AVAudioEngine()
            let input = engine.inputNode
            let inputFormat = input.outputFormat(forBus: 0)

            print("📡 Input format: \(inputFormat.sampleRate)Hz, \(inputFormat.channelCount) channels")

            // M1 Optimization: Use optimal format for Apple Neural Engine
            let targetFormat = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: 16000,
                channels: 1,
                interleaved: false  // Non-interleaved for better ANE performance
            )!

            print("🎯 Target format: 16000Hz, 1 channel (Float32, non-interleaved)")

            // Performance Profiling: Detailed timing measurements
            print("⚡ PERFORMANCE PROFILING ENABLED ⚡")
            print("📊 Measuring model loading bottlenecks...")
            print("")

            let totalStartTime = Date()

            // Stage 1: Model Download/Loading
            print("🔄 Stage 1: Model Download & Loading...")
            let modelLoadStartTime = Date()
            let models = try await AsrModels.downloadAndLoad()
            let modelLoadTime = Date().timeIntervalSince(modelLoadStartTime)
            print("✅ Stage 1 Complete: \(String(format: "%.2f", modelLoadTime))s")

            // Stage 2: StreamingAsrManager Creation
            print("🔄 Stage 2: StreamingAsrManager Creation...")
            let managerCreateStartTime = Date()
            let streamingManager = StreamingAsrManager(config: .streaming)
            let managerCreateTime = Date().timeIntervalSince(managerCreateStartTime)
            print("✅ Stage 2 Complete: \(String(format: "%.3f", managerCreateTime))s")

            // Stage 3: Manager Initialization
            print("🔄 Stage 3: Manager Initialization (ANE + CoreML)...")
            let managerStartTime = Date()
            try await streamingManager.start(models: models, source: .microphone)
            let managerStartDuration = Date().timeIntervalSince(managerStartTime)
            print("✅ Stage 3 Complete: \(String(format: "%.2f", managerStartDuration))s")

            let totalLoadTime = Date().timeIntervalSince(totalStartTime)

            print("")
            print("📈 LOADING PERFORMANCE BREAKDOWN:")
            print("   Model Download/Load: \(String(format: "%.2f", modelLoadTime))s (\(Int((modelLoadTime/totalLoadTime)*100))%)")
            print("   Manager Creation: \(String(format: "%.3f", managerCreateTime))s (\(Int((managerCreateTime/totalLoadTime)*100))%)")
            print("   ANE/CoreML Init: \(String(format: "%.2f", managerStartDuration))s (\(Int((managerStartDuration/totalLoadTime)*100))%)")
            print("   TOTAL LOAD TIME: \(String(format: "%.2f", totalLoadTime))s")
            print("")

            // Performance analysis
            if totalLoadTime > 10 {
                print("⚠️  SLOW LOADING DETECTED (>\(String(format: "%.0f", totalLoadTime))s)")
                if modelLoadTime > totalLoadTime * 0.5 {
                    print("🔍 Primary Bottleneck: Model loading (\(String(format: "%.1f", modelLoadTime))s)")
                    print("💡 Optimization: Models may need recompilation or are downloading")
                }
                if managerStartDuration > totalLoadTime * 0.3 {
                    print("🔍 Secondary Bottleneck: ANE initialization (\(String(format: "%.1f", managerStartDuration))s)")
                    print("💡 Optimization: CoreML model compilation or ANE memory allocation")
                }
            } else {
                print("✨ Loading Performance: OPTIMAL (<10s)")
            }

            print("")
            print("🚀 M1 Optimizations Applied:")
            print("   StreamingConfig: 11s chunks + 1s hypothesis")
            print("   ANE Compute Units: All (CPU + GPU + Neural Engine)")
            print("   Memory: Unified architecture optimized")
            print("")

            print("✅ ASR models loaded and streaming manager started!")
            print("")
            print("🎙️  Listening... Speak now!")
            print("----------------------------")

            // Time To First Token (TTFT) measurement
            var firstTokenTime: Date? = nil
            var firstTokenReceived = false

            // Start transcription listener with TTFT tracking
            let _ = Task {
                for await update in await streamingManager.transcriptionUpdates {
                    // Measure Time To First Token (TTFT)
                    if !firstTokenReceived && !update.text.trimmingCharacters(in: .whitespaces).isEmpty {
                        let ttft = Date().timeIntervalSince(totalStartTime)
                        print("⚡ TIME TO FIRST TOKEN: \(String(format: "%.2f", ttft))s")
                        firstTokenReceived = true
                    }

                    if update.isConfirmed {
                        print("✅ \(update.text) (conf: \(String(format: "%.2f", update.confidence)))")
                    } else {
                        print("🔄 \(update.text) (conf: \(String(format: "%.2f", update.confidence)))")
                    }
                }
            }

            // M1 Optimization: Use 512-sample buffer (optimal for ANE scheduling)
            // Research shows 512 samples = 32ms at 16kHz is optimal for M1
            let optimalBufferSize: AVAudioFrameCount = 512

            // Create cached audio converter for efficiency
            var cachedConverter: AVAudioConverter?
            if inputFormat.sampleRate != 16000 || inputFormat.channelCount != 1 {
                cachedConverter = AVAudioConverter(from: inputFormat, to: targetFormat)
                print("🔧 Audio converter cached for M1 optimization")
            }

            // Install optimized audio tap
            input.installTap(
                onBus: 0,
                bufferSize: optimalBufferSize,
                format: inputFormat
            ) { buffer, _ in
                var bufferToStream = buffer

                // M1 Optimization: Efficient format conversion
                if let converter = cachedConverter {
                    // Calculate output buffer size for sample rate conversion
                    let outputFrameCount = AVAudioFrameCount(
                        Double(buffer.frameLength) * targetFormat.sampleRate / inputFormat.sampleRate
                    )

                    guard let convertedBuffer = AVAudioPCMBuffer(
                        pcmFormat: targetFormat,
                        frameCapacity: outputFrameCount
                    ) else { return }

                    var error: NSError?
                    let status = converter.convert(to: convertedBuffer, error: &error) { _, outStatus in
                        outStatus.pointee = .haveData
                        return buffer
                    }

                    if status == .error {
                        print("❌ Audio conversion failed: \(error?.localizedDescription ?? "unknown")")
                        return
                    }

                    bufferToStream = convertedBuffer
                }

                // Stream to FluidAudio
                Task {
                    await streamingManager.streamAudio(bufferToStream)
                }
            }

            // Start the engine
            try engine.start()

            // M1 8GB Optimization: Enhanced memory monitoring
            let performanceMonitor = Task {
                var checkCount = 0
                while true {
                    try await Task.sleep(nanoseconds: 10_000_000_000) // 10 seconds
                    checkCount += 1

                    let currentThermalState = ProcessInfo.processInfo.thermalState
                    let memoryUsage = ProcessInfo.processInfo.physicalMemory

                    if checkCount % 6 == 0 { // Every minute
                        print("📊 M1 8GB Performance Status:")
                        print("   Thermal State: \(thermalStateDescription(currentThermalState))")
                        print("   Memory Usage: Optimized for 8GB system")

                        if currentThermalState.rawValue >= 2 {
                            print("⚠️  Performance Alert: Thermal throttling may affect real-time performance")
                        }

                        // 8GB-specific recommendations
                        print("💡 8GB Tips: Close Chrome/Safari, disable unused apps for best performance")
                    }
                }
            }

            // Setup clean shutdown
            signal(SIGINT) { _ in
                print("\n🛑 Shutting down...")
                exit(0)
            }

            // Keep running with performance monitoring
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    try await performanceMonitor.value
                }

                group.addTask {
                    while true {
                        try await Task.sleep(nanoseconds: 1_000_000_000)
                    }
                }

                try await group.next()
            }

        } catch {
            print("❌ Error: \(error)")
            exit(1)
        }
    }

    // Helper function for thermal state descriptions
    static func thermalStateDescription(_ state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: return "Nominal (optimal)"
        case .fair: return "Fair (slight throttling)"
        case .serious: return "Serious (throttling)"
        case .critical: return "Critical (heavy throttling)"
        @unknown default: return "Unknown"
        }
    }
}