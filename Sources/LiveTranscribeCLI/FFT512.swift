//
//  FFT512.swift
//  LiveTranscribeCLI
//
//  Real 512-bin FFT → magnitude spectrum for waterfall.
//

import Accelerate
import AVFoundation

/// Returns 64 magnitude bins (0-8 kHz) from 512 samples @ 16 kHz.
func fft512(_ buffer: AVAudioPCMBuffer) -> [Float] {
    guard let data = buffer.floatChannelData?[0] else { return Array(repeating: -60, count: 64) }
    let n = 512
    var window = [Float](repeating: 0, count: n)
    vDSP_hamm_window(&window, vDSP_Length(n), 0)
    var windowed = [Float](repeating: 0, count: n)
    vDSP_vmul(data, 1, window, 1, &windowed, 1, vDSP_Length(n))

    var real = [Float](repeating: 0, count: n/2)
    var imag = [Float](repeating: 0, count: n/2)
    var split = DSPSplitComplex(realp: &real, imagp: &imag)
    windowed.withUnsafeBytes { ptr in
        let complex = ptr.bindMemory(to: DSPComplex.self)
        vDSP_ctoz(complex.baseAddress!, 2, &split, 1, vDSP_Length(n/2))
    }

    vDSP_fft_zrip(setupFFT, &split, 1, vDSP_Length(log2(Float(n))), FFTDirection(FFT_FORWARD))

    var mag = [Float](repeating: 0, count: n/2)
    vDSP_zvmags(&split, 1, &mag, 1, vDSP_Length(n/2))
    var db = [Float](repeating: 0, count: n/2)
    var one: Float = 1.0
    var zero: Float = 0.0
    vDSP_vdbcon(&mag, 1, &one, &db, 1, vDSP_Length(n/2), 0)   // power → dB

    // Down-sample 256 → 64 bins (0-8 kHz)
    var bins = [Float](repeating: -60, count: 64)
    for i in 0..<64 {
        let start = i * 4
        var avg: Float = 0
        vDSP_meanv(&db[start], 1, &avg, vDSP_Length(4))
        bins[i] = avg
    }
    return bins
}

// FFT setup (one-time)
private let setupFFT: FFTSetup = {
    let n = 512
    return vDSP_create_fftsetup(vDSP_Length(log2(Float(n))), FFTRadix(2))!
}()