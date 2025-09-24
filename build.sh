#!/bin/bash

set -e

echo "🔨 Building Live Transcribe CLI..."
echo "================================"

# Build the project
cd "$(dirname "$0")"

echo "📦 Building Swift package..."
swift build -c release

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo ""
    echo "🚀 Run with:"
    echo "   cd $(pwd)"
    echo "   ./.build/release/live-transcribe"
    echo ""
    echo "📝 Note: First run will ask for microphone permission"
    echo "📝 Note: First run will download ASR models (~850MB)"
    echo ""
    echo "⚠️  Important: This CLI needs to be code-signed for microphone access"
    echo "   Run the signing script:"
    echo "   ./sign.sh"
else
    echo ""
    echo "❌ Build failed!"
    exit 1
fi