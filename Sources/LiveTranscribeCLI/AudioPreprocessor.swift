//
//  AudioPreprocessor.swift
//  LiveTranscribeCLI
//
//  macOS-safe, entitlement-free, offline render via vDSP.
//

import AVFoundation
import Accelerate

final class AudioPreprocessor {

    struct Config {
        var targetRate      = 16_000
        var highPassHz      = 80.0
        var presenceHz      = 3_000.0
        var presenceGain    = 1.0
        var compRatio       = 4.0
        var limiterCeiling  = -1.0
    }

    private let config: Config
    private let converter: AVAudioConverter

    init(config: Config = .init(), inputFormat: AVAudioFormat) {
        self.config = config
        let targetFormat = AVAudioFormat(standardFormatWithSampleRate: Double(config.targetRate),
                                         channels: 1)!
        self.converter = AVAudioConverter(from: inputFormat, to: targetFormat)!
    }

    func process(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        let resampled = resample(buffer)
        let processed = applyDynamics(resampled)
        return processed
    }

    // MARK: - Private

    private func resample(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        let out = AVAudioPCMBuffer(pcmFormat: converter.outputFormat,
                                   frameCapacity: buffer.frameLength)!
        var error: NSError?
        converter.convert(to: out, error: &error) { _, count in
            count.pointee = .haveData
            return buffer
        }
        return out
    }

    /// Offline dynamics via vDSP (no AVAudioOfflineRenderNode).
    private func applyDynamics(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        guard let data = buffer.floatChannelData?[0] else { return buffer }
        let n = Int(buffer.frameLength)

        // 1. High-pass (biquad)
        var hp = biquadCreate(type: .highPass, freq: config.highPassHz, sampleRate: Double(config.targetRate))
        biquadProcess(&hp, data, n)

        // 2. Presence peak
        var peak = biquadCreate(type: .peaking, freq: config.presenceHz, q: 0.7, gain: config.presenceGain, sampleRate: Double(config.targetRate))
        biquadProcess(&peak, data, n)

        // 3. Soft compressor (simple RMS)
        let thresh: Float = -30.0
        let slope: Float = 1.0 / Float(config.compRatio)
        var rms: Float = 0
        vDSP_rmsqv(data, 1, &rms, vDSP_Length(n))
        let gain = (rms > thresh) ? pow(10, (thresh - rms) * (slope - 1) / 20) : 1.0
        vDSP_vsmul(data, 1, [gain], data, 1, vDSP_Length(n))

        // 4. Hard limiter
        var ceil = Float(pow(10, config.limiterCeiling / 20))
        var limits: [Float] = [-ceil, ceil]
        vDSP_vclip(data, 1, &limits[0], &limits[1], data, 1, vDSP_Length(n))

        return buffer
    }

    // MARK: - Biquad helpers

    private enum BiquadType { case highPass, peaking }
    private struct Biquad {
        var a0, a1, a2, b1, b2: Float
        var z1: Float = 0, z2: Float = 0
    }

    private func biquadCreate(type: BiquadType, freq: Double, q: Double = 0.707, gain: Double = 0, sampleRate: Double) -> Biquad {
        let w = 2 * .pi * freq / sampleRate
        let cosw = cos(w)
        let sinw = sin(w)
        let alpha = sinw / (2 * q)
        switch type {
        case .highPass:
            let b0 = (1 + cosw) / 2
            let b1 = -(1 + cosw)
            let b2 = (1 + cosw) / 2
            let a0 = 1 + alpha
            let a1 = -2 * cosw
            let a2 = 1 - alpha
            return Biquad(a0: Float(a0), a1: Float(a1), a2: Float(a2),
                          b1: Float(b1 / a0), b2: Float(b2 / a0))
        case .peaking:
            let A = pow(10, gain / 40)
            let b0 = 1 + alpha * A
            let b1 = -2 * cosw
            let b2 = 1 - alpha * A
            let a0 = 1 + alpha / A
            let a1 = -2 * cosw
            let a2 = 1 - alpha / A
            return Biquad(a0: Float(a0), a1: Float(a1), a2: Float(a2),
                          b1: Float(b1 / a0), b2: Float(b2 / a0))
        }
    }

    private func biquadProcess(_ b: inout Biquad, _ x: UnsafeMutablePointer<Float>, _ n: Int) {
        for i in 0..<n {
            let xi = x[i]
            let yi = (xi + b.z1 * b.b1 + b.z2 * b.b2) / b.a0
            b.z2 = b.z1
            b.z1 = xi - yi * b.a1 - b.z2 * b.a2
            x[i] = yi
        }
    }
}