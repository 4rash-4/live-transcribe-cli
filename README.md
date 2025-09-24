# Live Transcribe CLI

A simple command-line tool for real-time microphone transcription using FluidAudio on macOS.

## Features

- 🎤 **Live microphone input** - Real-time speech capture
- ⚡ **Low latency** - <200ms processing delay
- 🧠 **FluidAudio integration** - State-of-the-art ASR models
- 💾 **Local processing** - All audio processed on-device
- 🎯 **Streaming results** - Progressive transcription updates

## Requirements

- macOS 13.0+
- Xcode command-line tools
- Developer Mode enabled (for code-signing)

## Setup

### 1. Build the CLI

```bash
cd LiveTranscribeCLI
./build.sh
```

### 2. Code-sign for microphone access

```bash
./sign.sh
```

### 3. Run the transcriber

```bash
./.build/release/live-transcribe
```

## First Run

On first run, macOS will:
1. **Request microphone permission** - Click "Allow"
2. **Download ASR models** - ~850MB (one-time download)

## Usage

```bash
# Start live transcription
./.build/release/live-transcribe

# Output:
🎤 Live Transcribe CLI - FluidAudio
=====================================
Press Ctrl+C to stop

📡 Input format: 44100Hz, 2 channels
🎯 Target format: 16000Hz, 1 channel (Float32)
⬇️  Downloading ASR models (first time only)...
✅ ASR models loaded and ready!

🎙️  Listening... Speak now!
----------------------------
🔄 Hello world
✅ Hello world
```

## Controls

- **Ctrl+C** - Stop transcription and exit
- **Microphone permission** - Allow on first run
- **System volume** - Use system volume controls

## Technical Details

- **Audio capture**: AVAudioEngine with real-time buffer processing
- **Format conversion**: 44.1kHz stereo → 16kHz mono Float32
- **Chunk processing**: 1-second audio chunks
- **ASR models**: FluidAudio CoreML models (~850MB)
- **Streaming**: Real-time volatile and confirmed results

## Troubleshooting

### Permission Denied

If you see "Permission denied" errors:

1. Go to **System Settings** → **Privacy & Security** → **Microphone**
2. Enable "Live Transcribe CLI" or "Terminal"
3. Run the tool again

### Code-signing Issues

If signing fails:

1. Enable **Developer Mode** in System Settings → Privacy & Security
2. Install Xcode command-line tools: `xcode-select --install`
3. Run `./sign.sh` again

### Audio Issues

If no audio is detected:

1. Check microphone is connected and working
2. Test with system microphone: `say "test"`
3. Restart the tool

## Files

- `Package.swift` - Swift package configuration
- `Sources/LiveTranscribeCLI/main.swift` - Main application code
- `Sources/LiveTranscribeCLI/Info.plist` - Permissions and metadata
- `build.sh` - Build script
- `sign.sh` - Code-signing script
- `entitlements.plist` - Microphone access entitlements