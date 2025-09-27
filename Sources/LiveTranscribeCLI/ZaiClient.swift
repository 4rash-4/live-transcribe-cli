//
//  ZaiClient.swift
//  LiveTranscribeCLI
//
//  z.ai client for optional crystallisation. Off by default.
//  Stubbed endpoint - you fill API key later.
//

import Foundation

final class ZaiClient {
    static let shared = ZaiClient(apiKey: "") // you fill via env or settings

    private let apiKey: String
    private let baseURL = "https://api.z.ai/v1"

    init(apiKey: String) {
        self.apiKey = apiKey
    }

    /// Crystallise last 30 s of transcript + local metrics.
    func crystallize(transcript: String, silenceRatio: Double, speakingRate: Double) async throws -> String {
        guard !apiKey.isEmpty else { throw ZaiError.noApiKey }

        let prompt = """
        Compress the following self-talk into 1 concise, neutral sentence.
        Keep the user's tone, add no advice, use their words.
        Transcript: \(transcript)
        Metrics: silence ratio \(silenceRatio), speaking rate \(speakingRate)
        """

        let body: [String: Any] = [
            "model": "glm-4-air",
            "messages": [["role": "user", "content": prompt]],
            "temperature": 0.3,
            "max_tokens": 30
        ]

        var request = URLRequest(url: URL(string: "\(baseURL)/chat/completions")!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await URLSession.shared.data(for: request)
        // Stub return for now - you can wire real response later
        return "Crystallised: \(transcript.prefix(40))..."
    }

    enum ZaiError: Error { case noApiKey }
}