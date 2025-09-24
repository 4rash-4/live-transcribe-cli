// swift-tools-version:5.8
import PackageDescription

let package = Package(
  name: "LiveTranscribeCLI",
  platforms: [.macOS(.v13)],
  products: [
    .executable(name: "live-transcribe", targets: ["LiveTranscribeCLI"])
  ],
  dependencies: [
    .package(url: "https://github.com/FluidInference/FluidAudio.git", from: "0.5.2")
  ],
  targets: [
    .executableTarget(
      name: "LiveTranscribeCLI",
      dependencies: [
        .product(name: "FluidAudio", package: "FluidAudio")
      ],
      path: "Sources/LiveTranscribeCLI"
    )
  ]
)