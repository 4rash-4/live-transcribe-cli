// MARK: - macOS-safe ANE flag parsing (no AVAudioSession)
private var useANEPreprocessing = false

private func parseANEFlag() {
    useANEPreprocessing = CommandLine.arguments.contains("--ane-first")
}

/// macOS: force built-in input via CoreAudio (public API)
//  We *do not* switch here—we simply rely on the user selecting
//  "MacBook Pro Microphone" in System Preferences or via `sudo ./live-transcribe --ane-first`
//  If AirPods are the *active* input we silently fall back to mono enhancement.