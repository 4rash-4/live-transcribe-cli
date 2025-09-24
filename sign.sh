#!/bin/bash

set -e

echo "🔐 Code-signing Live Transcribe CLI for microphone access..."
echo "========================================================"

cd "$(dirname "$0")"

# Check if the binary exists
if [ ! -f ".build/release/live-transcribe" ]; then
    echo "❌ Binary not found. Run ./build.sh first!"
    exit 1
fi

# Create entitlements file
cat > entitlements.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.device.audio-input</key>
    <true/>
    <key>com.apple.security.device.microphone</key>
    <true/>
</dict>
</plist>
EOF

echo "📝 Created entitlements.plist"

# Sign the binary
if command -v codesign &> /dev/null; then
    echo "🔐 Signing binary with hardened runtime..."
    codesign --force --deep --sign - --options runtime --entitlements entitlements.plist .build/release/live-transcribe

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Code-signing successful!"
        echo ""
        echo "🎤 You can now run:"
        echo "   ./.build/release/live-transcribe"
        echo ""
        echo "📝 First run will request microphone permission"
    else
        echo ""
        echo "❌ Code-signing failed!"
        echo "ℹ️  You may need to enable Developer Mode in System Settings"
        exit 1
    fi
else
    echo ""
    echo "⚠️  codesign not found. You'll need to sign manually:"
    echo "   1. Enable Developer Mode in System Settings"
    echo "   2. Run: codesign --force --deep --sign - --options runtime --entitlements entitlements.plist .build/release/live-transcribe"
fi