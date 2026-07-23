#!/usr/bin/env swift

import AppKit
import Foundation
import Vision

func recognize(_ path: String) throws -> [String] {
    let url = URL(fileURLWithPath: path)
    guard
        let image = NSImage(contentsOf: url),
        let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else {
        throw NSError(
            domain: "research-video-ocr",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "cannot load image: \(path)"]
        )
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
    return request.results?.compactMap { $0.topCandidates(1).first?.string } ?? []
}

for path in CommandLine.arguments.dropFirst() {
    do {
        let text = try recognize(path)
            .map { $0.replacingOccurrences(of: "\t", with: " ") }
            .joined(separator: " ")
        print("\(path)\t\(text)")
    } catch {
        FileHandle.standardError.write(
            "OCR failed for \(path): \(error)\n".data(using: .utf8)!
        )
    }
}
